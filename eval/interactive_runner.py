"""Interactive CLI runner for manual SCADA Agent evaluation."""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from pydantic import ValidationError

from agent.config import ExperimentConfig, load_config
from agent.llm import LLMResponse
from agent.orchestrator import Agent, assemble, build_demo_world
from agent.tracer import ToolCallRecord
from eval._selector import select_from_list
from eval.schema import GoldenRecord, load_golden_dataset
from world import MockWorld, Page, Point, Widget, deep_copy_world

DEFAULT_CONFIG = Path("configs/D_minimal.yaml")
DEFAULT_DATASET = Path("eval/golden_dataset.jsonl")
DEFAULT_RESULTS_ROOT = Path("results/interactive")

ALIASES = {
	"g": "golden",
	"w": "world",
	"q": "query",
	"c": "config",
	"m": "llm",
	"d": "display",
	"i": "inspect",
	"t": "trace",
	"h": "help",
	"?": "help",
}


@dataclass
class RunnerSession:
	"""Mutable runtime state for the interactive runner."""

	config_path: Path | None = None
	config: ExperimentConfig | None = None
	provider_override: str | None = None
	model_override: str | None = None
	agent: Agent | None = None
	dataset_path: Path = DEFAULT_DATASET
	golden_records: list[GoldenRecord] = field(default_factory=list)
	current_golden: GoldenRecord | None = None
	world: MockWorld = field(default_factory=MockWorld)
	initial_world_snapshot: dict[str, Any] | None = None
	last_trace: dict[str, Any] | None = None
	last_query: str | None = None
	show_llm_output: bool = True
	show_llm_reasoning: bool = True
	show_world_realtime: bool = True
	show_trace: bool = True
	results_root: Path = DEFAULT_RESULTS_ROOT
	run_id: str = field(default_factory=lambda: f"interactive_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
	last_error: str | None = None

	@property
	def provider(self) -> str:
		if self.provider_override:
			return self.provider_override
		if self.config is not None:
			return self.config.model.provider
		return "mock"

	@property
	def model(self) -> str:
		if self.model_override:
			return self.model_override
		if self.config is not None:
			return self.config.model.name
		return "mock"


def _print(out: TextIO, text: str = "") -> None:
	print(text, file=out)


def _parse_bool(value: str) -> bool | None:
	value = value.strip().lower()
	if value in {"on", "yes", "y", "true", "1"}:
		return True
	if value in {"off", "no", "n", "false", "0"}:
		return False
	return None


def _split_command(line: str) -> list[str]:
	try:
		return shlex.split(line, posix=False)
	except ValueError:
		return line.split()


def _jsonable(data: Any) -> Any:
	return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def world_from_snapshot(snapshot: dict[str, Any] | None) -> MockWorld:
	return MockWorld.model_validate(snapshot or {})


def world_to_json(world: MockWorld) -> dict[str, Any]:
	return world.model_dump(mode="json")


def load_world_json(path: Path) -> MockWorld:
	with path.open("r", encoding="utf-8") as f:
		payload = json.load(f)
	return MockWorld.model_validate(payload)


def save_world_json(world: MockWorld, path: Path) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(
		json.dumps(world_to_json(world), indent=2, ensure_ascii=False) + "\n",
		encoding="utf-8",
	)


def world_summary(world: MockWorld) -> str:
	points_by_type = {"analog": 0, "digital": 0, "string": 0}
	for point in world.points.values():
		points_by_type[point.type] = points_by_type.get(point.type, 0) + 1
	widgets = sum(len(page.widgets) for page in world.pages.values())
	alarms_enabled = sum(1 for alarm in world.alarms.values() if alarm.enabled)
	histories_enabled = sum(1 for hist in world.histories.values() if hist.enabled)
	scripts_enabled = sum(1 for script in world.scripts.values() if script.enabled)
	deployment_status: dict[str, int] = {}
	for deployment in world.deployments.values():
		deployment_status[deployment.status] = deployment_status.get(deployment.status, 0) + 1
	deployment_bits = ", ".join(f"{k}={v}" for k, v in sorted(deployment_status.items())) or "none"
	return "\n".join(
		[
			"World summary:",
			f"  pages        : {len(world.pages)}",
			f"  widgets      : {widgets} total",
			f"  points       : {points_by_type.get('analog', 0)} analog / {points_by_type.get('digital', 0)} digital / {points_by_type.get('string', 0)} string",
			f"  alarms       : {alarms_enabled} enabled / {len(world.alarms) - alarms_enabled} disabled",
			f"  devices      : {len(world.devices)}",
			f"  histories    : {histories_enabled} enabled / {len(world.histories) - histories_enabled} disabled",
			f"  scripts      : {scripts_enabled} enabled / {len(world.scripts) - scripts_enabled} disabled",
			f"  deployments  : {deployment_bits}",
		]
	)


def compact_world_summary(world: MockWorld) -> str:
	widgets = sum(len(page.widgets) for page in world.pages.values())
	return (
		f"{len(world.points)} points, {len(world.pages)} pages, {widgets} widgets, "
		f"{len(world.alarms)} alarms, {len(world.histories)} histories, "
		f"{len(world.scripts)} scripts, {len(world.deployments)} deployments"
	)


def format_world_diff(diff: dict[str, Any], *, limit: int = 30) -> str:
	lines: list[str] = []
	for key, value in list(diff.get("added_or_modified", {}).items())[:limit]:
		lines.append(f"  +/~ {key} = {value!r}")
	removed = diff.get("removed", [])
	for key in removed[:limit]:
		lines.append(f"  - {key}")
	if len(diff.get("added_or_modified", {})) + len(removed) > limit:
		lines.append(f"  ... truncated to {limit} changes")
	return "\n".join(lines) if lines else "  (no world changes)"


def lookup_path(world: MockWorld, dot_path: str) -> Any:
	node: Any = world.model_dump(mode="json")
	if dot_path in {"", "world"}:
		return node
	for part in dot_path.split("."):
		if not isinstance(node, dict) or part not in node:
			return None
		node = node[part]
	return node


def format_golden_summary(record: GoldenRecord) -> str:
	expected_diff = record.expected_final_state_diff
	return "\n".join(
		[
			f"id                 : {record.id}",
			f"query              : {record.query}",
			f"domain             : {record.domain}",
			f"complexity         : {record.complexity}",
			f"expected_behavior  : {record.expected_behavior}",
			f"expected_workflow  : {record.expected_workflow_id}",
			f"initial_world      : {compact_world_summary(world_from_snapshot(record.initial_world))}",
			f"expected_diff_mode : {expected_diff.match_mode}",
			f"expected_add/mod   : {len(expected_diff.added_or_modified)} keys",
			f"expected_removed   : {len(expected_diff.removed)} keys",
		]
	)


def status_text(session: RunnerSession) -> str:
	config = str(session.config_path) if session.config_path else "not loaded"
	return "\n".join(
		[
			"SCADA Interactive Runner",
			"",
			f"Current config : {config}",
			f"Current model  : {session.provider}/{session.model}",
			f"Current world  : {compact_world_summary(session.world)}",
			f"Show LLM IO    : {'on' if session.show_llm_output else 'off'}",
			f"Show reasoning : {'on' if session.show_llm_reasoning else 'off'}",
			f"Show world     : {'on' if session.show_world_realtime else 'off'}",
			"",
			"Commands:",
			"  golden     Load a golden test case",
			"  world      Create/edit/reset initial world",
			"  query      Run a query against current world",
			"  config     Pick a config (interactive) or load one by index/path",
			"  llm        Choose provider/model",
			"  display    Toggle LLM thought/output/world display",
			"  inspect    Show current world",
			"  trace      Show last trace summary",
			"  save       Save current ad-hoc case as JSON",
			"  help       Show commands",
			"  exit       Quit",
		]
	)


class InteractiveEventSink:
	"""Display-only event sink for Agent.run."""

	def __init__(self, session: RunnerSession, out: TextIO) -> None:
		self.session = session
		self.out = out

	def on_run_start(self, query: str, world: MockWorld) -> None:
		if self.session.show_trace:
			_print(self.out, f"[run] {query}")
		if self.session.show_world_realtime:
			_print(self.out, compact_world_summary(world))

	def on_state_enter(self, state: str) -> None:
		if self.session.show_trace:
			_print(self.out, f"[state] {state}")

	def on_llm_response(self, turn: int, state: str, response: LLMResponse) -> None:
		if self.session.show_trace:
			tool_names = ", ".join(call.name for call in response.tool_calls) or "none"
			_print(self.out, f"[turn {turn}] state={state} stop={response.stop_reason} tools={tool_names}")
		if self.session.show_llm_output and response.text:
			_print(self.out, f"[assistant] {response.text}")
		if self.session.show_llm_reasoning and response.reasoning:
			_print(self.out, f"[reasoning] {response.reasoning}")

	def on_resource_read(self, turn: int, record: dict[str, Any]) -> None:
		if self.session.show_trace:
			status = "OK" if record.get("found") else "ERROR"
			_print(self.out, f"[resource turn={turn}] {record.get('uri')} {status} error={record.get('error')}")

	def on_tool_call(
		self,
		turn: int,
		record: ToolCallRecord,
		world_before: MockWorld,
		world_after: MockWorld,
	) -> None:
		if self.session.show_trace:
			_print(
				self.out,
				f"[tool turn={turn}] {record.selected} action={record.action} ok={record.result_ok} code={record.error_code}",
			)
		if self.session.show_world_realtime:
			diff = world_before.diff(world_after)
			_print(self.out, "World changed:")
			_print(self.out, format_world_diff(diff))
			_print(self.out, compact_world_summary(world_after))

	def on_run_finish(self, trace: dict[str, Any], world: MockWorld) -> None:
		if self.session.show_trace:
			_print(self.out, f"[finish] trace_id={trace['trace_id']} terminal={trace['execution']['terminal_state']}")
		if self.session.show_world_realtime:
			_print(self.out, world_summary(world))


def rebuild_agent(session: RunnerSession) -> tuple[bool, str]:
	if session.config_path is None:
		return False, "No config path is loaded."
	old_agent = session.agent
	old_config = session.config
	try:
		agent = assemble(
			session.config_path,
			model_override=session.model_override,
			provider_override=session.provider_override,
			results_root=session.results_root,
			run_id=session.run_id,
			dataset_version="interactive",
		)
	except NotImplementedError as exc:
		session.agent = old_agent
		session.config = old_config
		session.last_error = str(exc)
		return False, f"Unsupported provider: {exc}. Choose mock or xiaomi-mimo."
	except RuntimeError as exc:
		session.agent = old_agent
		session.config = old_config
		session.last_error = str(exc)
		return False, f"Could not build LLM: {exc}"
	except Exception as exc:
		session.agent = old_agent
		session.config = old_config
		session.last_error = str(exc)
		return False, f"Could not rebuild agent: {exc}"
	session.agent = agent
	session.config = agent.config
	session.last_error = None
	return True, f"Agent ready: {session.provider}/{session.model}"


def load_config_into_session(session: RunnerSession, path: Path) -> tuple[bool, str]:
	old_path = session.config_path
	old_config = session.config
	try:
		cfg = load_config(path)
	except Exception as exc:
		session.config_path = old_path
		session.config = old_config
		return False, f"Config load failed: {exc}"
	session.config_path = path
	session.config = cfg
	ok, msg = rebuild_agent(session)
	if not ok:
		session.config_path = old_path
		session.config = old_config
		return False, msg
	return True, msg


def load_dataset_into_session(session: RunnerSession) -> tuple[bool, str]:
	try:
		session.golden_records = load_golden_dataset(session.dataset_path)
	except Exception as exc:
		return False, f"Dataset load failed: {exc}"
	return True, f"Loaded {len(session.golden_records)} golden records from {session.dataset_path}"


def find_golden(session: RunnerSession, selector: str) -> GoldenRecord | None:
	if not session.golden_records:
		load_dataset_into_session(session)
	selector_lower = selector.lower()
	for record in session.golden_records:
		if record.id == selector:
			return record
	matches = [
		record
		for record in session.golden_records
		if selector_lower in record.domain.lower()
		or selector_lower in record.complexity.lower()
		or selector_lower in record.query.lower()
	]
	return matches[0] if len(matches) == 1 else None


def load_golden_case(session: RunnerSession, selector: str) -> tuple[bool, str]:
	if not selector:
		return False, "Usage: golden <id|domain|complexity|query-substring>"
	record = find_golden(session, selector)
	if record is None:
		return False, f"Golden case not found or ambiguous: {selector}"
	session.current_golden = record
	session.world = world_from_snapshot(record.initial_world)
	session.initial_world_snapshot = world_to_json(session.world)
	return True, format_golden_summary(record)


def add_point(session: RunnerSession, args: list[str]) -> tuple[bool, str]:
	if not args:
		return False, "Usage: world add point <tag> [analog|digital|string] [unit] [min] [max]"
	tag = args[0]
	point_type = args[1] if len(args) > 1 else "analog"
	unit = args[2] if len(args) > 2 and args[2] != "-" else None
	min_value = float(args[3]) if len(args) > 3 and args[3] != "-" else None
	max_value = float(args[4]) if len(args) > 4 and args[4] != "-" else None
	session.world.points[tag] = Point(tag=tag, type=point_type, unit=unit, min=min_value, max=max_value)
	return True, f"Added point {tag}"


def add_page(session: RunnerSession, args: list[str]) -> tuple[bool, str]:
	if not args:
		return False, "Usage: world add page <id> [name] [width] [height] [background]"
	page_id = args[0]
	name = args[1] if len(args) > 1 else page_id
	width = int(args[2]) if len(args) > 2 else 1920
	height = int(args[3]) if len(args) > 3 else 1080
	background = args[4] if len(args) > 4 else "#FFFFFF"
	session.world.pages[page_id] = Page(id=page_id, name=name, resolution=(width, height), background=background)
	return True, f"Added page {page_id}"


def add_widget(session: RunnerSession, args: list[str]) -> tuple[bool, str]:
	if len(args) < 3:
		return False, "Usage: world add widget <page_id> <widget_id> <type> [x] [y] [width] [height]"
	page_id, widget_id, widget_type = args[:3]
	if page_id not in session.world.pages:
		return False, f"Page not found: {page_id}"
	x = int(args[3]) if len(args) > 3 else 0
	y = int(args[4]) if len(args) > 4 else 0
	width = int(args[5]) if len(args) > 5 else 100
	height = int(args[6]) if len(args) > 6 else 50
	widget = Widget(
		id=widget_id,
		page_id=page_id,
		type=widget_type,
		position=(x, y),
		size=(width, height),
	)
	session.world.pages[page_id].widgets[widget_id] = widget
	return True, f"Added widget {widget_id} to page {page_id}"


def remove_path(session: RunnerSession, dot_path: str) -> tuple[bool, str]:
	parts = dot_path.split(".")
	if len(parts) == 2 and parts[0] in {"points", "pages", "alarms", "devices", "histories", "scripts", "deployments"}:
		collection = getattr(session.world, parts[0])
		if parts[1] in collection:
			del collection[parts[1]]
			return True, f"Removed {dot_path}"
	if len(parts) == 4 and parts[0] == "pages" and parts[2] == "widgets":
		page = session.world.pages.get(parts[1])
		if page and parts[3] in page.widgets:
			del page.widgets[parts[3]]
			return True, f"Removed {dot_path}"
	return False, f"Cannot remove path: {dot_path}"


def handle_world(session: RunnerSession, args: list[str]) -> tuple[bool, str]:
	if not args or args[0] in {"inspect", "summary"}:
		return True, world_summary(session.world)
	cmd = args[0]
	if cmd == "reset":
		session.world = MockWorld()
		session.current_golden = None
		session.initial_world_snapshot = world_to_json(session.world)
		return True, "World reset to empty."
	if cmd == "demo":
		session.world = build_demo_world()
		session.current_golden = None
		session.initial_world_snapshot = world_to_json(session.world)
		return True, "Demo world loaded."
	if cmd == "add" and len(args) >= 2:
		kind = args[1]
		payload = args[2:]
		try:
			if kind == "point":
				return add_point(session, payload)
			if kind == "page":
				return add_page(session, payload)
			if kind == "widget":
				return add_widget(session, payload)
		except (ValueError, ValidationError) as exc:
			return False, f"Invalid entity fields: {exc}"
		return False, f"Unsupported entity kind: {kind}. Supported: point, page, widget"
	if cmd == "remove" and len(args) >= 2:
		return remove_path(session, args[1])
	if cmd == "load-json" and len(args) >= 2:
		try:
			session.world = load_world_json(Path(args[1]))
		except Exception as exc:
			return False, f"World JSON load failed: {exc}"
		session.initial_world_snapshot = world_to_json(session.world)
		return True, f"Loaded world from {args[1]}"
	if cmd == "save-json" and len(args) >= 2:
		save_world_json(session.world, Path(args[1]))
		return True, f"Saved world to {args[1]}"
	return False, "Usage: world reset|demo|inspect|add point|add page|add widget|remove|load-json|save-json"


def handle_inspect(session: RunnerSession, args: list[str]) -> tuple[bool, str]:
	if not args or args[0] == "world":
		return True, world_summary(session.world)
	name = args[0]
	if name in {"points", "pages", "alarms", "devices", "histories", "scripts", "deployments"}:
		return True, json.dumps(_jsonable(getattr(session.world, name)), indent=2, ensure_ascii=False)
	if name == "path" and len(args) > 1:
		value = lookup_path(session.world, args[1])
		return True, json.dumps(value, indent=2, ensure_ascii=False)
	value = lookup_path(session.world, name)
	if value is not None:
		return True, json.dumps(value, indent=2, ensure_ascii=False)
	return False, f"Unknown inspect target: {' '.join(args)}"


def handle_display(session: RunnerSession, args: list[str]) -> tuple[bool, str]:
	if not args:
		return True, "\n".join(
			[
				f"llm-output: {'on' if session.show_llm_output else 'off'}",
				f"reasoning : {'on' if session.show_llm_reasoning else 'off'}",
				f"world     : {'on' if session.show_world_realtime else 'off'}",
				f"trace     : {'on' if session.show_trace else 'off'}",
			]
		)
	if len(args) != 2:
		return False, "Usage: display llm-output|reasoning|world|trace on|off"
	value = _parse_bool(args[1])
	if value is None:
		return False, "Display value must be on or off."
	target = args[0]
	if target == "llm-output":
		session.show_llm_output = value
	elif target == "reasoning":
		session.show_llm_reasoning = value
	elif target == "world":
		session.show_world_realtime = value
	elif target == "trace":
		session.show_trace = value
	else:
		return False, f"Unknown display target: {target}"
	return True, f"{target} {'on' if value else 'off'}"


def _describe_config_flags(session: RunnerSession) -> str:
	arch = session.config.architecture if session.config else None
	if arch is None:
		return ""
	return "\n".join(
		[
			f"hierarchical_tools={arch.hierarchical_tools}",
			f"tool_rag={arch.tool_rag.enabled}",
			f"workflow={arch.workflow.enabled}",
			f"state_machine={arch.state_machine.enabled}",
			f"resources_separation={arch.resources_separation}",
		]
	)


def _load_config_with_flags(session: RunnerSession, path: Path) -> tuple[bool, str]:
	ok, msg = load_config_into_session(session, path)
	if not ok:
		return False, msg
	flags = _describe_config_flags(session)
	return True, msg + ("\n" + flags if flags else "")


def select_config_interactive(
	session: RunnerSession, paths: list[Path], out: TextIO, inp: TextIO
) -> tuple[bool, str]:
	current = next((i for i, path in enumerate(paths) if path == session.config_path), 0)
	idx = select_from_list(out, inp, "Select a config:", [path.name for path in paths], current)
	if idx is None:
		return True, "Config selection cancelled."
	return _load_config_with_flags(session, paths[idx])


def handle_config(
	session: RunnerSession, args: list[str], out: TextIO, inp: TextIO | None
) -> tuple[bool, str]:
	paths = sorted(Path("configs").glob("*.yaml"))
	if not args:
		# Interactive mode opens a picker; non-interactive (--command) just lists.
		if inp is not None and paths:
			return select_config_interactive(session, paths, out, inp)
		listing = "\n".join(f"  {i + 1}. {path}" for i, path in enumerate(paths)) or "  (none)"
		return True, f"Current config: {session.config_path or 'not loaded'}\n{listing}"
	path = Path(args[0])
	if args[0].isdigit():
		idx = int(args[0]) - 1
		if idx < 0 or idx >= len(paths):
			return False, f"Config index out of range: {args[0]}"
		path = paths[idx]
	return _load_config_with_flags(session, path)


def handle_llm(session: RunnerSession, args: list[str]) -> tuple[bool, str]:
	if not args:
		return True, "Known providers: mock, xiaomi-mimo, anthropic, openai, deepseek"
	provider = args[0]
	model = args[1] if len(args) > 1 else ("mock" if provider == "mock" else provider)
	old_provider = session.provider_override
	old_model = session.model_override
	old_agent = session.agent
	old_config = session.config
	session.provider_override = provider
	session.model_override = model
	ok, msg = rebuild_agent(session)
	if not ok:
		session.provider_override = old_provider
		session.model_override = old_model
		session.agent = old_agent
		session.config = old_config
		return False, msg
	return True, msg


def handle_query(session: RunnerSession, args: list[str], out: TextIO) -> tuple[bool, str]:
	query = " ".join(args).strip()
	if not query and session.current_golden is not None:
		query = session.current_golden.query
	if not query:
		return False, "Usage: query <natural-language request>"
	if session.agent is None:
		ok, msg = rebuild_agent(session)
		if not ok:
			return False, msg
	before = deep_copy_world(session.world)
	golden_id = session.current_golden.id if session.current_golden else "ad-hoc"
	complexity = session.current_golden.complexity if session.current_golden else "unknown"
	domain = session.current_golden.domain if session.current_golden else "unknown"
	try:
		trace = session.agent.run(
			query,
			golden_id=golden_id,
			initial_world=session.world,
			complexity=complexity,
			domain=domain,
			event_sink=InteractiveEventSink(session, out),
		)
	except KeyboardInterrupt:
		return False, "Run interrupted. Current world preserved."
	except Exception as exc:
		return False, f"Agent run failed: {exc}"
	session.last_trace = trace
	session.last_query = query
	if session.initial_world_snapshot is None:
		session.initial_world_snapshot = world_to_json(before)
	diff = before.diff(session.world)
	lines = [
		f"trace_id        : {trace['trace_id']}",
		f"terminal_state  : {trace['execution']['terminal_state']}",
		f"total_turns     : {trace['execution']['total_turns']}",
		f"tool_calls      : {len(trace['tool_calls'])}",
		f"resource_reads  : {len(trace['resource_reads'])}",
	]
	if session.agent is not None:
		lines.append(f"jsonl           : {session.agent.tracer.traces_path}")
	lines.append("World diff:")
	lines.append(format_world_diff(diff))
	lines.append(compact_world_summary(session.world))
	return True, "\n".join(lines)


def handle_trace(session: RunnerSession) -> tuple[bool, str]:
	if session.last_trace is None:
		return False, "No trace has been recorded in this session."
	trace = session.last_trace
	return True, "\n".join(
		[
			f"trace_id        : {trace['trace_id']}",
			f"golden_id       : {trace['query']['golden_id']}",
			f"terminal_state  : {trace['execution']['terminal_state']}",
			f"total_turns     : {trace['execution']['total_turns']}",
			f"tool_calls      : {len(trace['tool_calls'])}",
			f"resource_reads  : {len(trace['resource_reads'])}",
			f"model           : {trace['experiment']['model']}",
		]
	)


def handle_save(session: RunnerSession, args: list[str]) -> tuple[bool, str]:
	path = Path(args[0]) if args else Path("eval/golden_cases/adhoc_interactive.json")
	initial = session.initial_world_snapshot or world_to_json(session.world)
	current = world_to_json(session.world)
	initial_world = world_from_snapshot(initial)
	diff = initial_world.diff(session.world)
	payload = {
		"id": path.stem,
		"query": session.last_query or (session.current_golden.query if session.current_golden else ""),
		"domain": session.current_golden.domain if session.current_golden else "ad-hoc",
		"complexity": session.current_golden.complexity if session.current_golden else "simple",
		"initial_world": initial,
		"current_world": current,
		"expected_behavior": session.current_golden.expected_behavior if session.current_golden else "success",
		"expected_final_state_diff": {
			"match_mode": "subset",
			"added_or_modified": diff.get("added_or_modified", {}),
			"removed": diff.get("removed", []),
			"unchanged_keys_must_remain": [],
		},
		"rubric_hints": [],
	}
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
	return True, f"Saved ad-hoc case draft to {path}. Canonical dataset was not modified."


def handle_golden(session: RunnerSession, args: list[str]) -> tuple[bool, str]:
	if not args:
		if not session.golden_records:
			ok, msg = load_dataset_into_session(session)
			if not ok:
				return False, msg
		preview = session.golden_records[:10]
		return True, "\n".join(f"{record.id}: {record.domain}/{record.complexity} — {record.query}" for record in preview)
	if args[0] in {"list", "search"}:
		term = " ".join(args[1:]).lower()
		if not session.golden_records:
			load_dataset_into_session(session)
		matches = [record for record in session.golden_records if not term or term in record.query.lower() or term in record.domain.lower()]
		return True, "\n".join(f"{record.id}: {record.domain}/{record.complexity} — {record.query}" for record in matches[:30])
	return load_golden_case(session, args[0])


def handle_command(
	session: RunnerSession, line: str, out: TextIO | None = None, inp: TextIO | None = None
) -> tuple[bool, str, bool]:
	if out is None:
		out = sys.stdout
	parts = _split_command(line.strip().lstrip("﻿"))
	if not parts:
		return True, "", False
	cmd = ALIASES.get(parts[0].lower(), parts[0].lower())
	args = parts[1:]
	if cmd in {"exit", "quit"}:
		return True, "Goodbye.", True
	if cmd == "help":
		return True, status_text(session), False
	if cmd == "status":
		return True, status_text(session), False
	if cmd == "golden":
		ok, msg = handle_golden(session, args)
		return ok, msg, False
	if cmd == "world":
		ok, msg = handle_world(session, args)
		return ok, msg, False
	if cmd == "inspect":
		ok, msg = handle_inspect(session, args)
		return ok, msg, False
	if cmd == "display":
		ok, msg = handle_display(session, args)
		return ok, msg, False
	if cmd == "config":
		ok, msg = handle_config(session, args, out, inp)
		return ok, msg, False
	if cmd == "llm":
		ok, msg = handle_llm(session, args)
		return ok, msg, False
	if cmd == "query":
		ok, msg = handle_query(session, args, out)
		return ok, msg, False
	if cmd == "trace":
		ok, msg = handle_trace(session)
		return ok, msg, False
	if cmd == "save":
		ok, msg = handle_save(session, args)
		return ok, msg, False
	return False, f"Unknown command: {cmd}. Type help for available commands.", False


def create_session(args: argparse.Namespace) -> RunnerSession:
	session = RunnerSession(
		dataset_path=Path(args.dataset),
		provider_override=args.provider,
		model_override=args.model,
		results_root=Path(args.results_root),
	)
	if args.no_world_realtime:
		session.show_world_realtime = False
	session.show_llm_output = args.show_llm_output
	session.show_llm_reasoning = args.show_reasoning
	if args.config:
		ok, msg = load_config_into_session(session, Path(args.config))
		if not ok:
			session.last_error = msg
	elif DEFAULT_CONFIG.exists():
		ok, msg = load_config_into_session(session, DEFAULT_CONFIG)
		if not ok:
			session.last_error = msg
	if session.dataset_path.exists():
		load_dataset_into_session(session)
	return session


def run_repl(session: RunnerSession, *, inp: TextIO = sys.stdin, out: TextIO = sys.stdout) -> int:
	_print(out, status_text(session))
	if session.last_error:
		_print(out, f"warning: {session.last_error}")
	while True:
		_print(out, "",)
		try:
			line = input("> ") if inp is sys.stdin else inp.readline()
		except (EOFError, KeyboardInterrupt):
			_print(out, "Goodbye.")
			return 0
		if not line:
			_print(out, "Goodbye.")
			return 0
		ok, msg, should_exit = handle_command(session, line.strip(), out, inp)
		if msg:
			prefix = "" if ok else "error: "
			_print(out, prefix + msg)
		if should_exit:
			return 0


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="SCADA Agent interactive evaluation runner")
	parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Experiment YAML config path")
	parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="Golden dataset JSONL path")
	parser.add_argument("--provider", default="mock", help="LLM provider override")
	parser.add_argument("--model", default="mock", help="LLM model override")
	parser.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT), help="Interactive trace output root")
	parser.add_argument(
		"--show-llm-output",
		action=argparse.BooleanOptionalAction,
		default=True,
		help="Print assistant text during runs (default: on)",
	)
	parser.add_argument(
		"--show-reasoning",
		action=argparse.BooleanOptionalAction,
		default=True,
		help="Print provider reasoning during runs (default: on)",
	)
	parser.add_argument("--no-world-realtime", action="store_true", help="Suppress per-step world display")
	parser.add_argument("--command", action="append", help="Run a command non-interactively; can be repeated")
	return parser


def main(argv: list[str] | None = None) -> int:
	parser = build_parser()
	args = parser.parse_args(argv)
	session = create_session(args)
	if args.command:
		_print(sys.stdout, status_text(session))
		for command in args.command:
			ok, msg, should_exit = handle_command(session, command, sys.stdout)
			if msg:
				_print(sys.stdout, ("" if ok else "error: ") + msg)
			if not ok:
				return 1
			if should_exit:
				return 0
		return 0
	return run_repl(session)


if __name__ == "__main__":
	sys.exit(main())
