"""Acceptance tests for Phase 2 domain tools — graphics / history / scripts / deployment."""
from __future__ import annotations

import pytest

from tools._base import ErrorCode
from tools.deployment import (
    DeployProject,
    DeployProjectArgs,
    RollbackDeployment,
    RollbackDeploymentArgs,
    ShowDeploymentStatus,
    ShowDeploymentStatusArgs,
    ValidateProject,
    ValidateProjectArgs,
)
from tools.manage_alarms import CreateAnalogAlarm, CreateAnalogAlarmArgs
from tools.manage_graphics import (
    ApplyFlowLayout,
    ApplyFlowLayoutArgs,
    CreateCircle,
    CreateCircleArgs,
    CreateLine,
    CreateLineArgs,
    CreateRect,
    CreateRectArgs,
    CreateText,
    CreateTextArgs,
    DeleteWidget,
    DeleteWidgetArgs,
    GroupWidgets,
    GroupWidgetsArgs,
    SetWidgetStyle,
    SetWidgetStyleArgs,
)
from tools.manage_history import (
    DisableHistory,
    DisableHistoryArgs,
    EnableHistory,
    EnableHistoryArgs,
    ListHistory,
    ListHistoryArgs,
    QueryHistory,
    QueryHistoryArgs,
    SetRetention,
    SetRetentionArgs,
)
from tools.manage_pages import CreatePage, CreatePageArgs, CreateWidget, CreateWidgetArgs
from tools.manage_scripts import (
    CreateScript,
    CreateScriptArgs,
    DeleteScript,
    DeleteScriptArgs,
    DisableScript,
    DisableScriptArgs,
    EnableScript,
    EnableScriptArgs,
    ListScripts,
    ListScriptsArgs,
    UpdateScriptBody,
    UpdateScriptBodyArgs,
)
from world import MockWorld


# ============================================================ graphics
def _seed_page(world: MockWorld) -> None:
    CreatePage().run(CreatePageArgs(id="p1", name="P1"), world)


def test_create_rect_happy_and_page_not_found(fresh_world: MockWorld):
    _seed_page(fresh_world)
    r = CreateRect().run(
        CreateRectArgs(page_id="p1", widget_id="r1", position=(10, 20), size=(100, 80)),
        fresh_world,
    )
    assert r.ok and "r1" in fresh_world.pages["p1"].widgets

    r2 = CreateRect().run(
        CreateRectArgs(page_id="missing", widget_id="r2", position=(0, 0), size=(10, 10)),
        fresh_world,
    )
    assert r2.error_code == ErrorCode.PAGE_NOT_FOUND


def test_create_rect_already_exists(fresh_world: MockWorld):
    _seed_page(fresh_world)
    args = CreateRectArgs(page_id="p1", widget_id="r1", position=(0, 0), size=(10, 10))
    assert CreateRect().run(args, fresh_world).ok
    r = CreateRect().run(args, fresh_world)
    assert r.error_code == ErrorCode.ALREADY_EXISTS


def test_create_circle_size_derived(fresh_world: MockWorld):
    _seed_page(fresh_world)
    r = CreateCircle().run(
        CreateCircleArgs(page_id="p1", widget_id="c1", center=(50, 50), radius=10),
        fresh_world,
    )
    assert r.ok and fresh_world.pages["p1"].widgets["c1"].size == (20, 20)


def test_create_line_records_endpoints_in_style(fresh_world: MockWorld):
    _seed_page(fresh_world)
    r = CreateLine().run(
        CreateLineArgs(page_id="p1", widget_id="l1", start=(0, 0), end=(100, 50)),
        fresh_world,
    )
    assert r.ok
    style = fresh_world.pages["p1"].widgets["l1"].style
    assert style["start"] == [0, 0] and style["end"] == [100, 50]


def test_create_text_size_minimum(fresh_world: MockWorld):
    _seed_page(fresh_world)
    r = CreateText().run(
        CreateTextArgs(page_id="p1", widget_id="t1", position=(0, 0), text="Hi"),
        fresh_world,
    )
    assert r.ok
    w, h = fresh_world.pages["p1"].widgets["t1"].size
    assert w >= 32 and h >= 18


