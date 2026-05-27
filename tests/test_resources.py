"""Resources layer — URI routing + read-only contract enforcement."""
from __future__ import annotations

import pytest

from resources import (
    FrozenWorld,
    ResourceNotFound,
    build_default_resource_registry,
)
from tools.manage_alarms import CreateAnalogAlarm, CreateAnalogAlarmArgs
from tools.manage_history import EnableHistory, EnableHistoryArgs
from tools.manage_pages import (
    CreatePage,
    CreatePageArgs,
    CreateWidget,
    CreateWidgetArgs,
)
from world import MockWorld


@pytest.fixture
def reg():
    return build_default_resource_registry()


def test_resource_registry_describe_for_llm(reg):
    desc = reg.describe_for_llm()
    assert len(desc) >= 9
    uris = {d["uri"] for d in desc}
    assert "scada://pages" in uris
    assert "scada://points" in uris
    assert "scada://history/{tag}" in uris


def test_list_pages(reg, chemical_world: MockWorld):
    CreatePage().run(CreatePageArgs(id="p1", name="P1"), chemical_world)
    out = reg.read("scada://pages", chemical_world)
    assert out["count"] == 1 and out["pages"][0]["id"] == "p1"


def test_get_page_not_found(reg, chemical_world: MockWorld):
    with pytest.raises(ResourceNotFound):
        reg.read("scada://pages/no_such", chemical_world)


def test_list_widgets(reg, chemical_world: MockWorld):
    CreatePage().run(CreatePageArgs(id="p1", name="P1"), chemical_world)
    CreateWidget().run(
        CreateWidgetArgs(
            page_id="p1", widget_id="w1", type="thermometer", position=(0, 0), size=(10, 10)
        ),
        chemical_world,
    )
    out = reg.read("scada://pages/p1/widgets", chemical_world)
    assert out["count"] == 1 and out["widgets"][0]["id"] == "w1"


def test_points_filter(reg, chemical_world: MockWorld):
    out_all = reg.read("scada://points", chemical_world)
    assert out_all["count"] >= 6
    out_filter = reg.read("scada://points?filter=TEMP", chemical_world)
    assert all("TEMP" in p["tag"] for p in out_filter["points"])
    assert out_filter["count"] < out_all["count"]


def test_point_by_tag(reg, chemical_world: MockWorld):
    out = reg.read("scada://points/TEMP_101", chemical_world)
    assert out["tag"] == "TEMP_101" and out["type"] == "analog"


def test_devices_and_filter(reg, chemical_world: MockWorld):
    out = reg.read("scada://devices", chemical_world)
    assert out["count"] >= 1
    out2 = reg.read("scada://devices?type=reactor", chemical_world)
    assert all(d["type"] == "reactor" for d in out2["devices"])


def test_alarms(reg, chemical_world: MockWorld):
    CreateAnalogAlarm().run(
        CreateAnalogAlarmArgs(id="a1", tag="TEMP_101", high_limit=80), chemical_world
    )
    out = reg.read("scada://alarms", chemical_world)
    assert out["count"] == 1


def test_history_config(reg, chemical_world: MockWorld):
    EnableHistory().run(EnableHistoryArgs(tag="TEMP_101"), chemical_world)
    out = reg.read("scada://history/TEMP_101", chemical_world)
    assert out["tag"] == "TEMP_101" and out["enabled"]


def test_history_not_found(reg, chemical_world: MockWorld):
    with pytest.raises(ResourceNotFound):
        reg.read("scada://history/NOPE", chemical_world)


def test_unknown_uri_raises(reg, chemical_world: MockWorld):
    with pytest.raises(ResourceNotFound):
        reg.read("scada://elsewhere", chemical_world)


def test_frozen_world_returns_copies(chemical_world: MockWorld):
    """The proxy must return *copies* so handlers cannot mutate the underlying world."""
    fw = FrozenWorld(chemical_world)
    points = fw.points
    points["TEMP_101"].unit = "tampered"
    # The underlying world is unaffected
    assert chemical_world.points["TEMP_101"].unit == "°C"


def test_deployment_default_status(reg, chemical_world: MockWorld):
    out = reg.read("scada://deployments/never_set", chemical_world)
    assert out["status"] == "draft" and out["errors"] == []
