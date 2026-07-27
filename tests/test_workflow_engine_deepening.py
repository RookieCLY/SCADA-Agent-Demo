"""Workflow-engine deepening (§4.3): richer step types + real control flow.

The engine previously supported only ``llm_step`` / ``deterministic_step`` and
walked them in a straight line, so "the engine owns control flow" (§4.3.1) was
only half true. These tests cover the additions:

* ``tool_call_step``  — the engine dispatches a fixed tool, no LLM turn (§4.3.3)
* ``conditional_step`` — the engine branches on a predicate (§4.3.3)
* ``loop_step``       — bounded repeat of a single-step body (§4.3.3)
* ``depends_on``      — a DAG asserted at load (cycles raise) and enforced at
                        runtime (out-of-order prerequisites raise) (§4.3)
* LLM workflow entry selection behind ``workflow.selection: llm`` (§4.3.1)
"""
from __future__ import annotations

from pathlib import Path

import pytest

import workflows  # noqa: F401 — registers handlers + predicates at import time
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
from agent.workflow import (
    WorkflowCatalogue,
    WorkflowDef,
    WorkflowEngine,
    WorkflowError,
    register_predicate,
)
from tests._llm_factory import make_test_model_config
from world import MockWorld, Point


# ---------------------------------------------------------------- helpers
class _ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.turns = 0

    def call(self, system_prompt, user_query, visible_tools, history, state):
        self.turns += 1
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(text="done", tool_calls=[], stop_reason="end_turn")

    def reset(self):
        return None


def _tool(name, args):
    return LLMResponse(
        text=None,
        tool_calls=[LLMToolCall(name=name, arguments=args)],
        stop_reason="tool_use",
    )


def _wf(steps, *, name="DeepeningDemo", keywords=("deepening-demo",)):
    return WorkflowDef.model_validate(
        {
            "name": name,
            "trigger": {"keywords": list(keywords)},
            "steps": steps,
        }
    )


def _agent(tmp_path, catalogue, responses, *, selection="keyword", rollback=False):
    cfg = ExperimentConfig(
        name="wf_deepening",
        architecture=ArchitectureConfig(
            state_machine=StateMachineConfig(enabled=True),
            workflow=WorkflowConfig(
                enabled=True, mode="engine", selection=selection,
                rollback_on_failure=rollback,
            ),
        ),
        model=make_test_model_config(force_mock=True),
    )
    agent = Agent(
        config=cfg,
        registry=build_default_registry(),
        llm=_ScriptedLLM(responses),
        tracer=Tracer(results_root=str(tmp_path), config_name=cfg.name, model_name="s"),
        workflow_catalogue=catalogue,
        max_turns=10,
    )
    return agent


def _world():
    w = MockWorld()
    w.points["TEMP_101"] = Point(tag="TEMP_101", type="analog", unit="C")
    return w


# ================================================================ load-time
def test_depends_on_cycle_is_rejected_at_load():
    with pytest.raises(ValueError, match="cycle"):
        _wf(
            [
                {"id": "a", "type": "llm_step", "state": "CONFIG_ALARM",
                 "allowed_tools": ["create_analog_alarm"], "depends_on": ["b"]},
                {"id": "b", "type": "deterministic_step", "state": "VALIDATE",
                 "handler": "handlers.validate_project", "depends_on": ["a"]},
            ]
        )


def test_control_flow_loop_body_is_rejected_at_load():
    # A loop whose body is itself a control-flow step corrupts the shared
    # return stack (nested loops don't work) — must be rejected at load.
    with pytest.raises(ValueError, match="must be a plain step"):
        _wf(
            [
                {"id": "outer", "type": "loop_step", "state": "BIND_POINTS",
                 "predicate": "predicates.has_points", "body": "inner"},
                {"id": "inner", "type": "loop_step", "state": "BIND_POINTS",
                 "predicate": "predicates.has_points", "body": "leaf"},
                {"id": "leaf", "type": "llm_step", "state": "BIND_POINTS",
                 "allowed_tools": ["bind_point"]},
            ]
        )


def test_branch_target_must_exist():
    with pytest.raises(ValueError, match="if_true"):
        _wf(
            [
                {"id": "c", "type": "conditional_step", "state": "VALIDATE",
                 "predicate": "predicates.has_alarms", "if_true": "ghost"},
            ]
        )


