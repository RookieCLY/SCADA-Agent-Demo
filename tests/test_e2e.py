"""End-to-end: D_minimal config + the "create high-temp alarm" task.

These tests exercise the Phase-1 happy path, which relies on MockLLM's
scripted regex outputs (the `next_state` field, specific tool arguments).
Real LLMs have no `next_state` channel so they can't drive the
state-machine-on D_minimal config; the tests are therefore pinned to
MockLLM via ``@pytest.mark.mock_only``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.orchestrator import assemble
from agent.tracer import Tracer
from world import MockWorld
from world.models import Point


@pytest.fixture
def patched_results(tmp_path: Path, monkeypatch) -> Path:
    """Redirect Tracer output to tmp_path/results."""
    out = tmp_path / "results"
    out.mkdir()

    orig_init = Tracer.__init__

    def patched(self, results_root, *args, **kwargs):
        return orig_init(self, out, *args, **kwargs)

    monkeypatch.setattr(Tracer, "__init__", patched)
    return out


@pytest.mark.mock_only
def test_e2e_create_alarm(patched_results: Path):
    agent = assemble("configs/D_minimal.yaml")
    # Seed world with the point the mock LLM will reference
    initial = MockWorld()
    initial.points["TEMP_101"] = Point(tag="TEMP_101", type="analog", unit="°C")
    record = agent.run(
        "给反应釜1加个高温报警,超过80度告警",
        initial_world=initial,
        golden_id="e2e-001",
        complexity="simple",
        domain="alarm",
    )
    assert record["execution"]["terminal_state"] == "DONE"
    assert record["execution"]["total_turns"] >= 1
    assert any(c["selected"] == "manage_alarms" for c in record["tool_calls"])
    tool_call = next(c for c in record["tool_calls"] if c["selected"] == "manage_alarms")
    assert tool_call["result_ok"] is True
    assert tool_call["error_code"] == "OK"
    assert tool_call["action"] == "create_analog_alarm"
    assert tool_call["intended_entities"]
    assert tool_call["referenced_entities"]
    assert record["world_snapshots"]["initial_hash"] != record["world_snapshots"]["final_hash"]


@pytest.mark.mock_only
def test_trace_jsonl_is_valid_json(patched_results: Path):
    agent = assemble("configs/D_minimal.yaml")
    initial = MockWorld()
    initial.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    agent.run(
        "给反应釜1加个高温报警,超过80度告警",
        initial_world=initial,
        golden_id="e2e-002",
    )
    text = agent.tracer.traces_path.read_text(encoding="utf-8").strip()
    lines = text.split("\n")
    assert len(lines) >= 1
    for ln in lines:
        json.loads(ln)  # must parse


@pytest.mark.mock_only
def test_e2e_unknown_query_terminates_cleanly(patched_results: Path):
    agent = assemble("configs/D_minimal.yaml")
    record = agent.run("xyz random unmapped query", golden_id="e2e-003")
    # mock LLM falls through → end_turn, agent terminates without crashing
    assert record["execution"]["total_turns"] >= 1
    assert record["execution"]["terminal_state"] in ("ANALYZE_INTENT", "DONE")


@pytest.mark.mock_only
def test_e2e_blocked_when_state_off_path(patched_results: Path, monkeypatch):
    """If we force the state machine to a state that does not whitelist
    `create_analog_alarm` then the orchestrator must flag OUT_OF_SCOPE rather
    than running the call against the world.
    """
    agent = assemble("configs/D_minimal.yaml")

    # Bypass the mock LLM's intent step by registering a stricter script: emit
    # a tool call immediately from ANALYZE_INTENT.
    from agent.llm import MockLLM

    orig_call = MockLLM.call

    def stub_call(self, system_prompt, user_query, visible_tools, history, state):
        if user_query == "FORCE":
            from agent.llm import LLMResponse, LLMToolCall

            return LLMResponse(
                text=None,
                tool_calls=[
                    LLMToolCall(
                        name="manage_alarms",
                        arguments={
                            "action": "create_analog_alarm",
                            "id": "x",
                            "tag": "TEMP_101",
                            "high_limit": 80,
                        },
                    )
                ],
                stop_reason="tool_use",
            )
        return orig_call(self, system_prompt, user_query, visible_tools, history, state)

    monkeypatch.setattr(MockLLM, "call", stub_call)

    initial = MockWorld()
    initial.points["TEMP_101"] = Point(tag="TEMP_101", type="analog")
    record = agent.run("FORCE", initial_world=initial, golden_id="e2e-004")
    bad = [c for c in record["tool_calls"] if c["error_code"] == "OUT_OF_SCOPE"]
    assert bad, "state machine should have blocked the call in ANALYZE_INTENT"
