"""Domain Tool acceptance — happy path + each error code per action."""
from __future__ import annotations

from tools._base import ErrorCode
from tools.manage_alarms import (
    CreateAnalogAlarm,
    CreateAnalogAlarmArgs,
    CreateDigitalAlarm,
    CreateDigitalAlarmArgs,
    DeleteAlarm,
    DeleteAlarmArgs,
    DisableAlarm,
    DisableAlarmArgs,
    EnableAlarm,
    EnableAlarmArgs,
    SetThreshold,
    SetThresholdArgs,
)
from tools.manage_pages import (
    BindPoint,
    BindPointArgs,
    CreatePage,
    CreatePageArgs,
    CreateWidget,
    CreateWidgetArgs,
    DeletePage,
    DeletePageArgs,
    ListPages,
    ListPagesArgs,
    RenamePage,
    RenamePageArgs,
)
from tools.manage_points import (
    CreatePoint,
    CreatePointArgs,
    DeletePoint,
    DeletePointArgs,
    ListPoints,
    ListPointsArgs,
    UpdatePoint,
    UpdatePointArgs,
)
from world import MockWorld
from world.models import Point


# ============================================================ alarms
def test_create_analog_alarm_happy_path(chemical_world: MockWorld):
    r = CreateAnalogAlarm().run(
        CreateAnalogAlarmArgs(id="a1", tag="TEMP_101", high_limit=80), chemical_world
    )
    assert r.ok and "alarms.a1" in r.world_diff["added_or_modified"]
    assert chemical_world.alarms["a1"].high_limit == 80


def test_create_analog_alarm_point_not_found(chemical_world: MockWorld):
    r = CreateAnalogAlarm().run(
        CreateAnalogAlarmArgs(id="a1", tag="DOES_NOT_EXIST", high_limit=80), chemical_world
    )
    assert not r.ok and r.error_code == ErrorCode.POINT_NOT_FOUND


def test_create_analog_alarm_type_mismatch(chemical_world: MockWorld):
    r = CreateAnalogAlarm().run(
        CreateAnalogAlarmArgs(id="a1", tag="PUMP_101_RUN", high_limit=1), chemical_world
    )
    assert not r.ok and r.error_code == ErrorCode.TYPE_MISMATCH


def test_create_analog_alarm_already_exists(chemical_world: MockWorld):
    CreateAnalogAlarm().run(
        CreateAnalogAlarmArgs(id="a1", tag="TEMP_101", high_limit=80), chemical_world
    )
    r = CreateAnalogAlarm().run(
        CreateAnalogAlarmArgs(id="a1", tag="TEMP_102", high_limit=70), chemical_world
    )
    assert r.error_code == ErrorCode.ALREADY_EXISTS


def test_create_digital_alarm_type_mismatch(chemical_world: MockWorld):
    r = CreateDigitalAlarm().run(
        CreateDigitalAlarmArgs(id="d1", tag="TEMP_101"), chemical_world
    )
    assert r.error_code == ErrorCode.TYPE_MISMATCH


def test_create_digital_alarm_happy(chemical_world: MockWorld):
    r = CreateDigitalAlarm().run(
        CreateDigitalAlarmArgs(id="d1", tag="PUMP_101_RUN"), chemical_world
    )
    assert r.ok


def test_set_threshold_paths(chemical_world: MockWorld):
    CreateAnalogAlarm().run(
        CreateAnalogAlarmArgs(id="a1", tag="TEMP_101", high_limit=80), chemical_world
    )
    r = SetThreshold().run(SetThresholdArgs(id="a1", high_limit=85), chemical_world)
    assert r.ok and chemical_world.alarms["a1"].high_limit == 85
    r = SetThreshold().run(SetThresholdArgs(id="missing", high_limit=85), chemical_world)
    assert r.error_code == ErrorCode.ALARM_NOT_FOUND


def test_enable_disable_delete(chemical_world: MockWorld):
    CreateAnalogAlarm().run(
        CreateAnalogAlarmArgs(id="a1", tag="TEMP_101", high_limit=80), chemical_world
    )
    assert DisableAlarm().run(DisableAlarmArgs(id="a1"), chemical_world).ok
    assert not chemical_world.alarms["a1"].enabled
    assert EnableAlarm().run(EnableAlarmArgs(id="a1"), chemical_world).ok
    assert chemical_world.alarms["a1"].enabled
    assert DeleteAlarm().run(DeleteAlarmArgs(id="a1"), chemical_world).ok
    assert "a1" not in chemical_world.alarms


# ============================================================ points
def test_create_point_already_exists(chemical_world: MockWorld):
    r = CreatePoint().run(
        CreatePointArgs(tag="TEMP_101", type="analog"), chemical_world
    )
    assert r.error_code == ErrorCode.ALREADY_EXISTS


def test_update_point(chemical_world: MockWorld):
    r = UpdatePoint().run(UpdatePointArgs(tag="TEMP_101", min=0, max=200), chemical_world)
    assert r.ok and chemical_world.points["TEMP_101"].max == 200
    r = UpdatePoint().run(UpdatePointArgs(tag="MISSING", min=0), chemical_world)
    assert r.error_code == ErrorCode.POINT_NOT_FOUND


