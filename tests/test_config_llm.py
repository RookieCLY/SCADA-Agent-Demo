"""Config loader + LLM client (mock)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import load_config
from agent.llm import MockLLM, build_llm


def test_load_d_minimal():
    cfg = load_config(Path("configs/D_minimal.yaml"))
    assert cfg.name == "D_minimal"
    assert cfg.architecture.hierarchical_tools is True
    assert cfg.architecture.state_machine.enabled is True
    assert cfg.model.provider == "mock"


def test_load_a_baseline():
    cfg = load_config(Path("configs/A_flat_baseline.yaml"))
    assert cfg.architecture.hierarchical_tools is False
    assert cfg.architecture.state_machine.enabled is False


@pytest.mark.mock_only
def test_build_llm_mock():
    # mock_only opts out of the auto-upgrade fixture so build_llm() returns
    # the real MockLLM instance instead of being swapped to xiaomi-mimo.
    cfg = load_config(Path("configs/D_minimal.yaml"))
    llm = build_llm(cfg.model)
    assert isinstance(llm, MockLLM)


def test_mock_llm_default_falls_through():
    # Direct MockLLM instantiation — unaffected by the build_llm patch and
    # therefore valid in both mock and real-LLM test modes.
    llm = MockLLM()
    resp = llm.call(
        system_prompt="",
        user_query="random question unrelated to alarms",
        visible_tools=[],
        history=[],
        state="ANALYZE_INTENT",
    )
    assert resp.stop_reason == "end_turn"
    assert not resp.tool_calls


def test_mock_llm_high_temp_alarm_script():
    llm = MockLLM()
    resp = llm.call(
        system_prompt="",
        user_query="给反应釜1加个高温报警,超过80度告警",
        visible_tools=[],
        history=[],
        state="ANALYZE_INTENT",
    )
    assert resp.next_state == "CONFIG_ALARM"

    resp2 = llm.call(
        system_prompt="",
        user_query="给反应釜1加个高温报警,超过80度告警",
        visible_tools=[],
        history=[],
        state="CONFIG_ALARM",
    )
    assert resp2.tool_calls and resp2.tool_calls[0].name == "manage_alarms"
    args = resp2.tool_calls[0].arguments
    assert args["action"] == "create_analog_alarm"
    assert args["high_limit"] == 80
