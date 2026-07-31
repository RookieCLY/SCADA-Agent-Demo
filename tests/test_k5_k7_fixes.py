"""Regression tests for the K5 and K7 fixes.

Each of these was a *silent* defect — the call succeeded, the trace looked clean,
and only the final world state was wrong — which is the class hardest to notice
if it comes back. Every test names the golden case that caught it.
"""
from __future__ import annotations

import pytest

from agent.planner import _validate_or_repair
from agent.tool_registry import build_default_registry
from world import Alarm, MockWorld, Page, Point


@pytest.fixture(scope="module")
def registry():
    return build_default_registry()


# ------------------------------------------------------------------ K5: hex coercion
@pytest.mark.parametrize(
    ("tool", "args", "field", "expected"),
    [
        # documented "Hex color or image reference" — golden-007 stored "white"
        ("set_page_background", {"page_id": "p1", "background": "white"}, "background", "#FFFFFF"),
        ("set_page_background", {"page_id": "p1", "background": "BLACK"}, "background", "#000000"),
        # hex *default* is the other signal that a field wants hex — golden-013
        ("create_page", {"id": "r", "name": "报表", "background": "black"}, "background", "#000000"),
        ("add_trend_pen", {"group_name": "g", "pen_id": "p", "tag": "T1", "color": "red"},
         "color", "#FF0000"),
    ],
)
def test_css_name_coerced_to_hex(registry, tool, args, field, expected):
    parsed = _validate_or_repair(registry.atomic(tool), dict(args))
    assert parsed is not None
    assert getattr(parsed, field) == expected


@pytest.mark.parametrize(
    ("tool", "args", "field", "expected"),
    [
        # documents "Hex or named color", so a name is a legal value here
        # note: group_id here, group_name on add_trend_pen — the two trend tools
        # genuinely disagree, which is why the pair is worth testing together
        ("set_trend_pen_color", {"group_id": "g", "tag": "T1", "color": "red"}, "color", "red"),
        # already hex, and an image reference, both pass through untouched
        ("set_page_background", {"page_id": "p1", "background": "#202020"},
         "background", "#202020"),
        ("set_page_background", {"page_id": "p1", "background": "assets/bg.png"},
         "background", "assets/bg.png"),
    ],
)
def test_values_that_must_not_be_coerced(registry, tool, args, field, expected):
    parsed = _validate_or_repair(registry.atomic(tool), dict(args))
    assert parsed is not None
    assert getattr(parsed, field) == expected


# ------------------------------------------------------------------ K5: packed-pair split
def test_resolution_pair_split_into_width_height(registry):
    """create_page spells it resolution=[w,h]; set_page_resolution wants scalars.

    golden-013 lost "把报表页大小设成4K" to a schema drop for exactly this.
    """
    parsed = _validate_or_repair(
        registry.atomic("set_page_resolution"),
        {"page_id": "report", "resolution": [3840, 2160]},
    )
    assert parsed is not None
    assert (parsed.width, parsed.height) == (3840, 2160)


def test_pair_split_does_not_fire_when_the_model_has_the_packed_field(registry):
    parsed = _validate_or_repair(
        registry.atomic("create_page"),
        {"id": "p", "name": "n", "resolution": [800, 600]},
    )
    assert parsed is not None
    assert tuple(parsed.resolution) == (800, 600)


# ------------------------------------------------------------------ K7: set_alarm_priority
def test_set_alarm_priority_actually_writes(registry):
    """It returned ok() and changed nothing; golden-022 / -043 could not pass.

    ``Alarm.priority`` exists and manage_alarms is not a declared-stub module, so
    a success here has to be a write.
    """
    world = MockWorld()
    world.points["T1"] = Point(tag="T1", type="analog")
    world.alarms["ALM1"] = Alarm(id="ALM1", tag="T1", type="analog",
                                 high_limit=80.0, low_limit=0.0, priority="medium")
    before = world.hash()

    meta = registry.atomic("set_alarm_priority")
    result = meta.handler.run(meta.args_model(alarm_id="ALM1", priority="high"), world)

    assert result.ok
    assert world.alarms["ALM1"].priority == "high"
    assert world.hash() != before, "succeeded without changing the world"
    assert result.world_diff["added_or_modified"] == {"alarms.ALM1.priority": "high"}


def test_set_alarm_priority_still_reports_a_missing_alarm(registry):
    world = MockWorld()
    meta = registry.atomic("set_alarm_priority")
    result = meta.handler.run(meta.args_model(alarm_id="NOPE", priority="high"), world)
    assert not result.ok
    assert result.error_code == "ALARM_NOT_FOUND"


# ------------------------------------------------------------------ K7: binding vocabulary
def test_bind_point_documents_the_property_vocabulary(registry):
    """The description is the only place the convention is stated, and it must
    reach the planning catalogue — ``property`` is required, so it does."""
    schema = registry.atomic("bind_point").args_model.model_json_schema()
    description = schema["properties"]["property"]["description"]
    assert "property" in schema["required"]
    for word in ("level", "command", "state", "value"):
        assert word in description


# ------------------------------------------------------------------ no silent no-ops
def test_mutating_alarm_tools_report_a_diff_when_they_succeed(registry):
    """A mutating tool that succeeds must say what it changed.

    Scoped to manage_alarms: ten modules declare themselves stubs that deliberately
    do not touch the world, and this is not one of them.
    """
    world = MockWorld()
    world.pages["p1"] = Page(id="p1", name="P1")
    world.points["T1"] = Point(tag="T1", type="analog")
    world.alarms["ALM1"] = Alarm(id="ALM1", tag="T1", type="analog",
                                 high_limit=80.0, low_limit=0.0, priority="medium")

    meta = registry.atomic("set_alarm_priority")
    result = meta.handler.run(meta.args_model(alarm_id="ALM1", priority="critical"), world)
    assert result.ok and result.world_diff, "ok() with no world_diff is a silent no-op"