# ================================================================ runtime deps
def test_out_of_order_dependency_raises_at_runtime():
    # No cycle (prep precedes worker), but prep is declared *after* worker, so a
    # linear walk lands on worker before prep has run — the engine must refuse.
    wf = _wf(
        [
            {"id": "gate", "type": "llm_step", "state": "CONFIG_ALARM",
             "allowed_tools": ["create_analog_alarm"]},
            {"id": "worker", "type": "deterministic_step", "state": "VALIDATE",
             "handler": "handlers.validate_project", "depends_on": ["prep"]},
            {"id": "prep", "type": "deterministic_step", "state": "VALIDATE",
             "handler": "handlers.validate_project"},
        ]
    )
    eng = WorkflowEngine(wf)
    st = eng.initial_state()
    with pytest.raises(WorkflowError, match="depends_on"):
        eng.advance(st, succeeded=True)  # gate done -> land on worker -> prep missing


# ================================================================ conditional
def test_conditional_branches_both_ways():
    register_predicate("test.flag", lambda world, ctx: bool(ctx.get("flag")))
    wf = _wf(
        [
            {"id": "c", "type": "conditional_step", "state": "VALIDATE",
             "predicate": "test.flag", "if_true": "yes", "if_false": "no"},
            {"id": "yes", "type": "llm_step", "state": "VALIDATE",
             "allowed_tools": ["validate_project"]},
            {"id": "no", "type": "llm_step", "state": "CONFIG_ALARM",
             "allowed_tools": ["create_analog_alarm"]},
        ]
    )
    eng = WorkflowEngine(wf)

    st = eng.initial_state()
    assert eng.resolve_conditional(st, {}, {"flag": True}) is True
    assert st.current_step_id == "yes"

    st = eng.initial_state()
    assert eng.resolve_conditional(st, {}, {"flag": False}) is False
    assert st.current_step_id == "no"


# ================================================================ loop
def test_loop_iterates_until_predicate_false():
    calls = {"n": 0}

    def _thrice(world, ctx):
        calls["n"] += 1
        return calls["n"] <= 3

    register_predicate("test.thrice", _thrice)
    wf = _wf(
        [
            {"id": "loop", "type": "loop_step", "state": "BIND_POINTS",
             "predicate": "test.thrice", "body": "body", "max_iterations": 100},
            {"id": "body", "type": "llm_step", "state": "BIND_POINTS",
             "allowed_tools": ["bind_point"]},
        ]
    )
    eng = WorkflowEngine(wf)
    st = eng.initial_state()

    iterations = 0
    while not st.finished and eng.is_llm_step(eng.current_step(st)) is False:
        step = eng.current_step(st)
        if step.type == "loop_step":
            entered = eng.resolve_loop(st, {}, {})
            if entered:
                iterations += 1
                # simulate the body running and completing
                eng.advance(st, succeeded=True)
        else:  # pragma: no cover
            break
    assert iterations == 3
    assert st.finished


def test_loop_respects_max_iterations():
    register_predicate("test.always", lambda world, ctx: True)
    wf = _wf(
        [
            {"id": "loop", "type": "loop_step", "state": "BIND_POINTS",
             "predicate": "test.always", "body": "body", "max_iterations": 2},
            {"id": "body", "type": "llm_step", "state": "BIND_POINTS",
             "allowed_tools": ["bind_point"]},
        ]
    )
    eng = WorkflowEngine(wf)
    st = eng.initial_state()
    entered = 0
    for _ in range(50):
        if st.finished:
            break
        if eng.current_step(st).type == "loop_step" and eng.resolve_loop(st, {}, {}):
            entered += 1
            eng.advance(st, succeeded=True)
    assert entered == 2, "max_iterations must bound an always-true predicate"


