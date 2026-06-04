from __future__ import annotations

from agent.tool_registry import build_default_registry
from eval.metrics import aggregate_summary, evaluate_trace, evaluate_traces
from eval.schema import GoldenRecord


def _golden(payload: dict) -> GoldenRecord:
    base = {
        "id": "golden-test",
        "query": "test",
        "domain": "alarm",
        "complexity": "simple",
        "initial_world": {},
        "expected_behavior": "success",
        "expected_final_state_diff": {
            "match_mode": "subset",
            "added_or_modified": {},
            "removed": [],
            "unchanged_keys_must_remain": [],
        },
        "rubric_hints": [],
    }
    base.update(payload)
    return GoldenRecord.model_validate(base)


def _trace(tool_calls: list[dict], *, terminal_state: str = "DONE") -> dict:
    return {
        "trace_id": "trace-1",
        "experiment": {
            "config_name": "cfg",
            "model": "mock",
            "rep_index": 0,
            "seed": 42,
        },
        "query": {
            "golden_id": "golden-test",
            "complexity": "simple",
            "domain": "alarm",
        },
        "execution": {
            "terminal_state": terminal_state,
            "early_terminated": False,
            "total_turns": 1,
        },
        "tool_calls": tool_calls,
        "resource_reads": [],
        "world_snapshots": {
            "final_state_match": None,
            "match_mode": None,
            "diff_against_expected": {},
        },
        "llm_calls": [],
        "workflow": {"selected_workflow": None},
        "totals": {"input_tokens": 10, "output_tokens": 2, "cost_usd": 0.0, "e2e_latency_ms": 5.0},
    }


def test_metrics_scores_flat_atomic_in_unified_tool_space():
    registry = build_default_registry()
    golden = _golden(
        {
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
        }
    )
    trace = _trace(
        [
            {
                "turn": 1,
                "visible_count": 39,
                "selected": "create_analog_alarm",
                "action": "create_analog_alarm",
                "args": {"id": "alarm_high_temp_101", "tag": "TEMP_101", "high_limit": 80},
                "schema_valid": True,
                "result_ok": True,
                "error_code": "OK",
                "world_diff": {
                    "added_or_modified": {
                        "alarms.alarm_high_temp_101": {
                            "tag": "TEMP_101",
                            "high_limit": 80.0,
                        }
                    },
                    "removed": [],
                },
                "intended_entities": ["alarms.alarm_high_temp_101"],
                "referenced_entities": ["points.TEMP_101"],
            }
        ]
    )

    row = evaluate_trace(trace, golden, registry)

    assert row["tool_selection_f1"] == 1.0
    assert row["actual_logical_tools"] == [
        {"domain": "manage_alarms", "action": "create_analog_alarm"}
    ]
    assert row["final_state_match"] is True
    assert row["parameter_match"] == 1.0
    assert row["trajectory_match"] is True
    assert row["task_success"] is True


def test_metrics_flags_hallucinated_and_out_of_scope_tool():
    golden = _golden(
        {
            "expected_trajectory": {
                "min_steps": 1,
                "max_steps": 2,
                "required_tools": ["manage_alarms"],
                "required_actions": ["create_analog_alarm"],
                "forbidden_tools": [],
                "terminal_state": "DONE",
            }
        }
    )
    trace = _trace(
        [
            {
                "turn": 1,
                "visible_count": 1,
                "selected": "manage_alarms",
                "action": "not_a_real_action",
                "schema_valid": True,
                "result_ok": False,
                "error_code": "OUT_OF_SCOPE",
                "world_diff": None,
            }
        ]
    )

    row = evaluate_trace(trace, golden)

    assert row["hallucinated"] is True
    assert row["hallucinated_tool_rate"] == 1.0
    assert row["out_of_scope"] is True
    assert row["out_of_scope_tool_rate"] == 1.0
    assert row["tool_selection_f1"] == 0.0


def test_metrics_step_efficiency_zero_when_required_steps_missing():
    golden = _golden(
        {
            "expected_trajectory": {
                "min_steps": 1,
                "max_steps": 2,
                "required_tools": ["manage_pages"],
                "required_actions": ["create_page"],
                "forbidden_tools": [],
                "terminal_state": "DONE",
            },
        }
    )

    row = evaluate_trace(_trace([]), golden)

    assert row["trajectory_match"] is False
    assert row["step_count"] == 0
    assert row["step_efficiency"] == 0.0


def test_metrics_accepts_strict_reject_with_no_mutation():
    golden = _golden(
        {
            "expected_behavior": "reject",
            "expected_final_state_diff": {
                "match_mode": "strict",
                "added_or_modified": {},
                "removed": [],
                "unchanged_keys_must_remain": [],
            },
        }
    )
    row = evaluate_trace(_trace([]), golden)

    assert row["final_state_match"] is True
    assert row["parameter_validity"] == 1.0
    assert row["task_success"] is True


def test_metrics_detects_cascade_failure():
    golden = _golden(
        {
            "expected_behavior": "fail_or_clarify",
            "expected_error_code": "POINT_NOT_FOUND",
            "expected_final_state_diff": {
                "match_mode": "strict",
                "added_or_modified": {},
                "removed": [],
                "unchanged_keys_must_remain": [],
            },
        }
    )
    trace = _trace(
        [
            {
                "turn": 1,
                "selected": "create_point",
                "action": "create_point",
                "schema_valid": False,
                "result_ok": False,
                "error_code": "SCHEMA_ERROR",
                "world_diff": None,
                "intended_entities": ["points.P1"],
                "referenced_entities": [],
            },
            {
                "turn": 2,
                "selected": "create_analog_alarm",
                "action": "create_analog_alarm",
                "schema_valid": True,
                "result_ok": False,
                "error_code": "POINT_NOT_FOUND",
                "world_diff": None,
                "intended_entities": ["alarms.a1"],
                "referenced_entities": ["points.P1"],
            },
        ]
    )

    row = evaluate_trace(trace, golden)

    assert row["expected_error_code_match"] is True
    assert row["cascade_failure_count"] == 1
    assert row["schema_error_rate"] == 0.5
    assert row["reference_error_rate"] == 0.5


def test_evaluate_traces_and_summary():
    golden = _golden({})
    rows = evaluate_traces([_trace([])], [golden])
    summary = aggregate_summary(rows)

    assert len(rows) == 1
    assert summary["n"] == 1
    assert summary["final_state_match_rate"] == 1.0
