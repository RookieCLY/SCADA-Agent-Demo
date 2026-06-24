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


def _alarm_call(alarm_id: str, *, priority: str = "medium") -> dict:
	return {
		"turn": 1,
		"selected": "manage_alarms",
		"action": "create_analog_alarm",
		"args": {"id": alarm_id, "tag": "PT101", "high_limit": 2.0, "priority": priority},
		"schema_valid": True,
		"result_ok": True,
		"error_code": "OK",
		"world_diff": {
			"added_or_modified": {
				f"alarms.{alarm_id}": {
					"tag": "PT101",
					"high_limit": 2.0,
					"priority": priority,
				}
			},
			"removed": [],
		},
		"intended_entities": [f"alarms.{alarm_id}"],
		"referenced_entities": ["points.PT101"],
	}


def _alarm_golden(match_mode: str) -> GoldenRecord:
	return _golden(
		{
			"expected_final_state_diff": {
				"match_mode": match_mode,
				"added_or_modified": {
					"alarms.alarm_PT101_hi.tag": "PT101",
					"alarms.alarm_PT101_hi.high_limit": 2.0,
					"alarms.alarm_PT101_hi.priority": "medium",
				},
				"removed": [],
				"unchanged_keys_must_remain": [],
			},
			"expected_trajectory": {
				"min_steps": 1,
				"max_steps": 3,
				"required_tools": ["manage_alarms"],
				"required_actions": ["create_analog_alarm"],
				"forbidden_tools": [],
				"terminal_state": "DONE",
			},
		}
	)


def test_key_fields_accepts_generated_entity_id():
	# Model picked its own ID (alarm_pt101_high) instead of the symbolic
	# expected ID (alarm_PT101_hi); semantic fields match, so it should pass.
	golden = _alarm_golden("key_fields")
	trace = _trace([_alarm_call("alarm_pt101_high")])

	row = evaluate_trace(trace, golden)

	assert row["final_state_match"] is True
	assert row["parameter_match"] == 1.0
	assert row["task_success"] is True
	assert row["final_state_report"]["entity_aliases"] == {
		"alarms.alarm_PT101_hi": "alarms.alarm_pt101_high"
	}


def test_functional_success_separates_result_from_trajectory():
	golden = _alarm_golden("key_fields")
	golden.expected_trajectory.required_actions = ["acknowledge_alarm"]
	trace = _trace([_alarm_call("alarm_pt101_high")])

	row = evaluate_trace(trace, golden)

	assert row["final_state_match"] is True
	assert row["trajectory_match"] is False
	assert row["task_success"] is True
	assert row["task_success_deterministic"] is False
	assert row["functional_success"] is True
	assert row["strict_success"] is False
	assert 0.0 < row["weighted_success"] < 1.0


def test_key_fields_rejects_wrong_semantic_field():
	# Generated ID differs AND priority is wrong -> no alias may form.
	golden = _alarm_golden("key_fields")
	trace = _trace([_alarm_call("alarm_pt101_high", priority="high")])

	row = evaluate_trace(trace, golden)

	assert row["final_state_match"] is False
	assert row["parameter_match"] < 1.0


def test_subset_mode_still_requires_exact_entity_id():
	# subset must remain exact-path: a different generated ID is a miss.
	golden = _alarm_golden("subset")
	trace = _trace([_alarm_call("alarm_pt101_high")])

	row = evaluate_trace(trace, golden)

	assert row["final_state_match"] is False
	assert row["parameter_match"] == 0.0


def test_key_fields_accepts_generated_widget_id_within_same_page():
	golden = _golden(
		{
			"domain": "graphics",
			"expected_final_state_diff": {
				"match_mode": "key_fields",
				"added_or_modified": {
					"pages.main_page.widgets.w_text1.type": "text",
					"pages.main_page.widgets.w_text1.style.text": "欢迎",
				},
				"removed": [],
				"unchanged_keys_must_remain": [],
			},
		}
	)
	trace = _trace(
		[
			{
				"turn": 1,
				"selected": "manage_widgets",
				"action": "create_widget",
				"schema_valid": True,
				"result_ok": True,
				"error_code": "OK",
				"world_diff": {
					"added_or_modified": {
						"pages.main_page.widgets.text_auto_7": {
							"type": "text",
							"style": {"text": "欢迎"},
						}
					},
					"removed": [],
				},
			}
		]
	)

	row = evaluate_trace(trace, golden)

	assert row["final_state_match"] is True
	assert row["parameter_match"] == 1.0
	assert row["final_state_report"]["entity_aliases"] == {
		"pages.main_page.widgets.w_text1": "pages.main_page.widgets.text_auto_7"
	}


def test_key_fields_does_not_alias_point_tags():
	golden = _golden(
		{
			"expected_final_state_diff": {
				"match_mode": "key_fields",
				"added_or_modified": {"points.PT101.type": "analog"},
				"removed": [],
				"unchanged_keys_must_remain": [],
			},
		}
	)
	trace = _trace(
		[
			{
				"turn": 1,
				"selected": "manage_points",
				"action": "create_point",
				"schema_valid": True,
				"result_ok": True,
				"error_code": "OK",
				"world_diff": {
					"added_or_modified": {"points.PT102": {"type": "analog"}},
					"removed": [],
				},
			}
		]
	)

	row = evaluate_trace(trace, golden)

	assert row["final_state_match"] is False
	assert row["parameter_match"] == 0.0


def test_key_fields_cascades_page_alias_to_nested_widget():
	golden = _golden(
		{
			"domain": "graphics",
			"expected_final_state_diff": {
				"match_mode": "key_fields",
				"added_or_modified": {
					"pages.pump_station.name": "泵站画面",
					"pages.pump_station.widgets.pump1.type": "pump",
					"pages.pump_station.widgets.pump1.bindings.state": "PumpA",
				},
				"removed": [],
				"unchanged_keys_must_remain": [],
			},
		}
	)
	trace = _trace(
		[
			{
				"turn": 1,
				"selected": "manage_pages",
				"action": "create_page",
				"schema_valid": True,
				"result_ok": True,
				"error_code": "OK",
				"world_diff": {
					"added_or_modified": {
						"pages.pump_auto": {
							"name": "泵站画面",
							"widgets": {
								"pump_auto_1": {
									"type": "pump",
									"bindings": {"state": "PumpA"},
								}
							},
						}
					},
					"removed": [],
				},
			}
		]
	)

	row = evaluate_trace(trace, golden)

	assert row["final_state_match"] is True
	assert row["parameter_match"] == 1.0
	assert row["final_state_report"]["entity_aliases"] == {
		"pages.pump_station": "pages.pump_auto",
		"pages.pump_station.widgets.pump1": "pages.pump_auto.widgets.pump_auto_1",
	}