# ================================================================ orchestrator glue
@pytest.mark.mock_only
def test_engine_runs_conditional_and_tool_call_without_extra_llm_turns(tmp_path: Path):
    """The recipe: LLM creates the alarm, then the engine (no LLM) checks the
    world via a conditional and dispatches validate_project via a tool_call."""
    wf = _wf(
        [
            {"id": "s_alarm", "type": "llm_step", "state": "CONFIG_ALARM",
             "allowed_tools": ["create_analog_alarm"], "must_call_tool": True},
            {"id": "s_check", "type": "conditional_step", "state": "VALIDATE",
             "predicate": "predicates.has_alarms", "if_true": "s_validate",
             "if_false": "s_alarm"},
            {"id": "s_validate", "type": "tool_call_step", "state": "VALIDATE",
             "tool": "validate_project", "arguments": {}},
        ]
    )
    catalogue = WorkflowCatalogue([WorkflowEngine(wf)])
    agent = _agent(
        tmp_path,
        catalogue,
        [_tool("create_analog_alarm", {"id": "a1", "tag": "TEMP_101", "high_limit": 80.0})],
    )
    world = _world()
    record = agent.run("deepening-demo 给 TEMP_101 加高温报警", initial_world=world)

    assert record["workflow"]["selected_workflow"] == "DeepeningDemo"
    # The LLM never emitted validate_project — its presence proves the engine
    # dispatched the tool_call step itself.
    selected = [c["selected"] for c in record["tool_calls"]]
    assert "create_analog_alarm" in selected
    assert "validate_project" in selected
    assert "a1" in world.alarms
    assert record["workflow"]["finished"] is True
    # Only one LLM turn was needed; the conditional + tool_call were engine-driven.
    assert agent.llm.turns == 1


# ================================================================ LLM entry selection
def test_llm_entry_selection_prefers_model_choice(tmp_path: Path):
    wf = _wf(
        [
            {"id": "s", "type": "llm_step", "state": "CONFIG_ALARM",
             "allowed_tools": ["create_analog_alarm"]},
        ],
        keywords=("this-keyword-never-appears",),
    )
    catalogue = WorkflowCatalogue([WorkflowEngine(wf)])
    agent = _agent(tmp_path, catalogue, [], selection="llm")

    class _Router:
        def call(self, *a, **k):  # pragma: no cover — not exercised here
            return LLMResponse(text="", tool_calls=[], stop_reason="end_turn")

        def reset(self):
            return None

        def select_workflow(self, query, options):
            return "DeepeningDemo"

    agent.llm = _Router()
    # Keyword match would miss (trigger keyword absent), so a hit proves the LLM
    # router chose it.
    chosen = agent._pick_workflow("completely unrelated phrasing")
    assert chosen is not None and chosen.wf.name == "DeepeningDemo"


def test_llm_entry_selection_falls_back_to_keyword_on_abstain(tmp_path: Path):
    wf = _wf(
        [
            {"id": "s", "type": "llm_step", "state": "CONFIG_ALARM",
             "allowed_tools": ["create_analog_alarm"]},
        ],
        keywords=("deepening-demo",),
    )
    catalogue = WorkflowCatalogue([WorkflowEngine(wf)])
    agent = _agent(tmp_path, catalogue, [], selection="llm")

    class _Abstain:
        def reset(self):
            return None

        def select_workflow(self, query, options):
            return None  # abstain

    agent.llm = _Abstain()
    # Router abstains -> deterministic keyword match still selects on trigger.
    assert agent._pick_workflow("deepening-demo please") is not None
    assert agent._pick_workflow("nothing matches here") is None


# ================================================================ failure handling
def test_loop_body_failure_unwinds_loop_context():
    """A failure inside a loop body must abort the loop (drop the pending return
    edge + counter) so recovery goes to on_failure, not back into the loop."""
    register_predicate("test.always_true", lambda world, ctx: True)
    wf = _wf(
        [
            {"id": "loop", "type": "loop_step", "state": "BIND_POINTS",
             "predicate": "test.always_true", "body": "body"},
            {"id": "body", "type": "llm_step", "state": "BIND_POINTS",
             "allowed_tools": ["bind_point"], "on_failure": "recover"},
            {"id": "recover", "type": "llm_step", "state": "ASK_USER",
             "allowed_tools": ["create_analog_alarm"]},
        ]
    )
    eng = WorkflowEngine(wf)
    st = eng.initial_state()

    assert eng.resolve_loop(st, {}, {}) is True
    assert st.current_step_id == "body" and st.loop_return == ["loop"]

    eng.advance(st, succeeded=False)  # body fails
    assert st.loop_return == [], "stale return edge would re-enter the loop"
    assert st.loop_counters == {}, "loop counter must reset on abort"
    assert st.current_step_id == "recover", "should follow on_failure, not loop back"


