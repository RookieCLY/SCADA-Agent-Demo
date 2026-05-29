import json
from pathlib import Path

from eval.schema import ExpectedFinalStateDiff, ExpectedTrajectory, GoldenRecord

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
