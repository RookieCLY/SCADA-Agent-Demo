"""Phase-2 end-to-end smoke — flip each architecture flag and verify the
orchestrator wires the right components and produces the right trace fields.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent.orchestrator import Agent, assemble, build_demo_world
from agent.tool_rag import build_index_from_registry
from agent.tool_registry import build_default_registry
from agent.tracer import Tracer
from agent.llm import MockLLM
from agent.config import (
    ArchitectureConfig,
    ExperimentConfig,
    ModelConfig,
    StateMachineConfig,
    ToolRAGConfig,
    WorkflowConfig,
)
from agent.workflow import load_catalogue
from resources import build_default_resource_registry

from tests._llm_factory import make_test_llm, make_test_model_config


WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"


def _agent(
    arch: ArchitectureConfig,
    tmp_path: Path,
    *,
    with_index: bool = True,
    with_workflows: bool = True,
    with_resources: bool = True,
    force_mock: bool = False,
) -> Agent:
    """Build an Agent against either the real LLM or MockLLM.

    ``force_mock=True`` keeps MockLLM regardless of API key availability —
    used by tests that depend on MockLLM's scripted regex outputs.
    """
    model_cfg = make_test_model_config(force_mock=force_mock)
    cfg = ExperimentConfig(
        name="phase2_test",
        architecture=arch,
        model=model_cfg,
    )
    reg = build_default_registry()
    tracer = Tracer(
        results_root=str(tmp_path), config_name=cfg.name, model_name=model_cfg.name
    )
    return Agent(
        config=cfg,
        registry=reg,
        llm=make_test_llm(registry=reg, arch=arch, force_mock=force_mock),
        tracer=tracer,
        tool_index=build_index_from_registry(reg) if with_index else None,
        workflow_catalogue=load_catalogue(WORKFLOWS_DIR) if with_workflows else None,
        resource_registry=build_default_resource_registry() if with_resources else None,
    )


def _read_trace(tracer: Tracer) -> dict:
    line = tracer.traces_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    return json.loads(line)


# ============================================================ rag + state-machine
@pytest.mark.mock_only
def test_rag_enabled_shrinks_visible(tmp_path: Path):
    arch = ArchitectureConfig(
        hierarchical_tools=False,
        tool_rag=ToolRAGConfig(enabled=True, top_n=30, top_k=6),
        state_machine=StateMachineConfig(enabled=True),
    )
    # Assertion needs at least one tool_call record, which depends on the
    # mock script transitioning ANALYZE_INTENT → CONFIG_ALARM and emitting a
    # call there. Real LLM has no `next_state` channel so it stays in
    # ANALYZE_INTENT and may exit without calling any tool.
    agent = _agent(arch, tmp_path, force_mock=True)
    record = agent.run(
        "给反应釜温度加个高温报警",
        golden_id="g_alarm",
        initial_world=build_demo_world(),
    )
    visible_counts = [c["visible_count"] for c in record["tool_calls"]]
    # RAG cap of 6 + state-machine whitelist must keep the list narrow
    assert visible_counts and max(visible_counts) <= 6


@pytest.mark.mock_only
def test_state_machine_filters_alarm_tools_outside_alarm_state(tmp_path: Path):
    """Whitelist enforcement: if the LLM tries an alarm tool while still in
    ANALYZE_INTENT, the orchestrator records OUT_OF_SCOPE instead of executing it."""
    from agent.llm import LLMResponse, LLMToolCall, MockLLM

    class RogueMock(MockLLM):
        def call(self, *args, **kwargs):
            # Bypass the scripted intent → action transition and try the alarm
            # tool from the very first state.
            return LLMResponse(
                text=None,
                tool_calls=[
                    LLMToolCall(
                        name="create_analog_alarm",
                        arguments={"id": "a1", "tag": "TEMP_101", "high_limit": 80},
                    )
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=5,
                latency_ms=0.0,
            )

    arch = ArchitectureConfig(
        hierarchical_tools=False,
        state_machine=StateMachineConfig(enabled=True),
    )
    cfg = ExperimentConfig(
        name="rogue_test",
        architecture=arch,
        model=ModelConfig(provider="mock", name="mock"),
    )
    reg = build_default_registry()
    tracer = Tracer(results_root=str(tmp_path), config_name=cfg.name, model_name="mock")
    agent = Agent(config=cfg, registry=reg, llm=RogueMock(), tracer=tracer)
    record = agent.run(
        "anything",
        golden_id="g_rogue",
        initial_world=build_demo_world(),
    )
    error_codes = [c["error_code"] for c in record["tool_calls"]]
    assert "OUT_OF_SCOPE" in error_codes


# ============================================================ workflow
@pytest.mark.mock_only
def test_workflow_selects_chemical(tmp_path: Path):
    arch = ArchitectureConfig(
        hierarchical_tools=True,
        workflow=WorkflowConfig(enabled=True, yaml_path=str(WORKFLOWS_DIR)),
        state_machine=StateMachineConfig(enabled=True),
    )
    # Workflow selection is rules-based and happens before any LLM call —
    # the assertion is unaffected by which provider is wired in.
    agent = _agent(arch, tmp_path, with_index=False, with_resources=False, force_mock=True)
    record = agent.run(
        "帮我建一个化工反应釜监控画面",
        golden_id="g_chem",
        initial_world=build_demo_world(),
    )
    assert record["workflow"]["enabled"]
    assert record["workflow"]["selected_workflow"] == "ChemicalProductionScreen"


@pytest.mark.mock_only
def test_workflow_no_match_returns_none(tmp_path: Path):
    arch = ArchitectureConfig(
        workflow=WorkflowConfig(enabled=True, yaml_path=str(WORKFLOWS_DIR)),
        state_machine=StateMachineConfig(enabled=False),
    )
    agent = _agent(arch, tmp_path, with_index=False, with_resources=False, force_mock=True)
    record = agent.run(
        "讲个故事好不好",
        golden_id="g_none",
        initial_world=build_demo_world(),
    )
    assert record["workflow"]["selected_workflow"] is None


# ============================================================ resources
@pytest.mark.mock_only
def test_resources_separation_emits_resource_reads(tmp_path: Path):
    arch = ArchitectureConfig(
        hierarchical_tools=True,
        resources_separation=True,
        state_machine=StateMachineConfig(enabled=True),
    )
    agent = _agent(arch, tmp_path, with_index=False, with_workflows=False, force_mock=True)
    record = agent.run(
        "拉一下最近一分钟的温度历史",
        golden_id="g_hist",
        initial_world=build_demo_world(),
    )
    # The mock script issues a read_resource(scada://points?filter=TEMP) in ANALYZE_INTENT
    assert record["resource_reads"], "expected at least one resource_read entry"
    assert record["resource_reads"][0]["uri"] == "scada://points?filter=TEMP"
    assert record["resource_reads"][0]["found"]


@pytest.mark.mock_only
def test_resources_disabled_falls_back_to_error(tmp_path: Path):
    """If resources_separation is OFF but the LLM still tries read_resource, the
    orchestrator must record a non-found resource_read instead of crashing."""
    arch = ArchitectureConfig(
        hierarchical_tools=True,
        resources_separation=False,
        state_machine=StateMachineConfig(enabled=True),
    )
    agent = _agent(
        arch,
        tmp_path,
        with_index=False,
        with_workflows=False,
        with_resources=False,
        force_mock=True,
    )
    record = agent.run(
        "拉一下最近一分钟的温度历史",
        golden_id="g_hist2",
        initial_world=build_demo_world(),
    )
    # With resources disabled the read_resource call is rejected
    assert any(not r["found"] for r in record["resource_reads"])


# ============================================================ four-in-one (D-config)
@pytest.mark.mock_only
def test_four_in_one_alarm_e2e(tmp_path: Path):
    """All four levers on at once — query should still complete cleanly."""
    arch = ArchitectureConfig(
        hierarchical_tools=True,
        tool_rag=ToolRAGConfig(enabled=True, top_n=30, top_k=8),
        workflow=WorkflowConfig(enabled=True, yaml_path=str(WORKFLOWS_DIR)),
        state_machine=StateMachineConfig(enabled=True),
        resources_separation=True,
    )
    agent = _agent(arch, tmp_path, force_mock=True)
    record = agent.run(
        "给反应釜温度加个高温报警",
        golden_id="g_4in1",
        initial_world=build_demo_world(),
    )
    assert record["rag"]["enabled"] and record["workflow"]["enabled"]
    # The MockLLM emits a manage_alarms call with create_analog_alarm action
    actions = [c.get("action") for c in record["tool_calls"]]
    assert "create_analog_alarm" in actions


# ============================================================ assemble() smoke
def test_assemble_from_yaml(tmp_path: Path):
    """assemble() should wire the right components based on the YAML flags."""
    yaml_text = yaml.safe_dump(
        {
            "name": "D_test",
            "architecture": {
                "hierarchical_tools": True,
                "tool_rag": {"enabled": True, "top_k": 5},
                "workflow": {"enabled": True, "yaml_path": str(WORKFLOWS_DIR)},
                "state_machine": {"enabled": True},
                "resources_separation": True,
            },
            "model": {"provider": "mock", "name": "mock"},
        }
    )
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml_text, encoding="utf-8")
    a = assemble(cfg_path)
    assert a.tool_index is not None
    assert a.workflow_catalogue is not None and len(a.workflow_catalogue.all()) >= 5
    assert a.resource_registry is not None


@pytest.mark.mock_only
def test_full_f_config_alarm_path_with_deterministic_validation(tmp_path: Path):
    """The F config (all five levers on) must complete the alarm path end-to-end:
    AlarmConfig workflow → create_analog_alarm (OK) → deterministic
    validate_project (OK).
    """
    arch = ArchitectureConfig(
        hierarchical_tools=True,
        tool_rag=ToolRAGConfig(enabled=True, top_n=30, top_k=10),
        workflow=WorkflowConfig(enabled=True, yaml_path=str(WORKFLOWS_DIR)),
        state_machine=StateMachineConfig(enabled=True),
        resources_separation=True,
    )
    # The `workflows` package registers handlers at import time; in tests we
    # import it explicitly because we bypass the assemble() codepath.
    import workflows  # noqa: F401

    agent = _agent(arch, tmp_path, force_mock=True)
    record = agent.run(
        "给反应釜温度加个高温报警,超过80度告警",
        golden_id="full_f",
        initial_world=build_demo_world(),
    )
    assert record["execution"]["terminal_state"] == "DONE"
    assert record["workflow"]["selected_workflow"] == "AlarmConfig"
    # Expect both the alarm-creation tool and the validate_project handler
    # to surface as successful tool_calls in the trace.
    error_codes = [c["error_code"] for c in record["tool_calls"]]
    assert "OK" in error_codes
    assert "BUSINESS_RULE" not in error_codes  # the deterministic step succeeded
    selected_names = [c["selected"] for c in record["tool_calls"]]
    assert any("workflow:handlers.validate_project" in n for n in selected_names)


@pytest.mark.mock_only
def test_workflow_fast_forward_through_optional_step(tmp_path: Path):
    """When the LLM calls a tool that matches a downstream step's whitelist,
    optional (`must_call_tool: false`) intermediate steps are skipped, not
    treated as OUT_OF_SCOPE."""
    arch = ArchitectureConfig(
        hierarchical_tools=True,
        workflow=WorkflowConfig(enabled=True, yaml_path=str(WORKFLOWS_DIR)),
        state_machine=StateMachineConfig(enabled=True),
        resources_separation=True,
    )
    import workflows  # noqa: F401

    agent = _agent(arch, tmp_path, force_mock=True)
    # The HistoryQuery workflow has three steps:
    #   1. check_history_config (optional, allowed: list_*)
    #   2. enable_history_if_missing (optional, allowed: enable_history, set_retention, ...)
    #   3. query (required, allowed: query_history, list_history)
    # The MockLLM jumps straight to query_history; the orchestrator must
    # fast-forward across step 2.
    record = agent.run(
        "拉一下最近一分钟的温度历史",
        golden_id="ff_test",
        initial_world=build_demo_world(),
    )
    actions = [c.get("action") for c in record["tool_calls"]]
    # Either dispatched OK (and failed at POINT_NOT_FOUND because demo world
    # lacks the history config) or got through the workflow — but never
    # OUT_OF_SCOPE, which would mean fast-forward failed.
    out_of_scope = [c for c in record["tool_calls"] if c["error_code"] == "OUT_OF_SCOPE"]
    assert not out_of_scope, "fast-forward should have skipped the optional step"
    assert "query_history" in actions
