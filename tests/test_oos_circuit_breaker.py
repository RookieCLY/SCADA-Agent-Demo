"""Out-of-scope feedback quality and the thrash circuit breaker.

Motivation: the H3 experiment measured the out-of-scope rate going **up** when
the state machine was enabled (D→E: 1.20% → 13.60% on DeepSeek, 9.00% → 9.60%
on Mimo). The state machine was blocking calls correctly; the problem was the
feedback. A bare "tool not in whitelist for state X" tells the model that it
failed but not what to do about it, so it retried the same call until the turn
budget ran out. These tests pin the two fixes: actionable guidance, and a
bounded number of retries.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import (
    ArchitectureConfig,
    ExperimentConfig,
    StateMachineConfig,
)
from agent.llm import LLMResponse, LLMToolCall
from agent.orchestrator import Agent
from agent.state_machine import STATES
from agent.tool_registry import build_default_registry
from agent.tracer import Tracer
from world import MockWorld, Point

from tests._llm_factory import make_test_model_config

ALARM_ARGS = {"id": "alarm_hi", "tag": "TEMP_101", "high_limit": 80.0}


class _StubbornLLM:
    """Always requests the same tool, whatever it is told. Models really do
    this — that is the behaviour the circuit breaker exists to bound."""

    def __init__(self, name: str, args: dict) -> None:
        self.name = name
        self.args = args
        self.calls = 0

    def call(self, system_prompt, user_query, visible_tools, history, state):
        self.calls += 1
        return LLMResponse(
            text=None,
            tool_calls=[LLMToolCall(name=self.name, arguments=dict(self.args))],
            stop_reason="tool_use",
        )

    def reset(self) -> None:
        return None


def _agent(tmp_path: Path, sm_cfg: StateMachineConfig, llm) -> Agent:
    cfg = ExperimentConfig(
        name="oos_test",
        architecture=ArchitectureConfig(
            hierarchical_tools=False, state_machine=sm_cfg
        ),
        model=make_test_model_config(force_mock=True),
    )
    tracer = Tracer(
        results_root=str(tmp_path), config_name=cfg.name, model_name="stubborn"
    )
    return Agent(
        config=cfg,
        registry=build_default_registry(),
        llm=llm,
        tracer=tracer,
        max_turns=10,
    )


def _world() -> MockWorld:
    w = MockWorld()
    w.points["TEMP_101"] = Point(tag="TEMP_101", type="analog", unit="C")
    return w


# ============================================================ escape hatch
def test_every_working_state_can_reach_ask_user():
    """The breaker diverts to ASK_USER, so ASK_USER must be universally
    reachable — §4.4.3 lists a failure-triggered fallback as a first-class
    transition. DONE stays terminal and ASK_USER keeps its own edges."""
    for name, spec in STATES.items():
        if spec.terminal or name == "ASK_USER":
            continue
        assert "ASK_USER" in spec.next_states, f"{name} cannot bail out"
    assert STATES["DONE"].terminal
    assert not STATES["DONE"].next_states
    assert STATES["ASK_USER"].next_states == frozenset({"ANALYZE_INTENT", "DONE"})


# ============================================================ guidance
@pytest.mark.mock_only
def test_blocked_call_names_the_state_that_owns_the_tool(tmp_path: Path):
    agent = _agent(
        tmp_path,
        StateMachineConfig(enabled=True, oos_guidance=True, oos_repeat_limit=3),
        _StubbornLLM("create_analog_alarm", ALARM_ARGS),
    )
    record = agent.run("加个高温报警", golden_id="oos-guidance", initial_world=_world())
    blocked = [c for c in record["tool_calls"] if c["error_code"] == "OUT_OF_SCOPE"]
    assert blocked
    msg = blocked[0]["error_msg"]
    # ANALYZE_INTENT does not expose alarm tools; CONFIG_ALARM does, and it is
    # reachable — so the model is told exactly how to get there.
    assert "CONFIG_ALARM" in msg
    assert "next_state" in msg


@pytest.mark.mock_only
def test_guidance_can_be_switched_off(tmp_path: Path):
    """The pre-fix message is preserved behind a flag so the old behaviour
    remains reproducible for an A/B against the archived results."""
    agent = _agent(
        tmp_path,
        StateMachineConfig(enabled=True, oos_guidance=False, oos_repeat_limit=0),
        _StubbornLLM("create_analog_alarm", ALARM_ARGS),
    )
    record = agent.run("加个高温报警", golden_id="oos-bare", initial_world=_world())
    blocked = [c for c in record["tool_calls"] if c["error_code"] == "OUT_OF_SCOPE"]
    assert blocked
    assert blocked[0]["error_msg"] == "tool not in whitelist for state ANALYZE_INTENT"


# ============================================================ circuit breaker
@pytest.mark.mock_only
def test_breaker_bounds_the_thrash_loop(tmp_path: Path):
    agent = _agent(
        tmp_path,
        StateMachineConfig(enabled=True, oos_guidance=True, oos_repeat_limit=2),
        _StubbornLLM("create_analog_alarm", ALARM_ARGS),
    )
    record = agent.run("加个高温报警", golden_id="oos-breaker", initial_world=_world())
    blocked = [c for c in record["tool_calls"] if c["error_code"] == "OUT_OF_SCOPE"]

    # Without the breaker this model would emit one blocked call per turn for
    # all 10 turns. With it, the run is cut short.
    assert len(blocked) <= 3, f"breaker did not bound retries: {len(blocked)}"
    assert record["execution"]["total_turns"] <= 4
    assert any("熔断" in (c["error_msg"] or "") for c in blocked)
    assert "ASK_USER" in [s["name"] for s in record["states"]]
    assert record["execution"]["termination_reason"] == "oos_circuit_breaker"


@pytest.mark.mock_only
def test_breaker_disabled_by_zero_limit(tmp_path: Path):
    agent = _agent(
        tmp_path,
        StateMachineConfig(enabled=True, oos_guidance=True, oos_repeat_limit=0),
        _StubbornLLM("create_analog_alarm", ALARM_ARGS),
    )
    record = agent.run("加个高温报警", golden_id="oos-nobreak", initial_world=_world())
    blocked = [c for c in record["tool_calls"] if c["error_code"] == "OUT_OF_SCOPE"]
    assert len(blocked) == 10, "limit=0 must reproduce the unbounded legacy loop"
    assert "ASK_USER" not in [s["name"] for s in record["states"]]