def test_apply_flow_layout_row(fresh_world: MockWorld):
    _seed_page(fresh_world)
    for wid in ("a", "b", "c"):
        CreateRect().run(
            CreateRectArgs(page_id="p1", widget_id=wid, position=(0, 0), size=(40, 30)),
            fresh_world,
        )
    r = ApplyFlowLayout().run(
        ApplyFlowLayoutArgs(
            page_id="p1", widget_ids=["a", "b", "c"], direction="row", gap=10, origin=(0, 0)
        ),
        fresh_world,
    )
    assert r.ok
    positions = [fresh_world.pages["p1"].widgets[w].position for w in ("a", "b", "c")]
    assert positions == [(0, 0), (50, 0), (100, 0)]


def test_apply_flow_layout_missing_widget(fresh_world: MockWorld):
    _seed_page(fresh_world)
    r = ApplyFlowLayout().run(
        ApplyFlowLayoutArgs(page_id="p1", widget_ids=["nope"], direction="row"),
        fresh_world,
    )
    assert r.error_code == ErrorCode.WIDGET_NOT_FOUND


def test_group_widgets_then_already_exists(fresh_world: MockWorld):
    _seed_page(fresh_world)
    for wid in ("a", "b"):
        CreateRect().run(
            CreateRectArgs(page_id="p1", widget_id=wid, position=(0, 0), size=(10, 10)),
            fresh_world,
        )
    r = GroupWidgets().run(
        GroupWidgetsArgs(page_id="p1", group_id="g1", widget_ids=["a", "b"]),
        fresh_world,
    )
    assert r.ok
    r2 = GroupWidgets().run(
        GroupWidgetsArgs(page_id="p1", group_id="g1", widget_ids=["a", "b"]),
        fresh_world,
    )
    assert r2.error_code == ErrorCode.ALREADY_EXISTS


def test_set_widget_style_merge(fresh_world: MockWorld):
    _seed_page(fresh_world)
    CreateRect().run(
        CreateRectArgs(
            page_id="p1", widget_id="r1", position=(0, 0), size=(10, 10), style={"color": "red"}
        ),
        fresh_world,
    )
    r = SetWidgetStyle().run(
        SetWidgetStyleArgs(page_id="p1", widget_id="r1", style={"line_width": 3}),
        fresh_world,
    )
    assert r.ok
    style = fresh_world.pages["p1"].widgets["r1"].style
    assert style == {"color": "red", "line_width": 3}


def test_delete_widget(fresh_world: MockWorld):
    _seed_page(fresh_world)
    CreateRect().run(
        CreateRectArgs(page_id="p1", widget_id="r1", position=(0, 0), size=(10, 10)),
        fresh_world,
    )
    r = DeleteWidget().run(
        DeleteWidgetArgs(page_id="p1", widget_id="r1"), fresh_world
    )
    assert r.ok and "r1" not in fresh_world.pages["p1"].widgets
    r2 = DeleteWidget().run(
        DeleteWidgetArgs(page_id="p1", widget_id="r1"), fresh_world
    )
    assert r2.error_code == ErrorCode.WIDGET_NOT_FOUND


# ============================================================ history
def test_enable_history_happy_then_disable(chemical_world: MockWorld):
    r = EnableHistory().run(EnableHistoryArgs(tag="TEMP_101"), chemical_world)
    assert r.ok and chemical_world.histories["TEMP_101"].enabled
    r2 = DisableHistory().run(DisableHistoryArgs(tag="TEMP_101"), chemical_world)
    assert r2.ok and not chemical_world.histories["TEMP_101"].enabled


def test_enable_history_point_not_found(fresh_world: MockWorld):
    r = EnableHistory().run(EnableHistoryArgs(tag="NOPE"), fresh_world)
    assert r.error_code == ErrorCode.POINT_NOT_FOUND


def test_set_retention(chemical_world: MockWorld):
    EnableHistory().run(EnableHistoryArgs(tag="TEMP_101"), chemical_world)
    r = SetRetention().run(SetRetentionArgs(tag="TEMP_101", retention_days=180), chemical_world)
    assert r.ok and chemical_world.histories["TEMP_101"].retention_days == 180


def test_query_history_returns_samples(chemical_world: MockWorld):
    EnableHistory().run(EnableHistoryArgs(tag="TEMP_101", sample_interval_s=1.0), chemical_world)
    r = QueryHistory().run(
        QueryHistoryArgs(tag="TEMP_101", window_s=10.0, max_samples=5),
        chemical_world,
    )
    assert r.ok and r.data["count"] == 5 and len(r.data["samples"]) == 5


