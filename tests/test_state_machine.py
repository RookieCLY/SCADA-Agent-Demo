"""State machine — transition legality + tool filtering."""
from __future__ import annotations

import pytest

from agent.state_machine import INITIAL_STATE, STATES, StateMachine


def test_initial_state_is_analyze_intent():
    sm = StateMachine()
    assert sm.current == INITIAL_STATE == "ANALYZE_INTENT"
    assert sm.history == ["ANALYZE_INTENT"]


def test_unknown_initial_raises():
    with pytest.raises(ValueError):
        StateMachine(current="NOPE")


def test_legal_transition():
    sm = StateMachine()
    sm.transit("CONFIG_ALARM")
    assert sm.current == "CONFIG_ALARM"
    assert sm.history[-1] == "CONFIG_ALARM"


def test_illegal_transition_raises():
    sm = StateMachine()
    sm.transit("CONFIG_ALARM")
    with pytest.raises(ValueError, match="illegal transition"):
        sm.transit("ANALYZE_INTENT")  # not allowed from CONFIG_ALARM


def test_terminal_state():
    sm = StateMachine()
    sm.transit("DONE")
    assert sm.is_terminal


def test_tool_filtering():
    sm = StateMachine()
    sm.transit("CONFIG_ALARM")
    out = sm.filter_tools(
        ["create_analog_alarm", "create_page", "list_points", "bind_point"]
    )
    # whitelist in CONFIG_ALARM contains create_analog_alarm + list_points, not the others
    assert "create_analog_alarm" in out
    assert "list_points" in out
    assert "create_page" not in out
    assert "bind_point" not in out


def test_every_state_has_a_spec():
    # sanity: catalogue completeness
    expected = {
        "ANALYZE_INTENT",
        "CONFIG_POINT",
        "MANAGE_PAGES",
        "BIND_POINTS",
        "CONFIG_ALARM",
        "VALIDATE",
        "ASK_USER",
        "DONE",
    }
    assert set(STATES.keys()) >= expected
