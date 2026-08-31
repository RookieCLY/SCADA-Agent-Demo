from collections import Counter

from eval.schema import GoldenRecord, load_golden_dataset
from world import MockWorld


def test_golden_record_schema_parsing():
    """Test that GoldenRecord can parse the standard format correctly."""
    raw_json = '''{
      "id": "golden-042",
      "query": "给反应釜1的温度显示绑定TEMP_101",
      "domain": "binding",
      "complexity": "simple",
      "initial_world": {
        "pages": {
          "p1": {
            "id": "p1",
            "name": "反应釜监控",
            "widgets": {
              "w_thermo_1": {
                "id": "w_thermo_1",
                "page_id": "p1",
                "type": "thermometer",
                "position": [100, 200],
                "size": [80, 200],
                "expected_binding_types": {"value": ["analog"]}
              }
            }
          }
        },
        "points": {
          "TEMP_101": {"tag": "TEMP_101", "type": "analog", "unit": "°C"}
        }
      },
      "expected_behavior": "success",
      "expected_final_state_diff": {
        "match_mode": "subset",
        "added_or_modified": {
          "pages.p1.widgets.w_thermo_1.bindings.value": "TEMP_101"
        },
        "removed": [],
        "unchanged_keys_must_remain": ["points.TEMP_101"]
      },
      "expected_trajectory": {
        "min_steps": 1,
        "max_steps": 3,
        "required_tools": ["manage_graphics"],
        "required_actions": ["bind_point"],
        "forbidden_tools": ["deploy_project", "manage_alarms"],
        "terminal_state": "DONE"
      },
      "expected_error_code": null,
      "expected_workflow_id": null,
      "rubric_hints": [
        "应直接绑定,无需创建额外图元"
      ]
    }'''
    
    record = GoldenRecord.model_validate_json(raw_json)
    assert record.id == "golden-042"
    assert record.expected_behavior == "success"
    assert record.expected_final_state_diff.match_mode == "subset"
    assert "pages.p1.widgets.w_thermo_1.bindings.value" in record.expected_final_state_diff.added_or_modified
    assert record.expected_trajectory is not None
    assert record.expected_trajectory.min_steps == 1
    assert "manage_graphics" in record.expected_trajectory.required_tools
    
def test_golden_record_failure_parsing():
    """Test parsing a failure scenario."""
    raw_json = '''{
      "id": "golden-043",
      "query": "给反应釜1的温度显示绑定TEMP_999",
      "domain": "binding",
      "complexity": "simple",
      "initial_world": {},
      "expected_behavior": "fail_or_clarify",
      "expected_final_state_diff": {
        "match_mode": "strict",
        "added_or_modified": {},
        "removed": []
      },
      "expected_error_code": "POINT_NOT_FOUND",
      "expected_alternative": "Agent 应先查"
    }'''
    
    record = GoldenRecord.model_validate_json(raw_json)
    assert record.id == "golden-043"
    assert record.expected_behavior == "fail_or_clarify"
    assert record.expected_error_code == "POINT_NOT_FOUND"
    assert record.expected_trajectory is None


def test_golden_dataset_acceptance_coverage():
    records = load_golden_dataset("eval/golden_dataset.jsonl")

    assert len(records) >= 100

    complexity = Counter(record.complexity for record in records)
    assert complexity["simple"] >= 25
    assert complexity["medium"] >= 35
    assert complexity["complex"] >= 25

    domains = Counter(record.domain for record in records)
    for domain in ["page", "point", "alarm", "graphics", "history", "script", "multi"]:
        assert domains[domain] >= 10

    behavior = Counter(record.expected_behavior for record in records)
    assert behavior["success"] >= 65
    assert behavior["reject"] >= 15
    assert behavior["ask_for_clarification"] + behavior["fail_or_clarify"] >= 15

    workflow = Counter(record.expected_workflow_id for record in records)
    workflow_hits = len(records) - workflow[None]
    assert workflow_hits / len(records) >= 0.65
    for workflow_id in [
        "alarm_config",
        "chemical_screen",
        "deployment_check",
        "graphics_layout",
        "history_query",
        "point_binding",
        "point_creation",
        "pump_station_screen",
        "script_config",
    ]:
        assert workflow[workflow_id] >= 6

    for record in records:
        MockWorld.model_validate(record.initial_world or {})