@pytest.mark.mock_only
def test_unregistered_predicate_degrades_to_failed_step(tmp_path: Path):
    """A typo'd/unregistered predicate must record a failed step, not crash the
    whole run (parity with deterministic-handler error handling)."""
    wf = _wf(
        [
            {"id": "c", "type": "conditional_step", "state": "CONFIG_ALARM",
             "predicate": "predicates.does_not_exist", "if_true": "s", "if_false": "s"},
            {"id": "s", "type": "llm_step", "state": "CONFIG_ALARM",
             "allowed_tools": ["create_analog_alarm"]},
        ]
    )
    catalogue = WorkflowCatalogue([WorkflowEngine(wf)])
    agent = _agent(tmp_path, catalogue, [])  # scripted LLM ends the turn
    record = agent.run("deepening-demo bad predicate", initial_world=_world())

    # Run completed (no exception) and the bad conditional was recorded as a
    # failed step rather than propagating.
    assert record["workflow"]["failed_step"] == "c"
    assert any(
        c["selected"] == "workflow:c" and c["error_code"] == "BUSINESS_RULE"
        for c in record["tool_calls"]
    )


# ================================================================ Saga compensation (§4.3.4)
@pytest.mark.mock_only
def test_saga_runs_per_step_compensation_in_reverse(tmp_path: Path):
    """A step declares an inverse action; when a later step fails, the engine
    undoes only what actually ran — not a blanket world reset."""
    wf = _wf(
        [
            {"id": "s_alarm", "type": "llm_step", "state": "CONFIG_ALARM",
             "allowed_tools": ["create_analog_alarm"], "must_call_tool": True,
             "compensate": {"tool": "delete_alarm", "arguments": {"id": "sa1"}}},
            # This tool_call is missing required args -> SCHEMA_ERROR -> the step
            # fails, which triggers the Saga unwind of s_alarm.
            {"id": "s_bad", "type": "tool_call_step", "state": "VALIDATE",
             "tool": "create_analog_alarm", "arguments": {}},
        ]
    )
    catalogue = WorkflowCatalogue([WorkflowEngine(wf)])
    agent = _agent(
        tmp_path, catalogue,
        [_tool("create_analog_alarm", {"id": "sa1", "tag": "TEMP_101", "high_limit": 80.0})],
        rollback=True,
    )
    world = _world()
    record = agent.run("deepening-demo saga", initial_world=world)

    assert record["workflow"]["failed_step"] == "s_bad"
    assert record["workflow"]["rolled_back"] is True
    # The alarm was created, then compensated away.
    assert "sa1" not in world.alarms, "per-step compensation should delete the alarm"
    selected = [c["selected"] for c in record["tool_calls"]]
    assert "compensate:s_alarm" in selected, "expected a per-step compensation record"
    # Per-step Saga, not the coarse checkpoint restore.
    assert "workflow:__saga_rollback__" not in selected


@pytest.mark.mock_only
def test_saga_falls_back_to_checkpoint_when_no_compensation_declared(tmp_path: Path):
    """Without a declared inverse, rollback still restores the entry world
    (coarse fallback) — preserving the prior behaviour."""
    wf = _wf(
        [
            {"id": "s_alarm", "type": "llm_step", "state": "CONFIG_ALARM",
             "allowed_tools": ["create_analog_alarm"], "must_call_tool": True},
            {"id": "s_bad", "type": "tool_call_step", "state": "VALIDATE",
             "tool": "create_analog_alarm", "arguments": {}},
        ]
    )
    catalogue = WorkflowCatalogue([WorkflowEngine(wf)])
    agent = _agent(
        tmp_path, catalogue,
        [_tool("create_analog_alarm", {"id": "sa1", "tag": "TEMP_101", "high_limit": 80.0})],
        rollback=True,
    )
    world = _world()
    record = agent.run("deepening-demo saga-fallback", initial_world=world)

    assert record["workflow"]["rolled_back"] is True
    assert "sa1" not in world.alarms
    selected = [c["selected"] for c in record["tool_calls"]]
    assert "workflow:__saga_rollback__" in selected
    assert not any(s.startswith("compensate:") for s in selected)


