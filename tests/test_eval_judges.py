from __future__ import annotations

import json

from eval.judges import (
	StaticJudgeClient,
	extract_json_object,
	heuristic_judge,
	judge_trace,
	parse_judge_response,
)
from eval.metrics import evaluate_trace
from eval.schema import GoldenRecord


def _golden(payload: dict | None = None) -> GoldenRecord:
	base = {
		"id": "golden-judge",
		"query": "给TEMP_101加高温报警",
		"domain": "alarm",
		"complexity": "simple",
		"initial_world": {},
		"expected_behavior": "success",
		"expected_final_state_diff": {
			"match_mode": "subset",
			"added_or_modified": {
				"alarms.alarm_high_temp_101.tag": "TEMP_101",
				"alarms.alarm_high_temp_101.high_limit": 80.0,
			},
			"removed": [],
			"unchanged_keys_must_remain": [],
		},
		"expected_trajectory": {
			"min_steps": 1,
			"max_steps": 2,
			"required_tools": ["manage_alarms"],
			"required_actions": ["create_analog_alarm"],
			"forbidden_tools": [],
			"terminal_state": "DONE",
		},
		"rubric_hints": [],
	}
	if payload:
		base.update(payload)
	return GoldenRecord.model_validate(base)


def _trace(tool_calls: list[dict]) -> dict:
	return {
		"trace_id": "trace-judge-1",
		"experiment": {"config_name": "cfg", "model": "mock", "rep_index": 0, "seed": 42},
		"query": {"golden_id": "golden-judge", "complexity": "simple", "domain": "alarm"},
		"execution": {"terminal_state": "DONE", "early_terminated": False, "total_turns": 2},
		"tool_calls": tool_calls,
		"resource_reads": [],
		"world_snapshots": {"final_state_match": None, "match_mode": None, "diff_against_expected": {}},
		"llm_calls": [
			{"turn": 1, "model": "mock", "input_tokens": 20, "output_tokens": 5, "latency_ms": 1.0, "stop_reason": "tool_use", "text": None},
			{"turn": 2, "model": "mock", "input_tokens": 10, "output_tokens": 5, "latency_ms": 1.0, "stop_reason": "end_turn", "text": "已创建高温报警。"},
		],
		"workflow": {"selected_workflow": None},
		"totals": {"input_tokens": 30, "output_tokens": 10, "cost_usd": 0.0, "e2e_latency_ms": 2.0},
	}


def _successful_alarm_call() -> dict:
	return {
		"turn": 1,
		"visible_count": 3,
		"selected": "manage_alarms",
		"action": "create_analog_alarm",
		"args": {"id": "alarm_high_temp_101", "tag": "TEMP_101", "high_limit": 80},
		"schema_valid": True,
		"result_ok": True,
		"error_code": "OK",
		"world_diff": {
			"added_or_modified": {
				"alarms.alarm_high_temp_101": {"tag": "TEMP_101", "high_limit": 80.0}
			},
			"removed": [],
		},
		"intended_entities": ["alarms.alarm_high_temp_101"],
		"referenced_entities": ["points.TEMP_101"],
	}


def test_extract_json_object_accepts_fenced_json():
	obj = extract_json_object('```json\n{"passed": true, "overall": 1.0}\n```')
	assert obj == {"passed": True, "overall": 1.0}


def test_parse_judge_response_outputs_scores_row():
	text = json.dumps(
		{
			"golden_id": "golden-judge",
			"task_completion": 1.0,
			"tool_correctness": 1.0,
			"parameter_correctness": 1.0,
			"step_efficiency": 1.0,
			"clarification_or_rejection_quality": None,
			"communication_quality": 1.0,
			"overall": 1.0,
			"passed": True,
			"failure_category": None,
			"reason": "All expected changes are present.",
		}
	)
	result = parse_judge_response(
		text,
		golden_id="golden-judge",
		trace_id="trace-judge-1",
		judge_model="static",
	)
	row = result.to_json_row()

	assert result.passed is True
	assert row["scores"]["task_completion"] == 1.0
	assert row["trace_id"] == "trace-judge-1"


