"""Workflow ``engine`` mode and Saga compensation.

§4.3.1 of the paper is explicit that the LLM does not own a workflow: it is an
entry decision-maker, and once a workflow is running the engine owns sequencing
while the model is called back for *local* decisions only. The original
implementation did the opposite — the workflow merely intersected its per-step
``allowed_tools`` into the visibility pipeline while the model kept driving
turn-to-turn control flow. That adds constraint friction without ever relieving
the model of long-chain planning, which is the most likely reason configs D/E/F
underperformed the flat baseline A in the main ablation.

``mode: filter`` preserves the original behaviour so the two can be compared;
``mode: engine`` implements §4.3.1. ``rollback_on_failure`` adds the Saga
compensation of §4.3.4, whose absence §2.5(5) names as a distinct failure mode.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import (
    ArchitectureConfig,
    ExperimentConfig,
    StateMachineConfig,
    WorkflowConfig,
)
from agent.llm import LLMResponse, LLMToolCall
from agent.orchestrator import Agent
from agent.tool_registry import build_default_registry
from agent.tracer import Tracer
from agent.workflow import load_catalogue
from world import MockWorld, Point
from world.models import Page, Widget

# Registers the deterministic step handlers at import time. ``assemble()`` does
# this for us, but these tests construct the Agent directly — without it the
# validate step fails for the wrong reason (unregistered handler) and the saga
# assertions would pass vacuously.
import workflows  # noqa: F401

from tests._llm_factory import make_test_model_config

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"
ALARM_QUERY = "给 TEMP_101 加个高温报警"
ALARM_ARGS = {"id": "alarm_hi", "tag": "TEMP_101", "high_limit": 80.0}


class _ScriptedLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.last_system_prompt = ""

    def call(self, system_prompt, user_query, visible_tools, history, state):
        self.last_system_prompt = system_prompt
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(
            text="done", tool_calls=[], stop_reason="end_turn", next_state="DONE"
        )

    def reset(self) -> None:
        return None


def _tool(name: str, args: dict, next_state: str | None = None) -> LLMResponse:
    return LLMResponse(
        text=None,
        tool_calls=[LLMToolCall(name=name, arguments=args)],
        stop_reason="tool_use",
        next_state=next_state,
    )


def _talk(text: str, next_state: str | None = None) -> LLMResponse:
    return LLMResponse(
        text=text, tool_calls=[], stop_reason="end_turn", next_state=next_state
    )


def _agent(tmp_path: Path, wf_cfg: WorkflowConfig, responses: list[LLMResponse]):
    cfg = ExperimentConfig(
        name="wf_mode_test",
        architecture=ArchitectureConfig(
            hierarchical_tools=False,
            state_machine=StateMachineConfig(enabled=True),
            workflow=wf_cfg,
        ),
        model=make_test_model_config(force_mock=True),
    )
    llm = _ScriptedLLM(responses)
    tracer = Tracer(
        results_root=str(tmp_path), config_name=cfg.name, model_name="scripted"
    )
    agent = Agent(
        config=cfg,
        registry=build_default_registry(),
        llm=llm,
        tracer=tracer,
        workflow_catalogue=load_catalogue(WORKFLOWS_DIR),
        max_turns=10,
    )
    return agent, llm


def _world() -> MockWorld:
    w = MockWorld()
    w.points["TEMP_101"] = Point(tag="TEMP_101", type="analog", unit="C")
    return w


def _inconsistent_world() -> MockWorld:
    """Already violates a validation rule: a widget bound to a missing point.

    The alarm step will succeed and the workflow's deterministic ``validate``
    step will then fail — the realistic "got most of the way, then blew up"
    shape that leaves half-built configuration behind.
    """
    w = _world()
    w.pages["p1"] = Page(id="p1", name="main")
    w.pages["p1"].widgets["w1"] = Widget(
        id="w1",
        page_id="p1",
        type="label",
        position=(0, 0),
        size=(10, 10),
        bindings={"value": "GHOST_TAG"},
    )
    return w


# ============================================================ engine mode
@pytest.mark.mock_only
def test_engine_mode_ignores_llm_state_requests(tmp_path: Path):
    """The model asks to jump to DEPLOY mid-workflow; the engine must refuse."""
    agent, _ = _agent(
        tmp_path,
        WorkflowConfig(enabled=True, yaml_path=str(WORKFLOWS_DIR), mode="engine"),
        [
            _tool("create_analog_alarm", ALARM_ARGS, next_state="DEPLOY"),
            _talk("continuing"),
            _talk("continuing"),
        ],
    )
    record = agent.run(ALARM_QUERY, golden_id="wf-engine", initial_world=_world())

    assert record["workflow"]["selected_workflow"] == "AlarmConfig"
    assert record["workflow"]["mode"] == "engine"
    visited = [s["name"] for s in record["states"]]
    assert "DEPLOY" not in visited, f"engine let the LLM drive: {visited}"


@pytest.mark.mock_only
def test_filter_mode_still_lets_the_llm_drive(tmp_path: Path):
    """The legacy arm of the ablation must keep its original semantics."""
    agent, _ = _agent(
        tmp_path,
        WorkflowConfig(enabled=True, yaml_path=str(WORKFLOWS_DIR), mode="filter"),
        [
            _tool("create_analog_alarm", ALARM_ARGS, next_state="VALIDATE"),
            _talk("ok", next_state="DONE"),
        ],
    )
    record = agent.run(ALARM_QUERY, golden_id="wf-filter", initial_world=_world())
    assert record["workflow"]["mode"] == "filter"
    assert "VALIDATE" in [s["name"] for s in record["states"]]


@pytest.mark.mock_only
def test_engine_mode_scopes_the_prompt_to_one_step(tmp_path: Path):
    """The model should be given a local task, not the whole recipe."""
    agent, llm = _agent(
        tmp_path,
        WorkflowConfig(enabled=True, yaml_path=str(WORKFLOWS_DIR), mode="engine"),
        [_talk("thinking")],
    )
    agent.run(ALARM_QUERY, golden_id="wf-prompt", initial_world=_world())
    assert "本步骤任务" in llm.last_system_prompt
    assert "next_state 会被忽略" in llm.last_system_prompt


# ============================================================ saga
@pytest.mark.mock_only
def test_saga_rollback_restores_the_entry_checkpoint(tmp_path: Path):
    world = _inconsistent_world()
    before = world.hash()
    agent, _ = _agent(
        tmp_path,
        WorkflowConfig(
            enabled=True,
            yaml_path=str(WORKFLOWS_DIR),
            mode="filter",
            rollback_on_failure=True,
        ),
        [_tool("create_analog_alarm", ALARM_ARGS), _talk("stuck", next_state="DONE")],
    )
    record = agent.run(ALARM_QUERY, golden_id="wf-saga", initial_world=world)

    assert record["workflow"]["failed_step"], "expected the validate step to fail"
    assert record["workflow"]["rolled_back"] is True
    assert world.hash() == before, f"partial write left behind: {list(world.alarms)}"
    assert not world.alarms
    assert any(
        c["selected"] == "workflow:__saga_rollback__" for c in record["tool_calls"]
    )


@pytest.mark.mock_only
def test_without_rollback_partial_writes_survive(tmp_path: Path):
    """The control arm — this is the half-built configuration §2.5(5) warns
    about, and it is the default so archived results stay reproducible."""
    world = _inconsistent_world()
    agent, _ = _agent(
        tmp_path,
        WorkflowConfig(
            enabled=True,
            yaml_path=str(WORKFLOWS_DIR),
            mode="filter",
            rollback_on_failure=False,
        ),
        [_tool("create_analog_alarm", ALARM_ARGS), _talk("stuck", next_state="DONE")],
    )
    record = agent.run(ALARM_QUERY, golden_id="wf-nosaga", initial_world=world)

    assert world.alarms, "expected the partial write to remain"
    assert record["workflow"]["rolled_back"] is False
    assert not any(
        c["selected"] == "workflow:__saga_rollback__" for c in record["tool_calls"]
    )


@pytest.mark.mock_only
def test_successful_workflow_is_never_rolled_back(tmp_path: Path):
    world = _world()
    agent, _ = _agent(
        tmp_path,
        WorkflowConfig(
            enabled=True,
            yaml_path=str(WORKFLOWS_DIR),
            mode="filter",
            rollback_on_failure=True,
        ),
        [_tool("create_analog_alarm", ALARM_ARGS), _talk("ok", next_state="DONE")],
    )
    record = agent.run(ALARM_QUERY, golden_id="wf-ok", initial_world=world)
    assert record["workflow"]["failed_step"] is None
    assert record["workflow"]["rolled_back"] is False
    assert "alarm_hi" in world.alarms
