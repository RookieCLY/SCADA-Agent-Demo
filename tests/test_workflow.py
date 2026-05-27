"""Workflow Engine — YAML loading, triggers, stepping, error paths.

Also verifies the 7 workflow YAMLs ship with valid metadata and that every
``allowed_tools`` entry exists in the canonical Tool registry.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.tool_registry import build_default_registry
from agent.workflow import (
    LLMStep,
    WorkflowCatalogue,
    WorkflowEngine,
    WorkflowExecutionState,
    load_catalogue,
    load_workflow,
)
from world import MockWorld

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"


# ============================================================ YAML loading
def test_load_all_workflows():
    yamls = sorted(WORKFLOWS_DIR.glob("*.yaml"))
    assert len(yamls) >= 5, "Phase 2 needs ≥5 workflow YAMLs (see §3.4.2)"
    for path in yamls:
        wf = load_workflow(path)
        assert wf.name and wf.steps


def test_every_workflow_has_a_trigger():
    yamls = sorted(WORKFLOWS_DIR.glob("*.yaml"))
    for path in yamls:
        wf = load_workflow(path)
        assert wf.trigger.keywords or wf.trigger.regex, f"{path.name} lacks trigger"


def test_workflow_allowed_tools_exist_in_registry():
    """Every allowed_tools entry must be an atomic tool the registry knows."""
    registry = build_default_registry()
    known = {m.name for m in registry.all_atomics()}
    yamls = sorted(WORKFLOWS_DIR.glob("*.yaml"))
    for path in yamls:
        wf = load_workflow(path)
        for step in wf.steps:
            if isinstance(step, LLMStep):
                unknown = set(step.allowed_tools) - known
                assert not unknown, (
                    f"{path.name}.{step.id} references unknown tools {unknown}"
                )


def test_workflow_states_resolve():
    """Every step's state must be in the state-machine catalogue (workflow loader
    already enforces this, but we double-check at the data-set level)."""
    from agent.state_machine import STATES
    yamls = sorted(WORKFLOWS_DIR.glob("*.yaml"))
    for path in yamls:
        wf = load_workflow(path)
        for step in wf.steps:
            assert step.state in STATES, f"{path.name}.{step.id}: bad state {step.state}"


# ============================================================ engine basics
@pytest.fixture
def alarm_wf():
    return load_workflow(WORKFLOWS_DIR / "alarm_config.yaml")


def test_engine_initial_state(alarm_wf):
    eng = WorkflowEngine(alarm_wf)
    es = eng.initial_state()
    assert es.workflow_id == "AlarmConfig"
    assert es.current_step_id == alarm_wf.steps[0].id
    assert not es.finished


def test_engine_step_allowed_tools(alarm_wf):
    eng = WorkflowEngine(alarm_wf)
    es = eng.initial_state()
    tools = eng.step_allowed_tools(es)
    assert tools and "list_points" in tools


def test_engine_advance_happy(alarm_wf):
    eng = WorkflowEngine(alarm_wf)
    es = eng.initial_state()
    while not es.finished:
        eng.advance(es, succeeded=True)
    assert es.completed_steps == [s.id for s in alarm_wf.steps]
    assert es.failed_step is None


def test_engine_failure_routes_to_on_failure(tmp_path: Path):
    """A failure on a step with on_failure must route to that step."""
    yaml_text = """
name: TinyFlow
version: "1.0.0"
trigger:
  keywords: ["tiny"]
steps:
  - id: a
    type: llm_step
    state: ANALYZE_INTENT
    allowed_tools: [list_pages]
  - id: b
    type: llm_step
    state: BIND_POINTS
    depends_on: [a]
    allowed_tools: [bind_point]
    on_failure: a
"""
    path = tmp_path / "tiny.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    wf = load_workflow(path)
    eng = WorkflowEngine(wf)
    es = eng.initial_state()
    eng.advance(es, succeeded=True)  # complete a → cursor at b
    assert es.current_step_id == "b"
    eng.advance(es, succeeded=False)  # b fails, on_failure=a
    assert es.failed_step == "b"
    assert es.current_step_id == "a"
    assert not es.finished


def test_engine_failure_without_on_failure_ends(alarm_wf):
    eng = WorkflowEngine(alarm_wf)
    es = eng.initial_state()
    eng.advance(es, succeeded=False)  # first step has no on_failure
    assert es.finished
    assert es.failed_step is not None


# ============================================================ catalogue / matching
def test_catalogue_routes_chemical():
    cat = load_catalogue(WORKFLOWS_DIR)
    e = cat.select("帮我建一个化工反应釜监控画面")
    assert e is not None and e.wf.name == "ChemicalProductionScreen"


def test_catalogue_routes_alarm():
    cat = load_catalogue(WORKFLOWS_DIR)
    e = cat.select("给反应釜温度加个高温报警")
    # Both Chemical (keyword '反应釜') and AlarmConfig (keyword '报警') match.
    # The lower-priority (= preferred) one is AlarmConfig (priority=40).
    assert e is not None and e.wf.name == "AlarmConfig"


def test_catalogue_routes_history():
    cat = load_catalogue(WORKFLOWS_DIR)
    e = cat.select("拉一下温度最近一分钟的历史趋势")
    assert e is not None and e.wf.name == "HistoryQuery"


def test_catalogue_unknown_query():
    cat = load_catalogue(WORKFLOWS_DIR)
    e = cat.select("讲个故事")
    assert e is None


# ============================================================ deterministic handler
def test_validate_project_handler_clean(chemical_world: MockWorld):
    from workflows import handlers as wh  # noqa
    from agent.workflow import get_handler

    fn = get_handler("handlers.validate_project")
    # empty project is trivially consistent
    out = fn(chemical_world, {})
    assert out["ok"]


def test_validate_project_handler_dangling(chemical_world: MockWorld):
    from workflows import handlers as wh  # noqa
    from agent.workflow import get_handler
    from world.models import HistoryConfig

    chemical_world.histories["BOGUS"] = HistoryConfig(tag="BOGUS_TAG", enabled=True)
    fn = get_handler("handlers.validate_project")
    with pytest.raises(RuntimeError, match="validation failed"):
        fn(chemical_world, {})


# ============================================================ YAML errors surface early
def test_invalid_state_raises(tmp_path: Path):
    yaml_text = """
name: Bad
trigger:
  keywords: ["x"]
steps:
  - id: a
    type: llm_step
    state: NOT_A_STATE
    allowed_tools: [list_pages]
"""
    path = tmp_path / "bad.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(Exception):
        load_workflow(path)


def test_duplicate_step_id_raises(tmp_path: Path):
    yaml_text = """
name: Bad
trigger:
  keywords: ["x"]
steps:
  - id: a
    type: llm_step
    state: ANALYZE_INTENT
    allowed_tools: [list_pages]
  - id: a
    type: llm_step
    state: ANALYZE_INTENT
    allowed_tools: [list_points]
"""
    path = tmp_path / "bad.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(Exception, match="duplicate"):
        load_workflow(path)


def test_unknown_dependency_raises(tmp_path: Path):
    yaml_text = """
name: Bad
trigger:
  keywords: ["x"]
steps:
  - id: a
    type: llm_step
    state: ANALYZE_INTENT
    depends_on: [ghost]
    allowed_tools: [list_pages]
"""
    path = tmp_path / "bad.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(Exception, match="depends_on"):
        load_workflow(path)