# ================================================================ sub-workflow (§4.3.3)
@pytest.mark.mock_only
def test_sub_workflow_runs_inline_without_an_llm_turn(tmp_path: Path):
    child = _wf(
        [
            {"id": "c1", "type": "tool_call_step", "state": "CONFIG_ALARM",
             "tool": "create_analog_alarm",
             "arguments": {"action": "create_analog_alarm", "id": "child_alarm",
                           "tag": "TEMP_101", "high_limit": 90.0}},
        ],
        name="ChildProc", keywords=("child-proc-never",),
    )
    parent = _wf(
        [
            {"id": "p1", "type": "sub_workflow_step", "state": "CONFIG_ALARM",
             "workflow": "ChildProc"},
        ],
        name="ParentProc", keywords=("deepening-demo",),
    )
    catalogue = WorkflowCatalogue([WorkflowEngine(parent), WorkflowEngine(child)])
    agent = _agent(tmp_path, catalogue, [])  # no LLM turn should be needed
    world = _world()
    record = agent.run("deepening-demo parent", initial_world=world)

    assert record["workflow"]["selected_workflow"] == "ParentProc"
    assert record["workflow"]["finished"] is True
    assert "child_alarm" in world.alarms, "the sub-workflow's tool_call should have run"
    assert any(c["selected"] == "subworkflow:ChildProc" for c in record["tool_calls"])
    # The scripted LLM has no responses, so it cannot have created the alarm —
    # the engine did, during entry resolution. The one turn is only the agent's
    # terminal wrap-up after the workflow had already finished.
    assert agent.llm.turns <= 1


@pytest.mark.mock_only
def test_unknown_sub_workflow_degrades_to_failed_step(tmp_path: Path):
    parent = _wf(
        [
            {"id": "p1", "type": "sub_workflow_step", "state": "CONFIG_ALARM",
             "workflow": "NoSuchWorkflow"},
        ],
        keywords=("deepening-demo",),
    )
    catalogue = WorkflowCatalogue([WorkflowEngine(parent)])
    agent = _agent(tmp_path, catalogue, [])
    record = agent.run("deepening-demo missing child", initial_world=_world())
    assert record["workflow"]["failed_step"] == "p1"
    assert any(
        c["selected"] == "subworkflow:NoSuchWorkflow" and c["error_code"] == "BUSINESS_RULE"
        for c in record["tool_calls"]
    )


@pytest.mark.mock_only
def test_sub_workflow_compensations_merge_onto_parent(tmp_path: Path):
    """A child step's compensation is unwound when the *parent* later fails."""
    child = _wf(
        [
            {"id": "c1", "type": "tool_call_step", "state": "CONFIG_ALARM",
             "tool": "create_analog_alarm",
             "arguments": {"action": "create_analog_alarm", "id": "child_alarm",
                           "tag": "TEMP_101", "high_limit": 90.0},
             "compensate": {"tool": "delete_alarm", "arguments": {"id": "child_alarm"}}},
        ],
        name="ChildProc2", keywords=("child-proc-never",),
    )
    parent = _wf(
        [
            {"id": "p1", "type": "sub_workflow_step", "state": "CONFIG_ALARM",
             "workflow": "ChildProc2"},
            {"id": "p_bad", "type": "tool_call_step", "state": "VALIDATE",
             "tool": "create_analog_alarm", "arguments": {}},  # fails
        ],
        name="ParentProc2", keywords=("deepening-demo",),
    )
    catalogue = WorkflowCatalogue([WorkflowEngine(parent), WorkflowEngine(child)])
    agent = _agent(tmp_path, catalogue, [], rollback=True)
    world = _world()
    record = agent.run("deepening-demo parent-saga", initial_world=world)

    assert record["workflow"]["failed_step"] == "p_bad"
    assert record["workflow"]["rolled_back"] is True
    assert "child_alarm" not in world.alarms, "child compensation must run on parent failure"
    assert any(c["selected"] == "compensate:c1" for c in record["tool_calls"])
