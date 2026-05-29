"""Test that demonstrates the LLM getting stuck when state machine is ON but workflow is OFF.

The LLM starts in ANALYZE_INTENT (read-only tools). It wants to call create_analog_alarm
but that tool is not whitelisted. With no workflow engine to drive transitions, and the
real LLM unable to set next_state, the agent is trapped until max_turns.

Run from the repo root:

    uv run python scripts/test_stuck_state_machine.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(_REPO_ROOT))

from agent.orchestrator import assemble, build_demo_world


def main() -> int:
	# Build agent with state machine ON, workflow OFF — the trap condition.
	# We can't just call assemble() with a different config, so we'll use
	# the D_minimal config (which has SM=on, workflow=off, model=mock)
	# and swap the model provider in-memory.
	config_path = "configs/D_minimal.yaml"
	print(f"[setup] loading config from {config_path}")

	# Instead of hacking the config, we'll create a temporary config file
	# that mirrors D_minimal but uses the real xiaomi-mimo provider.
	tmp_config = _REPO_ROOT / "configs" / "_stuck_test.yaml"
	tmp_config.write_text(
		"""\
name: stuck_state_machine_test
description: |
  Reproduces the stuck-in-ANALYZE_INTENT bug: state machine ON, workflow OFF,
  real LLM provider. The LLM cannot transition out of the initial state.

architecture:
  hierarchical_tools: true
  tool_rag:
    enabled: false
  workflow:
    enabled: false
  state_machine:
    enabled: true
  resources_separation: false

model:
  provider: xiaomi-mimo
  name: mimo-v2.5-pro
  temperature: 0.0
  max_tokens: 1024

world:
  backend: memory

repetitions: 1
seed_base: 42
""",
		encoding="utf-8",
	)

	agent = assemble(str(tmp_config))
	print(f"[setup] model = {agent.config.model.provider} / {agent.config.model.name}")
	print(f"[setup] state machine = {agent.config.architecture.state_machine.enabled}")
	print(f"[setup] workflow       = {agent.config.architecture.workflow.enabled}")
	print(f"[setup] max_turns      = {agent.max_turns}")
	print()

	query = "给反应釜1的TEMP_101点位加一个高温报警,阈值80度,优先级high,ID用alarm_high_temp_101"
	print(f"[query] {query}")
	print()

	world = build_demo_world()
	record = agent.run(
		query,
		golden_id="stuck-sm-test-001",
		initial_world=world,
		complexity="simple",
		domain="alarm",
	)

	exe = record["execution"]
	print("[result] ---- summary ----")
	print(f"  trace_id       : {record['trace_id']}")
	print(f"  total_turns    : {exe['total_turns']}")
	print(f"  terminal_state : {exe['terminal_state']}")
	print(f"  early_terminated: {exe['early_terminated']}")
	print(f"  termination    : {exe.get('termination_reason')}")
	print(f"  tool_calls     : {len(record['tool_calls'])}")
	print(f"  trace_jsonl    : {agent.tracer.traces_path}")
	print()

	if record["tool_calls"]:
		print("[result] ---- tool calls ----")
		for c in record["tool_calls"]:
			print(
				f"  turn={c['turn']} selected={c['selected']} action={c.get('action')} "
				f"ok={c['result_ok']} err={c.get('error_code')}"
			)
			if c.get("args"):
				print(f"           args={json.dumps(c['args'], ensure_ascii=False)}")
			if c.get("error_msg"):
				print(f"           msg={c['error_msg']}")

	print()
	print("[result] ---- llm calls ----")
	for c in record.get("llm_calls", []):
		print(
			f"  turn={c['turn']} model={c['model']} "
			f"in={c['input_tokens']} out={c['output_tokens']} "
			f"latency_ms={c['latency_ms']:.0f} stop={c['stop_reason']}"
		)

	hashes = record.get("world_snapshots", {})
	print()
	print(f"[result] world_hash {hashes.get('initial_hash')} -> {hashes.get('final_hash')}")
	changed = hashes.get("initial_hash") != hashes.get("final_hash")
	print(f"[result] world mutated? {changed}")

	# Analyze the failure
	print()
	print("[analysis] ---- stuck detection ----")
	out_of_scope_count = sum(
		1 for c in record["tool_calls"] if c.get("error_code") == "OUT_OF_SCOPE"
	)
	successful_writes = sum(
		1 for c in record["tool_calls"] if c["result_ok"] and c.get("action") not in (
			"list_pages", "list_points", "list_history", "list_scripts",
			"show_deployment_status",
		)
	)
	print(f"  OUT_OF_SCOPE errors : {out_of_scope_count}")
	print(f"  successful writes   : {successful_writes}")
	print(f"  stayed in ANALYZE_INTENT: {exe['terminal_state'] == 'ANALYZE_INTENT'}")

	stuck = exe["terminal_state"] == "ANALYZE_INTENT" and successful_writes == 0
	print()
	if stuck:
		print("[verdict] STUCK — LLM was trapped in ANALYZE_INTENT, no writes succeeded")
		print("  The state machine blocked all write tools. Without a workflow engine")
		print("  to drive transitions (Mechanism B/C/D) and the real LLM unable to")
		print("  set next_state (Mechanism A), the agent exhausted max_turns doing")
		print("  nothing useful.")
	else:
		print("[verdict] NOT STUCK — LLM managed to perform a write action")

	# Cleanup temp config
	tmp_config.unlink(missing_ok=True)
	return 0


if __name__ == "__main__":
	sys.exit(main())