def test_query_history_disabled(chemical_world: MockWorld):
    EnableHistory().run(EnableHistoryArgs(tag="TEMP_101"), chemical_world)
    DisableHistory().run(DisableHistoryArgs(tag="TEMP_101"), chemical_world)
    r = QueryHistory().run(QueryHistoryArgs(tag="TEMP_101"), chemical_world)
    assert r.error_code == ErrorCode.BUSINESS_RULE


def test_list_history_filter(chemical_world: MockWorld):
    EnableHistory().run(EnableHistoryArgs(tag="TEMP_101"), chemical_world)
    EnableHistory().run(EnableHistoryArgs(tag="TEMP_102"), chemical_world)
    DisableHistory().run(DisableHistoryArgs(tag="TEMP_102"), chemical_world)
    all_ = ListHistory().run(ListHistoryArgs(), chemical_world)
    enabled = ListHistory().run(ListHistoryArgs(enabled_only=True), chemical_world)
    assert all_.data["count"] == 2
    assert enabled.data["count"] == 1


# ============================================================ scripts
def test_create_script_requires_bound_tag_for_on_change():
    with pytest.raises(Exception):
        CreateScriptArgs(id="s1", name="X", trigger="on_change")


def test_create_script_periodic_requires_period():
    with pytest.raises(Exception):
        CreateScriptArgs(id="s1", name="X", trigger="periodic")


def test_create_script_happy(chemical_world: MockWorld):
    r = CreateScript().run(
        CreateScriptArgs(
            id="s1", name="OnTemp", trigger="on_change", bound_tag="TEMP_101", body="print(1)"
        ),
        chemical_world,
    )
    assert r.ok and "s1" in chemical_world.scripts


def test_create_script_unknown_tag(chemical_world: MockWorld):
    r = CreateScript().run(
        CreateScriptArgs(id="s1", name="X", trigger="on_change", bound_tag="NOPE"),
        chemical_world,
    )
    assert r.error_code == ErrorCode.POINT_NOT_FOUND


def test_create_script_on_alarm_requires_alarm(chemical_world: MockWorld):
    r = CreateScript().run(
        CreateScriptArgs(
            id="s1", name="X", trigger="on_alarm", bound_tag="TEMP_101", body=""
        ),
        chemical_world,
    )
    assert r.error_code == ErrorCode.BUSINESS_RULE
    # Now create the alarm and the on_alarm script becomes legal
    CreateAnalogAlarm().run(
        CreateAnalogAlarmArgs(id="a1", tag="TEMP_101", high_limit=80), chemical_world
    )
    r2 = CreateScript().run(
        CreateScriptArgs(
            id="s2", name="X", trigger="on_alarm", bound_tag="TEMP_101", body=""
        ),
        chemical_world,
    )
    assert r2.ok


def test_update_disable_enable_delete_script(chemical_world: MockWorld):
    CreateScript().run(
        CreateScriptArgs(id="s1", name="A", trigger="periodic", period_s=2.0, body="x"),
        chemical_world,
    )
    assert UpdateScriptBody().run(UpdateScriptBodyArgs(id="s1", body="y"), chemical_world).ok
    assert chemical_world.scripts["s1"].body == "y"
    assert DisableScript().run(DisableScriptArgs(id="s1"), chemical_world).ok
    assert not chemical_world.scripts["s1"].enabled
    assert EnableScript().run(EnableScriptArgs(id="s1"), chemical_world).ok
    assert DeleteScript().run(DeleteScriptArgs(id="s1"), chemical_world).ok
    assert "s1" not in chemical_world.scripts


def test_list_scripts_filter(chemical_world: MockWorld):
    CreateScript().run(
        CreateScriptArgs(id="s1", name="A", trigger="periodic", period_s=1.0), chemical_world
    )
    CreateScript().run(
        CreateScriptArgs(id="s2", name="B", trigger="on_change", bound_tag="TEMP_101"),
        chemical_world,
    )
    r = ListScripts().run(ListScriptsArgs(trigger="periodic"), chemical_world)
    assert r.ok and r.data["count"] == 1


# ============================================================ deployment
def _seed_consistent_project(world: MockWorld) -> None:
    """alarm + page + history + script that all reference existing entities."""
    CreateAnalogAlarm().run(
        CreateAnalogAlarmArgs(id="a1", tag="TEMP_101", high_limit=80), world
    )
    EnableHistory().run(EnableHistoryArgs(tag="TEMP_101"), world)
    CreateScript().run(
        CreateScriptArgs(id="s1", name="X", trigger="periodic", period_s=1.0), world
    )