def test_heuristic_judge_uses_deterministic_metrics_for_success():
	golden = _golden()
	trace = _trace([_successful_alarm_call()])
	metrics = evaluate_trace(trace, golden)

	result = heuristic_judge(golden, trace, metrics)

	assert result.passed is True
	assert result.overall == 1.0
	assert result.failure_category is None


def test_heuristic_judge_hard_gates_unexpected_mutation():
	golden = _golden(
		{
			"expected_behavior": "reject",
			"expected_final_state_diff": {
				"match_mode": "strict",
				"added_or_modified": {},
				"removed": [],
				"unchanged_keys_must_remain": [],
			},
			"expected_trajectory": None,
		}
	)
	trace = _trace([_successful_alarm_call()])
	metrics = evaluate_trace(trace, golden)

	result = heuristic_judge(golden, trace, metrics)

	assert result.passed is False
	assert result.overall == 0.0
	assert result.failure_category == "unsafe_mutation"


def test_judge_trace_accepts_static_client_response():
	golden = _golden()
	trace = _trace([_successful_alarm_call()])
	metrics = evaluate_trace(trace, golden)
	client = StaticJudgeClient(
		json.dumps(
			{
				"golden_id": "golden-judge",
				"task_completion": 1.0,
				"tool_correctness": 1.0,
				"parameter_correctness": 1.0,
				"step_efficiency": 1.0,
				"clarification_or_rejection_quality": None,
				"communication_quality": 1.0,
				"overall": 1.0,
				"passed": True,
				"failure_category": None,
				"reason": "Static judge accepted the trace.",
			}
		),
		model_name="static",
	)

	result = judge_trace(golden, trace, metrics=metrics, rubric="rubric", client=client, provider="openai-compatible")

	assert result.judge_model == "static"
	assert result.passed is True
	assert result.raw_response is not None


def test_judge_trace_caps_over_permissive_llm_response_with_metrics():
	golden = _golden()
	trace = _trace([_successful_alarm_call()])
	metrics = evaluate_trace(trace, golden)
	metrics["task_success"] = False
	metrics["task_success_deterministic"] = False
	metrics["trajectory_match"] = False
	client = StaticJudgeClient(
		json.dumps(
			{
				"golden_id": "golden-judge",
				"task_completion": 1.0,
				"tool_correctness": 1.0,
				"parameter_correctness": 1.0,
				"step_efficiency": 1.0,
				"clarification_or_rejection_quality": None,
				"communication_quality": 1.0,
				"overall": 1.0,
				"passed": True,
				"failure_category": None,
				"reason": "Static judge was too permissive.",
			}
		),
		model_name="static",
	)

	result = judge_trace(golden, trace, metrics=metrics, rubric="rubric", client=client, provider="openai-compatible")

	# task_success guardrail removed: trajectory_match=false now caps overall at 0.95 (passes the 0.8 threshold).
	assert result.passed is True
	assert result.overall == 0.95
	assert result.tool_correctness == 0.6
	assert result.failure_category == "trajectory_violation"
	assert "Deterministic guardrails applied" in result.reason


def test_heuristic_judge_tolerates_generated_id_with_key_fields():
	# golden expects a symbolic ID; the agent generated a different one. With
	# key_fields the deterministic metrics alias it, so the judge must pass.
	golden = _golden(
		{
			"expected_final_state_diff": {
				"match_mode": "key_fields",
				"added_or_modified": {
					"alarms.alarm_temp101_high.tag": "TEMP_101",
					"alarms.alarm_temp101_high.high_limit": 80.0,
				},
				"removed": [],
				"unchanged_keys_must_remain": [],
			},
		}
	)
	trace = _trace([_successful_alarm_call()])
	metrics = evaluate_trace(trace, golden)

	assert metrics["final_state_match"] is True
	assert metrics["parameter_match"] == 1.0
	assert metrics["final_state_report"].get("entity_aliases") == {
		"alarms.alarm_temp101_high": "alarms.alarm_high_temp_101"
	}

	result = heuristic_judge(golden, trace, metrics)

	assert result.passed is True
	assert result.failure_category is None
