"""world/ — Pydantic models + in-memory store."""
from __future__ import annotations

from world import Alarm, MockWorld, Page, Point, Widget


def test_world_empty_diff(fresh_world: MockWorld):
    other = MockWorld()
    d = fresh_world.diff(other)
    assert d["added_or_modified"] == {}
    assert d["removed"] == []


def test_world_diff_added(fresh_world: MockWorld):
    other = MockWorld()
    other.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    d = fresh_world.diff(other)
    keys = list(d["added_or_modified"].keys())
    assert any(k.startswith("points.TEMP_101") for k in keys)


def test_world_diff_modified():
    a = MockWorld()
    a.points["TEMP_101"] = Point(tag="TEMP_101", type="analog", unit="°C")
    b = MockWorld()
    b.points["TEMP_101"] = Point(tag="TEMP_101", type="analog", unit="°F")
    d = a.diff(b)
    flat = d["added_or_modified"]
    assert "points.TEMP_101.unit" in flat
    assert flat["points.TEMP_101.unit"] == "°F"


def test_world_diff_removed():
    a = MockWorld()
    a.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    b = MockWorld()
    d = a.diff(b)
    assert any("points.TEMP_101" in r for r in d["removed"])


def test_world_snapshot_restore():
    w = MockWorld()
    w.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    snap = w.snapshot()
    w.points["TEMP_102"] = Point(tag="TEMP_102", type="analog")
    w.restore(snap)
    assert "TEMP_102" not in w.points
    assert "TEMP_101" in w.points


def test_world_reset():
    w = MockWorld()
    w.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    w.alarms["a1"] = Alarm(id="a1", tag="TEMP_101", type="analog", high_limit=80)
    w.reset()
    assert w.points == {} and w.alarms == {}


def test_world_hash_is_deterministic():
    w1 = MockWorld()
    w1.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    w2 = MockWorld()
    w2.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    assert w1.hash() == w2.hash()


def test_world_match_subset():
    initial = MockWorld()
    initial.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    final = MockWorld()
    final.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    final.alarms["a1"] = Alarm(id="a1", tag="TEMP_101", type="analog", high_limit=80)
    expected = {
        "match_mode": "subset",
        "added_or_modified": {"alarms.a1.tag": "TEMP_101", "alarms.a1.high_limit": 80.0},
        "removed": [],
        "unchanged_keys_must_remain": ["points.TEMP_101.tag"],
    }
    matched, report = final.match_against_expected(expected, initial=initial)
    assert matched, report


def test_world_match_strict_rejects_extras():
    initial = MockWorld()
    final = MockWorld()
    final.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    final.points["TEMP_999"] = Point(tag="TEMP_999", type="analog")  # extra
    expected = {
        "match_mode": "strict",
        "added_or_modified": {
            "points.TEMP_101.tag": "TEMP_101",
            "points.TEMP_101.type": "analog",
        },
        "removed": [],
    }
    matched, report = final.match_against_expected(expected, initial=initial)
    assert not matched
    assert any("TEMP_999" in u for u in report.get("unexpected", []))