def test_validate_clean_project(chemical_world: MockWorld):
    _seed_consistent_project(chemical_world)
    r = ValidateProject().run(ValidateProjectArgs(deployment_id="d1"), chemical_world)
    assert r.ok and r.data["errors"] == []
    assert chemical_world.deployments["d1"].status == "validated"


def test_validate_catches_dangling_history(chemical_world: MockWorld):
    chemical_world.histories["BOGUS"] = chemical_world.histories.get("BOGUS") or _bogus_hist()
    r = ValidateProject().run(ValidateProjectArgs(deployment_id="d1"), chemical_world)
    assert r.ok and r.data["errors"]
    assert chemical_world.deployments["d1"].status == "failed"


def _bogus_hist():
    from world.models import HistoryConfig

    return HistoryConfig(tag="BOGUS_TAG", enabled=True)


def test_deploy_requires_validation(chemical_world: MockWorld):
    r = DeployProject().run(DeployProjectArgs(deployment_id="dx"), chemical_world)
    assert r.error_code == ErrorCode.BUSINESS_RULE


def test_deploy_after_clean_validation(chemical_world: MockWorld):
    _seed_consistent_project(chemical_world)
    ValidateProject().run(ValidateProjectArgs(deployment_id="d1"), chemical_world)
    r = DeployProject().run(DeployProjectArgs(deployment_id="d1"), chemical_world)
    assert r.ok and chemical_world.deployments["d1"].status == "deployed"


def test_deploy_after_failed_validation_blocked(chemical_world: MockWorld):
    chemical_world.histories["BOGUS"] = _bogus_hist()
    ValidateProject().run(ValidateProjectArgs(deployment_id="d1"), chemical_world)
    r = DeployProject().run(DeployProjectArgs(deployment_id="d1"), chemical_world)
    assert r.error_code == ErrorCode.BUSINESS_RULE


def test_deploy_force_overrides(chemical_world: MockWorld):
    r = DeployProject().run(
        DeployProjectArgs(deployment_id="d1", force=True), chemical_world
    )
    assert r.ok


def test_rollback(chemical_world: MockWorld):
    _seed_consistent_project(chemical_world)
    ValidateProject().run(ValidateProjectArgs(deployment_id="d1"), chemical_world)
    DeployProject().run(DeployProjectArgs(deployment_id="d1"), chemical_world)
    r = RollbackDeployment().run(
        RollbackDeploymentArgs(deployment_id="d1", notes="bad config"), chemical_world
    )
    assert r.ok and chemical_world.deployments["d1"].status == "draft"


def test_rollback_missing_record(chemical_world: MockWorld):
    r = RollbackDeployment().run(
        RollbackDeploymentArgs(deployment_id="nope"), chemical_world
    )
    assert r.error_code == ErrorCode.BUSINESS_RULE


def test_show_deployment_status_default(chemical_world: MockWorld):
    r = ShowDeploymentStatus().run(
        ShowDeploymentStatusArgs(deployment_id="never_validated"), chemical_world
    )
    assert r.ok and r.data["status"] == "draft"


# ============================================================ metadata coverage
def test_graphics_intended_referenced_static():
    args = CreateRectArgs(page_id="p1", widget_id="r1", position=(0, 0), size=(1, 1))
    assert CreateRect.intended_entities(args) == ["pages.p1.widgets.r1"]
    assert CreateRect.referenced_entities(args) == ["pages.p1"]


def test_history_intended_referenced_static():
    args = EnableHistoryArgs(tag="TEMP_101")
    assert EnableHistory.intended_entities(args) == ["histories.TEMP_101"]
    assert EnableHistory.referenced_entities(args) == ["points.TEMP_101"]


def test_scripts_intended_referenced_static():
    args = CreateScriptArgs(
        id="s1", name="X", trigger="on_change", bound_tag="TEMP_101"
    )
    assert CreateScript.intended_entities(args) == ["scripts.s1"]
    assert CreateScript.referenced_entities(args) == ["points.TEMP_101"]


def test_deployment_intended_referenced_static():
    args = ValidateProjectArgs(deployment_id="d1")
    assert ValidateProject.intended_entities(args) == ["deployments.d1"]
    assert ValidateProject.referenced_entities(args) == []