def test_delete_point_blocked_by_alarm(chemical_world: MockWorld):
    CreateAnalogAlarm().run(
        CreateAnalogAlarmArgs(id="a1", tag="TEMP_101", high_limit=80), chemical_world
    )
    r = DeletePoint().run(DeletePointArgs(tag="TEMP_101"), chemical_world)
    assert r.error_code == ErrorCode.BUSINESS_RULE


def test_delete_point_ok(chemical_world: MockWorld):
    r = DeletePoint().run(DeletePointArgs(tag="LEVEL_101"), chemical_world)
    assert r.ok


def test_list_points_filter(chemical_world: MockWorld):
    r = ListPoints().run(ListPointsArgs(type_filter="digital"), chemical_world)
    assert r.ok and r.data["count"] == 2  # PUMP_101_RUN + ALARM_LIGHT


# ============================================================ pages / widgets / binding
def _make_page_with_thermometer(world: MockWorld) -> None:
    CreatePage().run(CreatePageArgs(id="p1", name="Page 1"), world)
    CreateWidget().run(
        CreateWidgetArgs(
            page_id="p1",
            widget_id="w_thermo",
            type="thermometer",
            position=(100, 200),
            size=(80, 200),
            expected_binding_types={"value": ["analog"]},
        ),
        world,
    )


def test_create_page_and_widget(fresh_world: MockWorld):
    fresh_world.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    _make_page_with_thermometer(fresh_world)
    assert "p1" in fresh_world.pages and "w_thermo" in fresh_world.pages["p1"].widgets


def test_bind_point_happy(fresh_world: MockWorld):
    fresh_world.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    _make_page_with_thermometer(fresh_world)
    r = BindPoint().run(
        BindPointArgs(page_id="p1", widget_id="w_thermo", property="value", tag="TEMP_101"),
        fresh_world,
    )
    assert r.ok and fresh_world.pages["p1"].widgets["w_thermo"].bindings["value"] == "TEMP_101"


def test_bind_point_page_not_found(fresh_world: MockWorld):
    fresh_world.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    r = BindPoint().run(
        BindPointArgs(page_id="missing", widget_id="x", property="value", tag="TEMP_101"),
        fresh_world,
    )
    assert r.error_code == ErrorCode.PAGE_NOT_FOUND


def test_bind_point_widget_not_found(fresh_world: MockWorld):
    fresh_world.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    CreatePage().run(CreatePageArgs(id="p1", name="P1"), fresh_world)
    r = BindPoint().run(
        BindPointArgs(page_id="p1", widget_id="missing", property="value", tag="TEMP_101"),
        fresh_world,
    )
    assert r.error_code == ErrorCode.WIDGET_NOT_FOUND


def test_bind_point_type_mismatch(fresh_world: MockWorld):
    fresh_world.points["DI_1"] = Point(tag="DI_1", type="digital")
    _make_page_with_thermometer(fresh_world)
    r = BindPoint().run(
        BindPointArgs(page_id="p1", widget_id="w_thermo", property="value", tag="DI_1"),
        fresh_world,
    )
    assert r.error_code == ErrorCode.TYPE_MISMATCH


def test_bind_point_already_bound(fresh_world: MockWorld):
    fresh_world.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    fresh_world.points["TEMP_102"] = Point(tag="TEMP_102", type="analog")
    _make_page_with_thermometer(fresh_world)
    BindPoint().run(
        BindPointArgs(page_id="p1", widget_id="w_thermo", property="value", tag="TEMP_101"),
        fresh_world,
    )
    r = BindPoint().run(
        BindPointArgs(page_id="p1", widget_id="w_thermo", property="value", tag="TEMP_102"),
        fresh_world,
    )
    assert r.error_code == ErrorCode.ALREADY_BOUND


def test_rename_and_delete_page(fresh_world: MockWorld):
    CreatePage().run(CreatePageArgs(id="p1", name="X"), fresh_world)
    assert RenamePage().run(RenamePageArgs(id="p1", new_name="Y"), fresh_world).ok
    assert fresh_world.pages["p1"].name == "Y"
    assert DeletePage().run(DeletePageArgs(id="p1"), fresh_world).ok
    assert "p1" not in fresh_world.pages


def test_list_pages(fresh_world: MockWorld):
    CreatePage().run(CreatePageArgs(id="p1", name="X"), fresh_world)
    CreatePage().run(CreatePageArgs(id="p2", name="Y"), fresh_world)
    r = ListPages().run(ListPagesArgs(), fresh_world)
    assert r.ok and r.data["count"] == 2


# ============================================================ metadata methods
def test_intended_referenced_metadata():
    a = CreateAnalogAlarmArgs(id="alarm_1", tag="TEMP_101", high_limit=80)
    assert CreateAnalogAlarm.intended_entities(a) == ["alarms.alarm_1"]
    assert CreateAnalogAlarm.referenced_entities(a) == ["points.TEMP_101"]

    b = BindPointArgs(page_id="p1", widget_id="w1", property="value", tag="TEMP_101")
    assert BindPoint.intended_entities(b) == [
        "pages.p1.widgets.w1.bindings.value"
    ]
    assert "points.TEMP_101" in BindPoint.referenced_entities(b)
