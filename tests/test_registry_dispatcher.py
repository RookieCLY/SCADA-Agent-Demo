"""Registry + dispatcher tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.config import ArchitectureConfig
from agent.dispatcher import dispatch_atomic, dispatch_domain
from agent.tool_registry import ToolRegistry, build_default_registry
from tools._base import ErrorCode
from world import MockWorld
from world.models import Point


# ============================================================ registry
def test_registry_self_check_passes(registry: ToolRegistry):
    registry.selfcheck()


def test_registry_reverse_lookup_complete(registry: ToolRegistry):
    for meta in registry.all_atomics():
        d, a = registry.lookup(meta.name)
        assert d == meta.domain and a == meta.action


def test_registry_unknown_atomic_raises(registry: ToolRegistry):
    with pytest.raises(KeyError):
        registry.lookup("not_a_tool")


def test_registry_unknown_domain_raises(registry: ToolRegistry):
    with pytest.raises(KeyError):
        registry.domain("not_a_domain")


def test_visible_to_llm_flat(registry: ToolRegistry):
    arch = ArchitectureConfig(hierarchical_tools=False)
    view = registry.visible_to_llm(arch)
    assert all(t["kind"] == "atomic" for t in view)
    # 6 (alarms) + 4 (points) + 6 (pages) = 16 atomics
    assert len(view) == len(registry.all_atomics())


def test_visible_to_llm_hierarchical(registry: ToolRegistry):
    arch = ArchitectureConfig(hierarchical_tools=True)
    view = registry.visible_to_llm(arch)
    assert all(t["kind"] == "domain" for t in view)
    # 17 domains registered in build_default_registry
    assert len(view) == 17
    names = {t["name"] for t in view}
    assert names == {
        "manage_alarms",
        "manage_points",
        "manage_pages",
        "manage_graphics",
        "manage_history",
        "manage_scripts",
        "deployment",
        "manage_devices",
        "manage_trends",
        "manage_recipes",
        "manage_users",
        "manage_communication",
        "manage_reports",
        "manage_schedules",
        "manage_security",
        "manage_databases",
        "manage_notifications",
    }


def test_register_duplicate_atomic_raises():
    reg = ToolRegistry()
    from tools.manage_alarms import ALARM_ACTIONS, ManageAlarmsArgs

    reg.register_domain("manage_alarms", ManageAlarmsArgs, ALARM_ACTIONS)
    with pytest.raises(ValueError, match="duplicate"):
        reg.register_domain("manage_alarms", ManageAlarmsArgs, ALARM_ACTIONS)


# ============================================================ dispatcher
def _seed_point(world: MockWorld) -> None:
    world.points["TEMP_101"] = Point(tag="TEMP_101", type="analog", unit="°C")


def test_dispatch_atomic_happy(registry: ToolRegistry, fresh_world: MockWorld):
    _seed_point(fresh_world)
    result, parsed, lat = dispatch_atomic(
        registry,
        "create_analog_alarm",
        {"id": "a1", "tag": "TEMP_101", "high_limit": 80},
        fresh_world,
    )
    assert result.ok and parsed is not None
    assert lat >= 0


def test_dispatch_atomic_schema_error(registry: ToolRegistry, fresh_world: MockWorld):
    result, parsed, _ = dispatch_atomic(
        registry,
        "create_analog_alarm",
        {"id": "a1"},  # missing tag
        fresh_world,
    )
    assert result.error_code == ErrorCode.SCHEMA_ERROR and parsed is None


def test_dispatch_atomic_unknown_tool(registry: ToolRegistry, fresh_world: MockWorld):
    result, *_ = dispatch_atomic(registry, "no_such_tool", {}, fresh_world)
    assert result.error_code == ErrorCode.SCHEMA_ERROR


def test_dispatch_domain_happy(registry: ToolRegistry, fresh_world: MockWorld):
    _seed_point(fresh_world)
    result, parsed, lat, action = dispatch_domain(
        registry,
        "manage_alarms",
        {"action": "create_analog_alarm", "id": "a1", "tag": "TEMP_101", "high_limit": 80},
        fresh_world,
    )
    assert result.ok and action == "create_analog_alarm"
    assert parsed is not None


def test_dispatch_domain_unknown_action(registry: ToolRegistry, fresh_world: MockWorld):
    result, _, _, action = dispatch_domain(
        registry, "manage_alarms", {"action": "fly_to_moon"}, fresh_world
    )
    assert result.error_code == ErrorCode.SCHEMA_ERROR
    assert "fly_to_moon" in (result.error_msg or "")
    assert action == "fly_to_moon"


def test_dispatch_domain_missing_action(registry: ToolRegistry, fresh_world: MockWorld):
    result, *_ = dispatch_domain(registry, "manage_alarms", {}, fresh_world)
    assert result.error_code == ErrorCode.SCHEMA_ERROR


def test_dispatch_domain_unknown_domain(registry: ToolRegistry, fresh_world: MockWorld):
    result, *_ = dispatch_domain(
        registry, "not_a_domain", {"action": "x"}, fresh_world
    )
    assert result.error_code == ErrorCode.SCHEMA_ERROR


# ============================================================ generated-examples loader
def test_merge_generated_examples_appends_and_dedups(tmp_path: Path):
    """``merge_generated_examples`` should grow each atomic's examples list,
    skip duplicates, ignore unknown keys, and tolerate malformed entries."""
    reg = build_default_registry()
    before = list(reg.atomic("create_analog_alarm").examples)

    sidecar = tmp_path / "generated.json"
    sidecar.write_text(
        json.dumps(
            {
                "create_analog_alarm": [
                    "全新示例 X",
                    "全新示例 Y",
                    before[0],  # duplicate of an existing one — must be skipped
                ],
                "ghost_tool_does_not_exist": ["should be ignored"],
                "validate_project": "not a list — must be ignored",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    appended = reg.merge_generated_examples(sidecar)
    assert appended == {"create_analog_alarm": 2}

    after = reg.atomic("create_analog_alarm").examples
    assert len(after) == len(before) + 2
    assert "全新示例 X" in after and "全新示例 Y" in after
    # no double-append of the duplicate
    assert after.count(before[0]) == 1


def test_merge_generated_examples_missing_file_is_noop(tmp_path: Path):
    reg = build_default_registry()
    snapshot = {m.name: list(m.examples) for m in reg.all_atomics()}
    out = reg.merge_generated_examples(tmp_path / "nope.json")
    assert out == {}
    for m in reg.all_atomics():
        assert m.examples == snapshot[m.name]


def test_build_default_registry_with_tool_counts():
    core_tool_names = {
        'apply_flow_layout', 'bind_point', 'create_analog_alarm', 'create_circle',
        'create_digital_alarm', 'create_line', 'create_page', 'create_point',
        'create_rect', 'create_script', 'create_text', 'create_widget',
        'delete_alarm', 'delete_page', 'delete_point', 'delete_script',
        'delete_widget', 'deploy_project', 'disable_alarm', 'disable_history',
        'disable_script', 'enable_alarm', 'enable_history', 'enable_script',
        'group_widgets', 'list_history', 'list_pages', 'list_points',
        'list_scripts', 'query_history', 'rename_page', 'rollback_deployment',
        'set_retention', 'set_threshold', 'set_widget_style', 'show_deployment_status',
        'update_point', 'update_script_body', 'validate_project'
    }
    
    # 1. tool_count=30: must still retain all 39 core tools
    reg30 = build_default_registry(tool_count=30)
    all_names30 = {t.name for t in reg30.all_atomics()}
    assert len(all_names30) == 39
    assert core_tool_names.issubset(all_names30)
    
    # 2. tool_count=100: exactly 100 tools
    reg100 = build_default_registry(tool_count=100)
    all_names100 = {t.name for t in reg100.all_atomics()}
    assert len(all_names100) == 100
    assert core_tool_names.issubset(all_names100)
    
    # 3. tool_count=300: exactly 300 tools
    reg300 = build_default_registry(tool_count=300)
    all_names300 = {t.name for t in reg300.all_atomics()}
    assert len(all_names300) == 300
    assert core_tool_names.issubset(all_names300)
    
    # 4. tool_count=500: exactly 500 tools
    reg500 = build_default_registry(tool_count=500)
    all_names500 = {t.name for t in reg500.all_atomics()}
    assert len(all_names500) == 500
    assert core_tool_names.issubset(all_names500)
