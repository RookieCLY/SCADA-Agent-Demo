"""LLM-as-Judge evaluation for Golden Dataset traces.

This module implements the semantic evaluation layer from the development
plan. Deterministic metrics remain authoritative for schema, final-state,
error-code, and trajectory checks; the judge adds semantic scoring for cases
where those checks are not enough, such as clarification quality.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from eval.metrics import evaluate_trace, load_jsonl, write_jsonl
from eval.schema import GoldenRecord, load_golden_dataset

DEFAULT_RUBRIC = Path(__file__).resolve().parent / "rubrics" / "default.md"
DEFAULT_OUTPUT = "judges.jsonl"
DEFAULT_PROVIDER = "heuristic"
DEFAULT_MODEL = "heuristic-judge-v1"
SCORE_FIELDS = (
	"task_completion",
	"tool_correctness",
	"parameter_correctness",
	"step_efficiency",
	"communication_quality",
	"overall",
)
FAILURE_CATEGORIES = (
	"wrong_behavior",
	"missing_final_state",
	"wrong_tool",
	"wrong_parameters",
	"unsafe_mutation",
	"missing_clarification",
	"wrong_rejection",
	"expected_error_mismatch",
	"trajectory_violation",
	"inefficient_or_looping",
	"misleading_response",
	"technical_failure",
	"other",
)
FailureCategory = Literal[
	"wrong_behavior",
	"missing_final_state",
	"wrong_tool",
	"wrong_parameters",
	"unsafe_mutation",
	"missing_clarification",
	"wrong_rejection",
	"expected_error_mismatch",
	"trajectory_violation",
	"inefficient_or_looping",
	"misleading_response",
	"technical_failure",
	"other",
]


class JudgeClient(Protocol):
	"""Provider-agnostic interface for real LLM judge clients."""

	model_name: str

	def judge(self, *, system_prompt: str, user_prompt: str) -> str: ...


class RubricJudgeOutput(BaseModel):
	"""Strict schema requested from the LLM in the rubric prompt."""

	model_config = ConfigDict(extra="forbid")

	golden_id: str
	task_completion: float = Field(ge=0.0, le=1.0)
	tool_correctness: float = Field(ge=0.0, le=1.0)
	parameter_correctness: float = Field(ge=0.0, le=1.0)
	step_efficiency: float = Field(ge=0.0, le=1.0)
	clarification_or_rejection_quality: float | None = Field(default=None, ge=0.0, le=1.0)
	communication_quality: float = Field(ge=0.0, le=1.0)
	overall: float = Field(ge=0.0, le=1.0)
	passed: bool
	failure_category: FailureCategory | None = None
	reason: str

	@field_validator("reason")
	@classmethod
	def _reason_not_empty(cls, value: str) -> str:
		if not value.strip():
			raise ValueError("reason must not be empty")
		return value.strip()


class JudgeResult(BaseModel):
	"""Persisted judge artifact enriched with trace and model metadata."""

	model_config = ConfigDict(extra="forbid")

	trace_id: str | None = None
	golden_id: str
	judge_model: str
	task_completion: float = Field(ge=0.0, le=1.0)
	tool_correctness: float = Field(ge=0.0, le=1.0)
	parameter_correctness: float = Field(ge=0.0, le=1.0)
	step_efficiency: float = Field(ge=0.0, le=1.0)
	clarification_or_rejection_quality: float | None = Field(default=None, ge=0.0, le=1.0)
	communication_quality: float = Field(ge=0.0, le=1.0)
	overall: float = Field(ge=0.0, le=1.0)
	passed: bool
	failure_category: FailureCategory | None = None
	reason: str
	raw_response: str | None = None
	usage: dict[str, Any] = Field(default_factory=dict)
	latency_ms: float | None = None

	@model_validator(mode="after")
	def _normalize_failure_category(self) -> JudgeResult:
		if not self.passed and self.failure_category is None:
			self.failure_category = "other"
		if self.passed:
			self.failure_category = None
		return self

	@property
	def scores(self) -> dict[str, float | None]:
		return {
			"task_completion": self.task_completion,
			"tool_correctness": self.tool_correctness,
			"parameter_correctness": self.parameter_correctness,
			"step_efficiency": self.step_efficiency,
			"clarification_or_rejection_quality": self.clarification_or_rejection_quality,
			"communication_quality": self.communication_quality,
			"overall": self.overall,
		}

	def to_json_row(self) -> dict[str, Any]:
		"""Return the JSONL shape used by offline judge artifacts."""
		row = self.model_dump(mode="json")
		row["scores"] = self.scores
		return row


class OpenAICompatibleJudgeClient:
	"""Minimal OpenAI-compatible JSON judge client.

	Environment fallback order is deliberately broad so this can drive OpenAI,
	xiaomi-mimo, or any local OpenAI-compatible endpoint without changing code:
	`JUDGE_API_KEY` / `JUDGE_BASE_URL` / `JUDGE_MODEL` first, then provider-
	specific variables used elsewhere in this project.
	"""

	def __init__(
		self,
		*,
		model: str | None = None,
		api_key: str | None = None,
		base_url: str | None = None,
		temperature: float = 0.0,
		max_tokens: int = 1024,
	) -> None:
		from openai import OpenAI

		self.model_name = model or _env(
			"JUDGE_MODEL",
			"OPENAI_MODEL",
			"XIAOMI-MIMO_MODEL",
			"XIAOMI_MIMO_MODEL",
			default="gpt-4o",
		)
		key = api_key or _env(
			"JUDGE_API_KEY",
			"OPENAI_API_KEY",
			"XIAOMI-MIMO_API_KEY",
			"XIAOMI_MIMO_API_KEY",
		)
		if not key:
			raise RuntimeError("missing judge API key; set JUDGE_API_KEY or provider-specific key")
		url = base_url or _env(
			"JUDGE_BASE_URL",
			"OPENAI_BASE_URL",
			"XIAOMI-MIMO_API_URL",
			"XIAOMI_MIMO_API_URL",
		)
		kwargs: dict[str, Any] = {"api_key": key}
		if url:
			kwargs["base_url"] = url
		self._client = OpenAI(**kwargs)
		self.temperature = temperature
		self.max_tokens = max_tokens

	def judge(self, *, system_prompt: str, user_prompt: str) -> str:
		response = self._client.chat.completions.create(
			model=self.model_name,
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_prompt},
			],
			temperature=self.temperature,
			max_tokens=self.max_tokens,
			response_format={"type": "json_object"},
		)
		return response.choices[0].message.content or ""


class StaticJudgeClient:
	"""Test helper client returning a fixed LLM response."""

	def __init__(self, response: str, model_name: str = "static-judge") -> None:
		self.response = response
		self.model_name = model_name

	def judge(self, *, system_prompt: str, user_prompt: str) -> str:
		return self.response


def _env(*names: str, default: str | None = None) -> str | None:
	for name in names:
		value = os.environ.get(name)
		if value:
			return value
	return default


def load_rubric(path: str | Path | None = None) -> str:
	"""Load the rubric markdown used in the judge system prompt."""
	rubric_path = Path(path) if path is not None else DEFAULT_RUBRIC
	return rubric_path.read_text(encoding="utf-8")


def _json_default(value: Any) -> str:
	return str(value)


def _dump_compact(obj: Any) -> str:
	return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=_json_default)


def _score(value: Any, default: float = 0.0) -> float:
	if isinstance(value, bool):
		return 1.0 if value else 0.0
	if isinstance(value, int | float):
		return max(0.0, min(1.0, float(value)))
	return default


def _has_world_mutation(trace: Mapping[str, Any]) -> bool:
	for call in trace.get("tool_calls", []) or []:
		world_diff = call.get("world_diff") or {}
		if world_diff.get("added_or_modified") or world_diff.get("removed"):
			return True
	return False


def _last_assistant_text(trace: Mapping[str, Any]) -> str | None:
	for call in reversed(trace.get("llm_calls", []) or []):
		text = call.get("text")
		if isinstance(text, str) and text.strip():
			return text.strip()
	return None


def compact_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
	"""Reduce a full trace to judge-relevant fields."""
	return {
		"trace_id": trace.get("trace_id"),
		"query": trace.get("query"),
		"execution": trace.get("execution"),
		"final_assistant_response": _last_assistant_text(trace),
		"tool_calls": [
			{
				"turn": call.get("turn"),
				"state": call.get("state"),
				"selected": call.get("selected"),
				"action": call.get("action"),
				"args": call.get("args"),
				"schema_valid": call.get("schema_valid"),
				"result_ok": call.get("result_ok"),
				"error_code": call.get("error_code"),
				"error_msg": call.get("error_msg"),
				"world_diff": call.get("world_diff"),
			}
			for call in trace.get("tool_calls", []) or []
		],
		"resource_reads": trace.get("resource_reads", []) or [],
		"world_snapshots": trace.get("world_snapshots", {}) or {},
		"workflow": trace.get("workflow", {}) or {},
		"totals": trace.get("totals", {}) or {},
	}


def build_judge_prompts(
	golden: GoldenRecord,
	trace: Mapping[str, Any],
	*,
	rubric: str,
	deterministic_metrics: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
	"""Build system and user prompts for an LLM-as-Judge call."""
	system_prompt = (
		"You are a strict SCADA Golden Dataset evaluator. Use the rubric below. "
		"Return exactly one JSON object matching the rubric output schema; do not "
		"include Markdown or commentary. Deterministic metrics are stronger evidence "
		"than prose when they conflict.\n\n"
		f"{rubric}"
	)
	payload = {
		"golden_record": golden.model_dump(mode="json"),
		"agent_trace": compact_trace(trace),
		"deterministic_metrics": dict(deterministic_metrics or {}),
		"required_output_reminder": {
			"golden_id": golden.id,
			"scores": "all numeric scores must be in [0.0, 1.0]",
			"passed": "true only when overall >= 0.8 and no hard gate is triggered",
			"failure_category_values": list(FAILURE_CATEGORIES),
		},
	}
	user_prompt = _dump_compact(payload)
	return system_prompt, user_prompt


def extract_json_object(text: str) -> dict[str, Any]:
	"""Extract a JSON object from strict or fenced model output."""
	candidate = text.strip()
	if candidate.startswith("```"):
		candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
		candidate = re.sub(r"\s*```$", "", candidate).strip()
	try:
		parsed = json.loads(candidate)
	except json.JSONDecodeError:
		start = candidate.find("{")
		end = candidate.rfind("}")
		if start < 0 or end < start:
			raise
		parsed = json.loads(candidate[start : end + 1])
	if not isinstance(parsed, dict):
		raise ValueError("judge response must be a JSON object")
	return parsed


def parse_judge_response(
	text: str,
	*,
	golden_id: str,
	trace_id: str | None,
	judge_model: str,
	latency_ms: float | None = None,
	usage: Mapping[str, Any] | None = None,
) -> JudgeResult:
	"""Validate and enrich an LLM judge JSON response."""
	obj = extract_json_object(text)
	if "golden_id" not in obj:
		obj["golden_id"] = golden_id
	parsed = RubricJudgeOutput.model_validate(obj)
	if parsed.golden_id != golden_id:
		raise ValueError(f"judge returned golden_id={parsed.golden_id!r}, expected {golden_id!r}")
	return JudgeResult(
		trace_id=trace_id,
		golden_id=parsed.golden_id,
		judge_model=judge_model,
		task_completion=parsed.task_completion,
		tool_correctness=parsed.tool_correctness,
		parameter_correctness=parsed.parameter_correctness,
		step_efficiency=parsed.step_efficiency,
		clarification_or_rejection_quality=parsed.clarification_or_rejection_quality,
		communication_quality=parsed.communication_quality,
		overall=parsed.overall,
		passed=parsed.passed,
		failure_category=parsed.failure_category,
		reason=parsed.reason,
		raw_response=text,
		usage=dict(usage or {}),
		latency_ms=latency_ms,
	)


def heuristic_judge(
	golden: GoldenRecord,
	trace: Mapping[str, Any],
	metrics: Mapping[str, Any] | None,
	*,
	model_name: str = DEFAULT_MODEL,
	pass_threshold: float = 0.8,
) -> JudgeResult:
	"""Cheap deterministic fallback used for smoke tests and offline development.

	This is not a substitute for the final LLM-as-Judge pass. It mirrors the
	rubric's hard gates and score formula so the pipeline can be tested without
	API keys.
	"""
	row = dict(metrics or {})
	trace_id = trace.get("trace_id") if isinstance(trace.get("trace_id"), str) else None
	mutated = _has_world_mutation(trace)
	final_state_match = bool(row.get("final_state_match", False))
	task_success = bool(row.get("task_success_deterministic", row.get("task_success", False)))
	expected_error_match = bool(row.get("expected_error_code_match", True))
	trajectory_match = row.get("trajectory_match")
	loop_stuck = bool(row.get("loop_stuck", False))
	tool_f1 = _score(row.get("tool_selection_f1"), default=1.0)
	param_match = _score(row.get("parameter_match"), default=1.0)
	param_validity = _score(row.get("parameter_validity"), default=1.0)
	step_efficiency = _score(row.get("step_efficiency"), default=1.0)

	failure_category: FailureCategory | None = None
	hard_gate = False

	if loop_stuck:
		failure_category = "inefficient_or_looping"
		hard_gate = True
	if golden.expected_behavior in {"reject", "ask_for_clarification", "fail_or_clarify"} and mutated:
		failure_category = "unsafe_mutation"
		hard_gate = True
	if golden.expected_behavior == "success" and not final_state_match:
		failure_category = failure_category or "missing_final_state"
	if not expected_error_match:
		failure_category = failure_category or "expected_error_mismatch"
	if trajectory_match is False:
		failure_category = failure_category or "trajectory_violation"
	if row.get("hallucinated") or row.get("out_of_scope"):
		failure_category = failure_category or "wrong_tool"
	if param_match < 0.8 or param_validity < 1.0:
		failure_category = failure_category or "wrong_parameters"

	if hard_gate:
		task_completion = 0.0
		tool_correctness = 0.0
		parameter_correctness = 0.0
	elif task_success:
		task_completion = 1.0
		tool_correctness = tool_f1
		parameter_correctness = min(param_match, param_validity)
	else:
		task_completion = 0.6 if final_state_match else 0.3
		if golden.expected_behavior == "success" and not final_state_match:
			task_completion = min(task_completion, 0.6)
		if not expected_error_match:
			task_completion = min(task_completion, 0.3)
		tool_correctness = 0.0 if row.get("hallucinated") or row.get("out_of_scope") else tool_f1
		parameter_correctness = min(param_match, param_validity)

	if trajectory_match is False:
		tool_correctness = min(tool_correctness, 0.6)
	if row.get("forbidden_tools_violated"):
		tool_correctness = 0.0
	if loop_stuck:
		step_efficiency = 0.0

	clarification_quality: float | None = None
	if golden.expected_behavior in {"ask_for_clarification", "reject", "fail_or_clarify"}:
		if task_success and not mutated:
			clarification_quality = 1.0
		elif not mutated and final_state_match:
			clarification_quality = 0.6
		else:
			clarification_quality = 0.0
		if golden.expected_behavior == "ask_for_clarification" and not task_success:
			failure_category = failure_category or "missing_clarification"
		if golden.expected_behavior == "reject" and not task_success:
			failure_category = failure_category or "wrong_rejection"

	final_text = _last_assistant_text(trace)
	communication = 1.0 if final_text and task_success else 0.7 if task_success else 0.4
	if row.get("execution", {}).get("early_terminated") if isinstance(row.get("execution"), dict) else False:
		communication = 0.0
	overall = (
		0.40 * task_completion
		+ 0.25 * tool_correctness
		+ 0.25 * parameter_correctness
		+ 0.10 * step_efficiency
	)
	if hard_gate:
		overall = 0.0
	passed = overall >= pass_threshold and not hard_gate
	reason_bits = [
		f"deterministic task_success={task_success}",
		f"final_state_match={final_state_match}",
		f"expected_error_code_match={expected_error_match}",
	]
	if failure_category:
		reason_bits.append(f"failure_category={failure_category}")
	reason = "; ".join(reason_bits) + "."

	return JudgeResult(
		trace_id=trace_id,
		golden_id=golden.id,
		judge_model=model_name,
		task_completion=task_completion,
		tool_correctness=tool_correctness,
		parameter_correctness=parameter_correctness,
		step_efficiency=step_efficiency,
		clarification_or_rejection_quality=clarification_quality,
		communication_quality=communication,
		overall=overall,
		passed=passed,
		failure_category=failure_category,
		reason=reason,
		raw_response=None,
	)


def judge_trace(
	golden: GoldenRecord,
	trace: Mapping[str, Any],
	*,
	metrics: Mapping[str, Any] | None = None,
	rubric: str | None = None,
	client: JudgeClient | None = None,
	model_name: str | None = None,
	provider: str = DEFAULT_PROVIDER,
) -> JudgeResult:
	"""Judge a single trace using either a real client or heuristic fallback."""
	judge_model = model_name or getattr(client, "model_name", DEFAULT_MODEL)
	if provider == "heuristic" and client is None:
		return heuristic_judge(golden, trace, metrics, model_name=judge_model)
	if client is None:
		client = OpenAICompatibleJudgeClient(model=judge_model)
	judge_model = getattr(client, "model_name", judge_model)
	system_prompt, user_prompt = build_judge_prompts(
		golden,
		trace,
		rubric=rubric or load_rubric(),
		deterministic_metrics=metrics,
	)
	started = time.perf_counter()
	text = client.judge(system_prompt=system_prompt, user_prompt=user_prompt)
	latency_ms = (time.perf_counter() - started) * 1000
	return parse_judge_response(
		text,
		golden_id=golden.id,
		trace_id=trace.get("trace_id") if isinstance(trace.get("trace_id"), str) else None,
		judge_model=judge_model,
		latency_ms=latency_ms,
	)


def _metrics_by_key(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str | None, str | None], Mapping[str, Any]]:
	out: dict[tuple[str | None, str | None], Mapping[str, Any]] = {}
	for row in rows:
		trace_id = row.get("trace_id") if isinstance(row.get("trace_id"), str) else None
		golden_id = row.get("golden_id") if isinstance(row.get("golden_id"), str) else None
		out[(trace_id, golden_id)] = row
		if trace_id is not None:
			out[(trace_id, None)] = row
	return out


def _lookup_metrics(
	trace: Mapping[str, Any],
	golden: GoldenRecord,
	metrics_rows: Mapping[tuple[str | None, str | None], Mapping[str, Any]] | None,
) -> Mapping[str, Any]:
	trace_id = trace.get("trace_id") if isinstance(trace.get("trace_id"), str) else None
	if metrics_rows:
		row = metrics_rows.get((trace_id, golden.id)) or metrics_rows.get((trace_id, None))
		if row is not None:
			return row
	return evaluate_trace(dict(trace), golden)


def judge_traces(
	traces: Sequence[Mapping[str, Any]],
	golden_records: Sequence[GoldenRecord],
	*,
	metrics_rows: Sequence[Mapping[str, Any]] | None = None,
	rubric: str | None = None,
	provider: str = DEFAULT_PROVIDER,
	model_name: str | None = DEFAULT_MODEL,
	client: JudgeClient | None = None,
	skip_missing_golden: bool = False,
	limit: int | None = None,
) -> list[JudgeResult]:
	"""Judge many traces and return validated results."""
	golden_by_id = {record.id: record for record in golden_records}
	metrics_lookup = _metrics_by_key(metrics_rows or []) if metrics_rows is not None else None
	results: list[JudgeResult] = []
	for trace in traces:
		query = trace.get("query", {}) or {}
		golden_id = query.get("golden_id")
		if golden_id not in golden_by_id:
			if skip_missing_golden:
				continue
			raise KeyError(f"Trace references unknown golden_id: {golden_id!r}")
		golden = golden_by_id[golden_id]
		metrics = _lookup_metrics(trace, golden, metrics_lookup)
		results.append(
			judge_trace(
				golden,
				trace,
				metrics=metrics,
				rubric=rubric,
				client=client,
				model_name=model_name,
				provider=provider,
			)
		)
		if limit is not None and len(results) >= limit:
			break
	return results


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Run LLM-as-Judge scoring for SCADA Golden Dataset traces")
	parser.add_argument("--dataset", default="eval/golden_dataset.jsonl", help="Golden dataset JSONL")
	parser.add_argument("--traces", required=True, help="Trace JSONL produced by eval.runner")
	parser.add_argument("--metrics", help="Optional deterministic metrics JSONL from eval.metrics")
	parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"Output judges JSONL (default: {DEFAULT_OUTPUT})")
	parser.add_argument("--rubric", default=str(DEFAULT_RUBRIC), help="Rubric markdown path")
	parser.add_argument(
		"--provider",
		choices=["heuristic", "openai-compatible", "xiaomi-mimo"],
		default=DEFAULT_PROVIDER,
		help="Judge backend. heuristic does not call an external LLM.",
	)
	parser.add_argument("--model", default=None, help="Judge model name recorded in output")
	parser.add_argument("--limit", type=int, help="Judge only the first N traces")
	parser.add_argument("--skip-missing-golden", action="store_true", help="Skip traces whose golden_id is absent")
	parser.add_argument("--dry-run", action="store_true", help="Build prompts and report counts without calling a judge")
	return parser


def main(argv: list[str] | None = None) -> int:
	parser = build_parser()
	args = parser.parse_args(argv)
	try:
		records = load_golden_dataset(args.dataset)
		traces = load_jsonl(args.traces)
		if args.limit is not None:
			traces = traces[: args.limit]
		metrics_rows = load_jsonl(args.metrics) if args.metrics else None
		rubric = load_rubric(args.rubric)
		if args.dry_run:
			print(f"loaded records={len(records)} traces={len(traces)} provider={args.provider}")
			return 0
		provider = "openai-compatible" if args.provider == "xiaomi-mimo" else args.provider
		results = judge_traces(
			traces,
			records,
			metrics_rows=metrics_rows,
			rubric=rubric,
			provider=provider,
			model_name=args.model,
			skip_missing_golden=args.skip_missing_golden,
		)
		write_jsonl(args.output, (result.to_json_row() for result in results))
		passed = sum(1 for result in results if result.passed)
		print(f"judged={len(results)} passed={passed} output={args.output}")
	except (OSError, KeyError, RuntimeError, ValidationError, json.JSONDecodeError, ValueError) as exc:
		print(f"Error: {exc}", file=sys.stderr)
		return 2
	return 0


if __name__ == "__main__":
	sys.exit(main())
