"""Combined agent-loop arbitration — plan → crew → ReAct fallback.

The three loop levers are individually pinned by test_react / test_plan_execute
/ test_multi_agent. These tests pin what only exists on this branch: the
arbitration between them, and the three docode-trial planner fixes.

    query → plan (full catalogue + world snapshot)
          ├─ single domain          → execute compiled steps
          ├─ ≥ min_domains domains  → crew (specialists run the ReAct loop)
          ├─ plan fails, budget out → crew, Blackboard seeded with partial work
          └─ planner abstains       → ReAct interleaved loop
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import (
    ArchitectureConfig,
    ExperimentConfig,
    MultiAgentConfig,
    PlanExecuteConfig,
    ReActConfig,
    SafetyPolicyConfig,
    StateMachineConfig,
    load_config,
)
from agent.llm import LLMResponse, LLMToolCall
from agent.orchestrator import Agent
from agent.planner import summarize_world_for_planner
from agent.tool_registry import build_default_registry
from agent.tracer import Tracer
from tests._llm_factory import make_test_model_config
from world import MockWorld, Page, Point, Widget

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"
REGISTRY = build_default_registry()


# ============================================================ helpers
def _tool(name: str, args: dict) -> LLMResponse:
    return LLMResponse(
        text=None, tool_calls=[LLMToolCall(name=name, arguments=args)],
        stop_reason="tool_use", input_tokens=100, output_tokens=30,
    )


def _text(t: str) -> LLMResponse:
    return LLMResponse(text=t, tool_calls=[], stop_reason="end_turn")


class _HybridLLM:
    """Planner + per-state specialist scripts + interleaved fallback, so one
    stub can serve whichever tier the arbitration picks (and the test can then
    assert which tier that was)."""

    def __init__(self, plans=None, script=None) -> None:
        self.plans = list(plans or [])
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.plan_calls = 0
        self.call_states: list[str] = []
        self.prompts: dict[str, list[str]] = {}
        self.world_contexts: list[str | None] = []
        self.feedbacks: list[str | None] = []
        self.resets = 0

    def make_plan(self, query, tool_list, feedback=None, world_context=None):
        self.plan_calls += 1
        self.feedbacks.append(feedback)
        self.world_contexts.append(world_context)
        self.last_tool_list = tool_list
        if not self.plans:
            return {"steps": []}
        return self.plans.pop(0)

    def call(self, system_prompt, user_query, visible_tools, history, state):
        self.call_states.append(state)
        self.prompts.setdefault(state, []).append(system_prompt)
        self.last_system_prompt = system_prompt
        queue = self.script.get(state)
        if queue:
            return queue.pop(0)
        return _text("无需处理")

    def reset(self) -> None:
        self.resets += 1


def _step(tool, **arguments):
    return {"tool": tool, "arguments": arguments, "rationale": "t"}


def _agent(
    tmp_path: Path,
    llm,
    *,
    plan: PlanExecuteConfig | None = None,
    react: ReActConfig | None = None,
    crew: MultiAgentConfig | None = None,
    safety: SafetyPolicyConfig | None = None,
    max_turns: int = 12,
) -> Agent:
    cfg = ExperimentConfig(
        name="combined_test",
        architecture=ArchitectureConfig(
            hierarchical_tools=False,
            state_machine=StateMachineConfig(enabled=True),
            plan_execute=plan if plan is not None else PlanExecuteConfig(enabled=True),
            react=react if react is not None else ReActConfig(enabled=True),
            # ``domain_gate`` now defaults off in production (it fired 22 of 51
            # escalations on plan *shape* alone, with no observed problem, at
            # ~+31% tokens). This module exists to exercise the escalation
            # arbitration itself, so its default opts back in; tests that care
            # about the production default set it explicitly.
            multi_agent=crew
            if crew is not None
            else MultiAgentConfig(enabled=True, domain_gate=True),
        ),
        safety=safety or SafetyPolicyConfig(),
        model=make_test_model_config(force_mock=True),
    )
    tracer = Tracer(results_root=str(tmp_path), config_name=cfg.name, model_name="combo-stub")
    return Agent(config=cfg, registry=REGISTRY, llm=llm, tracer=tracer, max_turns=max_turns)


def _world() -> MockWorld:
    w = MockWorld()
    w.points["TEMP_101"] = Point(tag="TEMP_101", type="analog", unit="C")
    return w


def _pin_rank(agent: Agent, *first: str) -> None:
    """Deterministic Supervisor routing for crew tests (no RAG index here)."""
    agent._rank_with_rag = lambda q, allowed: sorted(  # type: ignore[method-assign]
        allowed, key=lambda t: tuple(t != f for f in first) + (t,)
    )


# ============================================================ tier: plan
@pytest.mark.mock_only
def test_single_domain_plan_stays_on_the_cheap_path(tmp_path: Path):
    """Two same-domain steps: crew is enabled but must NOT be invoked — the
    whole point of the gate is that single-domain work keeps the 1-call cost."""
    llm = _HybridLLM(plans=[{"steps": [
        _step("create_point", tag="TEMP_301", type="analog"),
        _step("create_point", tag="TEMP_302", type="analog"),
    ]}])
    agent = _agent(tmp_path, llm)
    record = agent.run("建两个点位", golden_id="cb-plan", initial_world=_world())

    assert record["loop"]["path"] == "plan"
    assert llm.plan_calls == 1
    assert llm.call_states == [], "no interleaved/specialist call may run on the plan path"
    assert record["execution"]["total_turns"] == 1
    assert all(c["result_ok"] for c in record["tool_calls"])
    assert record["execution"]["terminal_state"] == "DONE"


@pytest.mark.mock_only
def test_multi_domain_plan_escalates_to_the_crew(tmp_path: Path):
    """Point + alarm spans 2 registry domains → the crew takes over; the plan
    tier spends exactly its one planning call and executes nothing."""
    llm = _HybridLLM(
        plans=[{"steps": [
            _step("create_point", tag="TEMP_555", type="analog"),
            _step("create_analog_alarm", id="a1", tag="TEMP_555", high_limit=90.0),
        ]}],
        script={
            "CONFIG_POINT": [
                _tool("create_point", {"tag": "TEMP_555", "type": "analog"}),
                _text("点位已创建"),
            ],
            "CONFIG_ALARM": [
                _tool("create_analog_alarm", {"id": "a1", "tag": "TEMP_555", "high_limit": 90.0}),
                _text("报警已配置"),
            ],
        },
    )
    agent = _agent(tmp_path, llm)
    _pin_rank(agent, "create_point", "create_analog_alarm")
    record = agent.run("新建点位并加报警", golden_id="cb-gate", initial_world=_world())

    assert record["loop"] == {"path": "crew", "trigger": "domain_gate"}
    assert record["plan"]["escalated"] == "domain_gate"
    assert sorted(record["plan"]["domains"]) == ["manage_alarms", "manage_points"]
    ok = [c for c in record["tool_calls"] if c["result_ok"]]
    assert {c["selected"] for c in ok} == {"create_point", "create_analog_alarm"}
    assert record["crew"]["executed"] is True
    assert record["execution"]["terminal_state"] == "DONE"


@pytest.mark.mock_only
def test_domain_gate_respects_min_domains(tmp_path: Path):
    """Same two-domain plan, gate raised to 3 → stays on the plan path."""
    llm = _HybridLLM(plans=[{"steps": [
        _step("create_point", tag="TEMP_556", type="analog"),
        _step("create_analog_alarm", id="a2", tag="TEMP_556", high_limit=90.0),
    ]}])
    agent = _agent(tmp_path, llm, crew=MultiAgentConfig(enabled=True, min_domains=3))
    record = agent.run("新建点位并加报警", golden_id="cb-min3", initial_world=_world())

    assert record["loop"]["path"] == "plan"
    assert all(c["result_ok"] for c in record["tool_calls"])
    assert len(record["tool_calls"]) == 2


@pytest.mark.mock_only
def test_plan_failure_escalates_and_seeds_the_blackboard(tmp_path: Path):
    """Replan budget 0: the first step succeeds, the second fails → crew takes
    over with the already-built entity on the board (visible to specialists),
    and the specialist finishes the task."""
    llm = _HybridLLM(
        plans=[{"steps": [
            _step("create_point", tag="TEMP_601", type="analog"),
            _step("create_point", tag="TEMP_101", type="analog"),  # exists → fails
        ]}],
        script={
            "CONFIG_POINT": [
                _tool("create_point", {"tag": "TEMP_602", "type": "analog"}),
                _text("补齐点位"),
            ],
        },
    )
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=True, max_replans=0),
        crew=MultiAgentConfig(enabled=True, max_specialists=1),
    )
    _pin_rank(agent, "create_point")
    record = agent.run("建点位", golden_id="cb-fail-esc", initial_world=_world())

    assert record["loop"] == {"path": "crew", "trigger": "plan_step_failed"}
    assert record["plan"]["escalated"] == "plan_step_failed"
    # The partial work travelled: board holds the plan-built entity plus the
    # specialist's own, and the specialist prompt showed the seed.
    assert "points.TEMP_601" in record["crew"]["blackboard"]["entities"]
    assert "points.TEMP_601" in llm.last_system_prompt
    dispatched = [c["args"].get("tag") for c in record["tool_calls"] if c["result_ok"]]
    assert "TEMP_601" in dispatched and "TEMP_602" in dispatched


@pytest.mark.mock_only
def test_policy_denial_never_escalates_to_the_crew(tmp_path: Path):
    """POLICY_DENIED is final in every tier: no replan, no crew, run over."""
    llm = _HybridLLM(
        plans=[{"steps": [_step("deploy_project", deployment_id="d1", force=True)]}],
        script={"DEPLOY": [_tool("deploy_project", {"deployment_id": "d1", "force": True})]},
    )
    agent = _agent(tmp_path, llm, safety=SafetyPolicyConfig(enabled=True))
    record = agent.run("强制下装", golden_id="cb-policy", initial_world=_world())

    assert record["loop"]["path"] == "plan"
    assert record["execution"]["termination_reason"] == "policy_denied"
    assert record["crew"] == {"enabled": False}
    assert llm.call_states == [], "the crew must not get a second try at a denied call"
    assert record["world_snapshots"]["initial_hash"] == record["world_snapshots"]["final_hash"]


# ============================================================ tier: fallback
@pytest.mark.mock_only
def test_planner_abstention_falls_back_to_the_react_loop(tmp_path: Path):
    """Empty plan without refusal → interleaved loop, ReAct block in prompt,
    crew not consulted (abstention is not a decomposition signal)."""
    llm = _HybridLLM(
        plans=[{"steps": []}],
        script={"ANALYZE_INTENT": [_text("思考: 无需配置")]},
    )
    agent = _agent(tmp_path, llm, max_turns=2)
    record = agent.run("随便聊聊", golden_id="cb-fallback", initial_world=_world())

    assert record["loop"] == {"path": "interleaved", "react": True}
    assert record["plan"]["executed"] is False
    assert record["crew"] == {"enabled": False}
    assert "【ReAct 作业方式】" in llm.last_system_prompt


@pytest.mark.mock_only
def test_all_levers_off_is_the_archived_plain_loop(tmp_path: Path):
    llm = _HybridLLM(script={"ANALYZE_INTENT": [_text("done")]})
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=False),
        react=ReActConfig(enabled=False),
        crew=MultiAgentConfig(enabled=False),
        max_turns=2,
    )
    record = agent.run("你好", golden_id="cb-off", initial_world=_world())

    assert record["loop"] == {"path": "interleaved", "react": False}
    assert llm.plan_calls == 0
    assert "【ReAct 作业方式】" not in llm.last_system_prompt


# ============================================================ planner fixes
def test_world_summary_names_real_identifiers():
    w = _world()
    text = summarize_world_for_planner(w)
    assert "TEMP_101" in text and "analog" in text
    assert summarize_world_for_planner(MockWorld()) == "(空项目,尚无任何配置)"


def test_world_summary_is_bounded():
    w = MockWorld()
    for i in range(80):
        w.points[f"T_{i:03d}"] = Point(tag=f"T_{i:03d}", type="analog")
    text = summarize_world_for_planner(w, max_items=10)
    assert "points(80)" in text and "…(+70)" in text


@pytest.mark.mock_only
def test_planner_receives_the_world_snapshot(tmp_path: Path):
    llm = _HybridLLM(plans=[{"steps": [_step("create_point", tag="T9", type="analog")]}])
    agent = _agent(tmp_path, llm)
    agent.run("建个点位", golden_id="cb-worldctx", initial_world=_world())
    assert llm.world_contexts and "TEMP_101" in (llm.world_contexts[0] or "")


@pytest.mark.mock_only
def test_world_context_can_be_switched_off(tmp_path: Path):
    llm = _HybridLLM(plans=[{"steps": [_step("create_point", tag="T9", type="analog")]}])
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=True, include_world_context=False),
    )
    agent.run("建个点位", golden_id="cb-noctx", initial_world=_world())
    assert llm.world_contexts == [None]


@pytest.mark.mock_only
def test_planner_catalogue_always_names_the_deploy_tools(tmp_path: Path):
    """The golden-025 refusal cause: validate/deploy fell out of the detailed
    head. They must at least be *named* whatever the budget."""
    llm = _HybridLLM(plans=[{"steps": [_step("create_point", tag="T9", type="analog")]}])
    agent = _agent(
        tmp_path, llm, plan=PlanExecuteConfig(enabled=True, planner_tool_budget=5)
    )
    agent.run("建个点位", golden_id="cb-catalogue", initial_world=_world())
    assert "validate_project" in llm.last_tool_list
    assert "deploy_project" in llm.last_tool_list


@pytest.mark.mock_only
def test_compile_drop_triggers_one_informed_replan(tmp_path: Path):
    """golden-013's failure mode: 2 proposed, 1 compiles. The replan must name
    the dropped step and the repaired plan must run in full."""
    llm = _HybridLLM(plans=[
        {"steps": [
            _step("create_point", tag="TEMP_701", type="analog"),
            _step("create_point", tag="TEMP_702"),  # schema-invalid: no type
        ]},
        {"steps": [
            _step("create_point", tag="TEMP_701", type="analog"),
            _step("create_point", tag="TEMP_702", type="analog"),
        ]},
    ])
    agent = _agent(tmp_path, llm)
    record = agent.run("建两个点位", golden_id="cb-drop", initial_world=_world())

    assert llm.plan_calls == 2
    assert llm.feedbacks[1] and "schema" in llm.feedbacks[1]
    assert record["plan"]["replans"] == 1
    tags = [c["args"]["tag"] for c in record["tool_calls"] if c["result_ok"]]
    assert set(tags) == {"TEMP_701", "TEMP_702"}


@pytest.mark.mock_only
def test_compile_drop_replan_can_be_switched_off(tmp_path: Path):
    """With the replan off and no crew, the shortened plan is executed — the
    archived single-lever behaviour."""
    llm = _HybridLLM(plans=[{"steps": [
        _step("create_point", tag="TEMP_701", type="analog"),
        _step("create_point", tag="TEMP_702"),  # dropped
    ]}])
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=True, replan_on_compile_drop=False),
        crew=MultiAgentConfig(enabled=False),
    )
    record = agent.run("建两个点位", golden_id="cb-nodrop", initial_world=_world())
    assert llm.plan_calls == 1
    assert len([c for c in record["tool_calls"] if c["result_ok"]]) == 1


@pytest.mark.mock_only
def test_a_worse_retry_does_not_replace_the_original_plan(tmp_path: Path):
    """The compile-drop replan is adopted only if it compiles at least as many
    steps — a panicking planner must not shrink the plan further. With no crew
    to escalate to, the original compiled step still runs."""
    llm = _HybridLLM(plans=[
        {"steps": [
            _step("create_point", tag="TEMP_701", type="analog"),
            _step("create_point", tag="TEMP_702"),  # dropped
        ]},
        {"steps": []},  # retry gives up entirely
    ])
    agent = _agent(tmp_path, llm, crew=MultiAgentConfig(enabled=False))
    record = agent.run("建两个点位", golden_id="cb-worse", initial_world=_world())

    assert llm.plan_calls == 2
    tags = [c["args"]["tag"] for c in record["tool_calls"] if c["result_ok"]]
    assert tags == ["TEMP_701"], "the original compiled step must still run"


@pytest.mark.mock_only
def test_surviving_compile_drops_escalate_to_the_crew(tmp_path: Path):
    """The golden-013 lesson: when part of the task still cannot compile after
    the informed replan, do not execute the shortened plan — the crew's
    iterate-with-feedback loop finishes the whole task instead."""
    llm = _HybridLLM(
        plans=[
            {"steps": [
                _step("create_point", tag="TEMP_701", type="analog"),
                _step("create_point", tag="TEMP_702"),  # schema-invalid
            ]},
            {"steps": [
                _step("create_point", tag="TEMP_701", type="analog"),
                _step("create_point", tag="TEMP_702"),  # retry still invalid
            ]},
        ],
        script={
            "CONFIG_POINT": [
                _tool("create_point", {"tag": "TEMP_701", "type": "analog"}),
                _tool("create_point", {"tag": "TEMP_702", "type": "analog"}),
                _text("两个点位都已创建"),
            ],
        },
    )
    agent = _agent(tmp_path, llm, crew=MultiAgentConfig(enabled=True, max_specialists=1))
    _pin_rank(agent, "create_point")
    record = agent.run("建两个点位", golden_id="cb-drop-esc", initial_world=_world())

    assert record["loop"] == {"path": "crew", "trigger": "compile_drop"}
    assert record["plan"]["escalated"] == "compile_drop"
    tags = [c["args"]["tag"] for c in record["tool_calls"] if c["result_ok"]]
    assert set(tags) == {"TEMP_701", "TEMP_702"}, "the crew must deliver the whole task"


@pytest.mark.mock_only
def test_clean_replan_still_stays_on_the_plan_path(tmp_path: Path):
    """When the informed replan actually repairs the drop, no escalation — the
    repaired plan runs on the cheap tier (regression guard for the tweak)."""
    llm = _HybridLLM(plans=[
        {"steps": [
            _step("create_point", tag="TEMP_701", type="analog"),
            _step("create_point", tag="TEMP_702"),  # dropped once
        ]},
        {"steps": [
            _step("create_point", tag="TEMP_701", type="analog"),
            _step("create_point", tag="TEMP_702", type="analog"),  # repaired
        ]},
    ])
    agent = _agent(tmp_path, llm)
    record = agent.run("建两个点位", golden_id="cb-clean", initial_world=_world())

    assert record["loop"]["path"] == "plan"
    tags = [c["args"]["tag"] for c in record["tool_calls"] if c["result_ok"]]
    assert set(tags) == {"TEMP_701", "TEMP_702"}


# ============================================================ plan-guided crew
@pytest.mark.mock_only
def test_escalated_crew_is_plan_guided(tmp_path: Path):
    """Rec 1: the crew must not re-derive from the raw query what the planner
    already decided — specialists are routed from the compiled plan's states
    and see their slice as an explicit worklist."""
    llm = _HybridLLM(
        plans=[{"steps": [
            _step("create_point", tag="TEMP_555", type="analog"),
            _step("create_analog_alarm", id="a1", tag="TEMP_555", high_limit=90.0),
        ]}],
        script={
            "CONFIG_POINT": [
                _tool("create_point", {"tag": "TEMP_555", "type": "analog"}),
                _text("点位已创建"),
            ],
            "CONFIG_ALARM": [
                _tool("create_analog_alarm", {"id": "a1", "tag": "TEMP_555", "high_limit": 90.0}),
                _text("报警已配置"),
            ],
        },
    )
    agent = _agent(tmp_path, llm)
    record = agent.run("新建点位并加报警", golden_id="cb-guided", initial_world=_world())

    assert record["crew"]["plan_guided"] is True
    # Specialists come from the plan's states, in plan order — not from the
    # RAG ranking (which _pin_rank was NOT applied to here on purpose).
    states = [a["state"] for a in record["crew"]["assignments"]]
    assert states == ["CONFIG_POINT", "CONFIG_ALARM"]
    # Each specialist saw its own worklist with the planner's arguments.
    point_prompts = "\n".join(llm.prompts["CONFIG_POINT"])
    assert "执行清单" in point_prompts and "TEMP_555" in point_prompts
    alarm_prompts = "\n".join(llm.prompts["CONFIG_ALARM"])
    assert "create_analog_alarm" in alarm_prompts and "high_limit" in alarm_prompts
    assert all(c["result_ok"] for c in record["tool_calls"])


@pytest.mark.mock_only
def test_failure_escalation_hands_over_only_the_remainder(tmp_path: Path):
    """Executed steps stay off the worklist — re-listing them would make the
    specialist redo finished work (their results are on the board instead)."""
    llm = _HybridLLM(
        plans=[{"steps": [
            _step("create_point", tag="TEMP_601", type="analog"),   # succeeds
            _step("create_point", tag="TEMP_101", type="analog"),   # exists → fails
        ]}],
        script={
            "CONFIG_POINT": [
                _tool("update_point", {"tag": "TEMP_101", "unit": "K"}),
                _text("改用更新完成"),
            ],
        },
    )
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=True, max_replans=0),
    )
    record = agent.run("建点位", golden_id="cb-remainder", initial_world=_world())

    assert record["loop"]["trigger"] == "plan_step_failed"
    worklists = [a for a in record["crew"]["assignments"]]
    assert len(worklists) == 1
    prompts = "\n".join(llm.prompts["CONFIG_POINT"])
    assert "TEMP_101" in prompts, "the failed step must be on the worklist"
    assert "create_point(tag='TEMP_601'" not in prompts, "executed work must not be re-listed"


@pytest.mark.mock_only
def test_plan_guided_budget_scales_with_the_task(tmp_path: Path):
    """Rec 3: a 4-step escalated plan must not be strangled by a small flat
    max_turns — the budget scales with ceil(turns_per_step × steps)."""
    llm = _HybridLLM(
        plans=[{"steps": [
            _step("create_point", tag="TEMP_611", type="analog"),
            _step("create_point", tag="TEMP_612", type="analog"),
            _step("create_analog_alarm", id="a1", tag="TEMP_611", high_limit=80.0),
            _step("create_analog_alarm", id="a2", tag="TEMP_612", high_limit=80.0),
        ]}],
        script={
            "CONFIG_POINT": [
                _tool("create_point", {"tag": "TEMP_611", "type": "analog"}),
                _tool("create_point", {"tag": "TEMP_612", "type": "analog"}),
                _text("点位完成"),
            ],
            "CONFIG_ALARM": [
                _tool("create_analog_alarm", {"id": "a1", "tag": "TEMP_611", "high_limit": 80.0}),
                _tool("create_analog_alarm", {"id": "a2", "tag": "TEMP_612", "high_limit": 80.0}),
                _text("报警完成"),
            ],
        },
    )
    agent = _agent(tmp_path, llm, max_turns=2)
    record = agent.run("两点两报警", golden_id="cb-budget", initial_world=_world())

    assert record["crew"]["turn_budget"] == 7  # max(2, 1 + ceil(1.5*4))
    assert len([c for c in record["tool_calls"] if c["result_ok"]]) == 4
    assert record["execution"]["early_terminated"] is False
    assert record["execution"]["total_turns"] > 2, "the flat cap would have starved this"


def test_plan_guided_assignment_covers_every_tool_on_its_worklist():
    """Regression: `atomics` used to be capped at `tools_per_specialist` while
    `worklist` was not, so a state whose plan used >8 distinct tools instructed
    the Specialist to call tools its own assignment forbade — a guaranteed
    OUT_OF_SCOPE and a silent amputation of the task."""
    from agent.multi_agent import route_subtasks_from_plan

    class _S:
        def __init__(self, tool):
            self.state, self.tool, self.arguments = "CONFIG_POINT", tool, {"tag": tool}

    steps = [_S(f"tool_{i}") for i in range(12)]
    subtasks = route_subtasks_from_plan(steps, tools_per_specialist=8)

    assert len(subtasks) == 1
    sub = subtasks[0]
    assert len(sub.worklist) == 12
    assert len(sub.atomics) == 12, "assignment must cover every tool it is told to call"
    for line in sub.worklist:
        named = line.split("(")[0]
        assert named in sub.atomics, f"{named} is on the worklist but not the assignment"


def test_plan_guided_routing_keeps_every_state():
    """`max_specialists` must not truncate a plan-guided crew — dropping a
    trailing state (e.g. DEPLOY) would amputate the end of the task."""
    from agent.multi_agent import route_subtasks_from_plan

    class _S:
        def __init__(self, state, tool):
            self.state, self.tool, self.arguments = state, tool, {}

    steps = [
        _S("CONFIG_POINT", "create_point"),
        _S("MANAGE_PAGES", "create_page"),
        _S("BIND_POINTS", "bind_point"),
        _S("DEPLOY", "validate_project"),
    ]
    states = [s.state for s in route_subtasks_from_plan(steps)]
    assert states == ["CONFIG_POINT", "MANAGE_PAGES", "BIND_POINTS", "DEPLOY"]


# ============================================================ P1-P4 follow-ups
def test_p1_double_encoded_arrays_are_repaired_not_dropped():
    """P1: every top-10 compile drop on the 106-case run was schema_invalid, and
    that one mode caused 81% of crew escalations. A double-encoded array is a
    shape defect, not an intent error — repair it."""
    from agent.planner import compile_plan

    plan = compile_plan(
        [{"tool": "create_rect", "arguments": {
            "page_id": "p1", "widget_id": "r1",
            "position": "[50, 50]", "size": "[120, 80]"}}],
        REGISTRY, MockWorld(),
    )
    assert plan.diagnostics.dropped_schema_invalid == []
    assert len(plan.steps) == 1
    assert plan.steps[0].arguments["position"] in ([50, 50], (50, 50))


def test_p1_nulls_and_unknown_keys_are_repaired():
    from agent.planner import compile_plan

    plan = compile_plan(
        [{"tool": "create_point", "arguments": {
            "tag": "T1", "type": "analog", "unit": None, "invented_field": 1}}],
        REGISTRY, MockWorld(),
    )
    assert len(plan.steps) == 1 and not plan.diagnostics.dropped_schema_invalid


def test_p1_genuinely_invalid_steps_still_drop():
    """Repair must not become "accept anything" — a missing required field is a
    real defect and must still surface as a drop (and so trigger the replan)."""
    from agent.planner import compile_plan

    plan = compile_plan(
        [{"tool": "create_point", "arguments": {"tag": "T1"}}], REGISTRY, MockWorld()
    )
    assert plan.steps == [] and plan.diagnostics.dropped_schema_invalid == ["create_point"]


def test_p1_catalogue_names_argument_types():
    """The drops clustered on nested-argument tools: the planner knew the field
    was required but not its shape."""
    from agent.planner import describe_tools_for_planner

    text = describe_tools_for_planner(REGISTRY, ["create_rect", "create_point"], max_tools=2)
    assert "position:array" in text
    assert "type:analog|digital" in text


@pytest.mark.mock_only
def test_p3_refusal_reaches_the_specialists(tmp_path: Path):
    """P3: a safety concern raised while planning used to evaporate at the tier
    boundary — the crew received a worklist and executed it (3 of 5 escalated
    reject cases wrote to the world, 20% behavior_success). It must be handed
    across.

    Uses the domain-gate escalation because that is the reachable carrier: a
    compile-drop escalation is guarded by `not refusal` (a refusal is a result,
    not a drop), so a refusal can only cross the boundary on the gate or on a
    plan-execution failure.
    """
    llm = _HybridLLM(
        plans=[{"steps": [
            _step("create_point", tag="TEMP_901", type="analog"),
            _step("create_analog_alarm", id="a1", tag="TEMP_901", high_limit=80.0),
        ], "refusal": "该操作会跳过安全校验"}],
        script={
            "CONFIG_POINT": [_text("同意上游判断，拒绝执行")],
            "CONFIG_ALARM": [_text("同意上游判断，拒绝执行")],
        },
    )
    agent = _agent(tmp_path, llm)
    record = agent.run("强制建点并加报警", golden_id="cb-refusal-handoff", initial_world=_world())

    assert record["loop"]["trigger"] == "domain_gate"
    assert record["crew"]["refusal_handoff"] is True
    prompts = "\n".join(llm.prompts.get("CONFIG_POINT", []))
    assert "上游安全提示" in prompts and "跳过安全校验" in prompts
    assert record["world_snapshots"]["initial_hash"] == record["world_snapshots"]["final_hash"]


def test_p3_specialists_are_told_refusal_outranks_execution():
    from agent.multi_agent import SPECIALIST_PROMPT_BLOCK

    block = SPECIALIST_PROMPT_BLOCK.format(role="X", blackboard="")
    assert "拒绝优先于执行" in block
    assert "并不代表这个任务已经通过安全审查" in block


def test_p4_config_J_enables_the_runtime_cage():
    cfg = load_config(CONFIGS_DIR / "J_combined.yaml")
    assert cfg.safety.enabled is True
    assert cfg.safety.runtime_mode == "design_time"


def test_blackboard_records_widget_entities_with_ids():
    """Rec 2: widget diffs keep their 4-segment identity — truncating to
    `pages.p1` hid every widget ID from the binding specialist."""
    from agent.multi_agent import Blackboard

    board = Blackboard()
    board.record_diff({
        "added_or_modified": {
            "pages.p1.widgets.w1": {"type": "pump"},
            "pages.p1.widgets.w1.style": "red",
            "pages.p1.title": "泵站",
            "points.T1": {},
        },
        "removed": [],
    })
    assert "pages.p1.widgets.w1" in board.entities
    assert "pages.p1" in board.entities
    assert "points.T1" in board.entities
    assert "pages.p1.widgets.w1.style" not in board.entities


def test_planner_remainder_lists_writes_only():
    """Rec 4: read tools are dropped from the name-only remainder (Resources
    serve reads), but validate/deploy stay — they are actions, not reads."""
    from agent.planner import describe_tools_for_planner

    atomics = ["create_point", "list_points", "query_history", "validate_project",
               "deploy_project", "list_pages"]
    text = describe_tools_for_planner(REGISTRY, atomics, max_tools=1)
    remainder = text.split("其余可用工具")[1]
    assert "validate_project" in remainder and "deploy_project" in remainder
    assert "list_points" not in remainder
    assert "query_history" not in remainder


# ============================================================ react in specialists
@pytest.mark.mock_only
def test_specialists_run_the_react_loop(tmp_path: Path):
    """Standalone crew + ReAct: the specialist prompt carries the ReAct block,
    a repeated call inside one specialist is absorbed, and the run-level react
    summary aggregates across specialists."""
    llm = _HybridLLM(script={
        "CONFIG_POINT": [
            _tool("create_point", {"tag": "TEMP_801", "type": "analog"}),
            _tool("create_point", {"tag": "TEMP_801", "type": "analog"}),  # repeat
            _text("done"),
        ],
    })
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=False),
        # Dedupe defaults off in production; this test asserts absorption, so
        # it opts in explicitly.
        react=ReActConfig(enabled=True, dedupe_repeat_actions=True),
        crew=MultiAgentConfig(enabled=True, max_specialists=1, critic_retry=False),
    )
    _pin_rank(agent, "create_point")
    record = agent.run("建点位 TEMP_801", golden_id="cb-spec-react", initial_world=_world())

    assert record["loop"] == {"path": "crew", "trigger": "standalone"}
    assert "【ReAct 作业方式】" in llm.last_system_prompt
    dispatched = [c for c in record["tool_calls"] if c["selected"] == "create_point"]
    assert len(dispatched) == 1, "the repeat must be absorbed, not re-dispatched"
    assert record["react"]["suppressed_repeats"] == 1
    assert record["react"]["enabled"] is True


# ============================================================ config wiring
def test_config_J_ships_plan_and_react_but_not_the_crew():
    on = load_config(CONFIGS_DIR / "J_combined.yaml").architecture
    off = load_config(CONFIGS_DIR / "F_full_four_in_one.yaml").architecture
    assert on.plan_execute.enabled and on.react.enabled
    assert on.plan_execute.include_world_context and on.plan_execute.replan_on_compile_drop
    # The crew is retired as a default tier. Its only measured justification
    # (+9pp reject-case behavior_success) came from a 33-case subset where 8
    # cases flip between identical reps — a 24pp noise band — so it does not
    # survive. Its cost is real and reproducible: +31% input tokens, and
    # crew-off scored better on every metric that still holds (64.2% vs 62.7%
    # task_success at 4,536 vs 6,411 tokens). The tier and its tests stay; only
    # the default changed.
    assert on.multi_agent.enabled is False
    # ReAct keeps observation compression; dedupe and repair hints are retired —
    # both fired 0 times across three 106-case sweeps on two models.
    assert on.react.dedupe_repeat_actions is False
    assert on.react.repair_hints is False
    # Promoted 2026-07-31 from the K4 arm (results_w11, 106 x 3): 69.2% vs 65.7%
    # against the identical config without it, +11 net run-by-run over 318 runs.
    assert on.plan_execute.clarify_on_underspecified is True
    # The other two W9/W10 levers stay off on measurement, not preference. The
    # cascade guard (K2) was the weakest arm at 65.1% and fired 3 times in 318
    # runs; the verify round (K3) scored 65.4% at +26% tokens — on corrected
    # inputs it does patch (13/169, up from 1/111), the patches just do not
    # convert into task success.
    assert on.plan_execute.replan_may_create_referenced is True
    assert on.plan_execute.verify_rounds == 0
    assert not (off.plan_execute.enabled or off.react.enabled or off.multi_agent.enabled)
    # J matches F on the first four architecture levers...
    assert (
        on.hierarchical_tools, on.tool_rag.enabled, on.workflow.enabled,
        on.state_machine.enabled,
    ) == (
        off.hierarchical_tools, off.tool_rag.enabled, off.workflow.enabled,
        off.state_machine.enabled,
    )
    # ...but deliberately diverges on §4.5. Measured W5 (LongCat, 106 cases x 2
    # reps): F_noresources 53.3% vs F_full 48.6% task_success, and F_full had the
    # worst accuracy and the highest early-termination (19.3%) of all nine arms
    # because it spends turns reading. The lever costs accuracy even now that
    # the read path actually works (W1). F keeps it on — F *is* the paper's
    # "all four levers" definition and must not be redefined; F_noresources is
    # the control that isolates it.
    assert on.resources_separation is False
    assert off.resources_separation is True


# ============================================ clarify vs refusal (golden-008/-060)
@pytest.mark.mock_only
def test_clarify_asks_instead_of_acting_and_lands_in_ask_user(tmp_path):
    """The measured failure: the planner invented an identity rather than asking.

    golden-008 ("帮忙建个页面") was planned as
    ``create_page(id="main_page", name="主页面")`` — a mutation on a case whose
    expected world diff is empty. With the lever on, the same reply carries a
    ``clarify`` and nothing is dispatched.
    """
    llm = _HybridLLM(plans=[{"steps": [], "clarify": "需要页面 ID 与名称"}])
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(enabled=True, clarify_on_underspecified=True),
    )
    world = _world()
    before = world.hash()
    trace = agent.run("帮忙建个页面", golden_id="cb-clarify", initial_world=world)

    assert trace["loop"]["path"] == "plan"
    assert trace["plan"]["clarify"] == "需要页面 ID 与名称"
    assert trace["plan"]["refusal"] is None
    assert trace["execution"]["termination_reason"] == "clarify"
    # ASK_USER, not DONE: `success` cases exclude ASK_USER, so a clarification
    # that lands on DONE would score as a completed task.
    assert trace["execution"]["terminal_state"] == "ASK_USER"
    assert trace["tool_calls"] == []
    assert world.hash() == before


@pytest.mark.mock_only
def test_a_clarify_discards_any_steps_proposed_alongside_it(tmp_path):
    """Belt and braces: a model that both asks *and* plans must not act."""
    llm = _HybridLLM(
        plans=[{
            "steps": [_step("create_point", tag="INVENTED", type="analog")],
            "clarify": "点位标签是什么?",
        }]
    )
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(enabled=True, clarify_on_underspecified=True),
    )
    world = _world()
    before = world.hash()
    trace = agent.run("加个点位", golden_id="cb-clarify-steps", initial_world=world)

    assert trace["plan"]["clarify"] == "点位标签是什么?"
    assert trace["plan"]["proposed"] == 1
    assert trace["plan"]["compiled"] == 0
    assert trace["tool_calls"] == []
    assert world.hash() == before
    assert "INVENTED" not in world.points


@pytest.mark.mock_only
def test_clarify_lever_off_ignores_the_field_entirely(tmp_path):
    """Default off, the field is *ignored* — not folded into ``refusal``.

    Folding looks harmless and is not: ``refusal`` gates
    ``replan_on_compile_drop`` and both crew escalations, all written
    ``and not refusal``. So a reply carrying steps *and* a clarification would
    lose the compile-drop replan while the lever is supposedly off. Ignoring an
    unrecognised key is what code predating the channel did, and is the only
    behaviour-preserving choice. See
    ``test_a_folded_clarify_must_not_disable_the_compile_drop_replan``.
    """
    llm = _HybridLLM(plans=[{"steps": [], "clarify": "需要页面 ID 与名称"}])
    agent = _agent(tmp_path, llm, plan=PlanExecuteConfig(enabled=True))
    world = _world()
    trace = agent.run("帮忙建个页面", golden_id="cb-clarify-off", initial_world=world)

    assert trace["plan"]["clarify"] is None
    assert trace["plan"]["refusal"] is None
    # Empty plan, no refusal → the archived path is the interleaved fallback.
    assert trace["loop"]["path"] == "interleaved"
    assert world.hash() == _world().hash()


@pytest.mark.mock_only
def test_a_folded_clarify_must_not_disable_the_compile_drop_replan(tmp_path):
    """Regression: with the lever off, a clarify alongside steps must not make
    the run behave as if the planner had refused.

    The measured defect this guards: `refusal` truthiness suppressed the
    informed replan, so a plan whose second step was dropped executed the first
    and reported success — golden-013's "half the task delivered as if it were
    all of it", re-opened through a lever that is off.
    """
    llm = _HybridLLM(
        plans=[
            {"steps": [_step("create_point", tag="A1", type="analog"),
                       {"tool": "no_such_tool", "arguments": {}, "rationale": "x"}],
             "clarify": "顺便问一下"},
            {"steps": [_step("create_point", tag="A1", type="analog"),
                       _step("create_point", tag="A2", type="analog")]},
        ]
    )
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=True, max_replans=2),
        crew=MultiAgentConfig(enabled=False),
    )
    world = _world()
    trace = agent.run("建两个点位", golden_id="cb-clarify-fold", initial_world=world)

    # The adopted retry's diagnostics replace the original's, so the drop is not
    # visible in the trace; the replan *firing* is the thing under test.
    assert llm.plan_calls == 2, "the compile-drop replan was suppressed"
    assert trace["plan"]["replans"] == 1
    assert "A1" in world.points and "A2" in world.points


def test_planner_prompt_separates_clarify_from_refusal():
    """Both channels must be described, and the fabrication ban must be explicit
    — the prompt is the only place the distinction can be taught."""
    from agent.planner import PLANNER_SYSTEM_PROMPT as P

    assert "clarify" in P and "refusal" in P
    assert "发明" in P, "the fabrication ban must be explicit"
    # The *discriminator*, not just the field. Measured: a rule phrased as
    # "missing identity or missing semantics → ask" over-fires badly — it made
    # golden-013 ask for page IDs the user had already named ("一个叫报警汇总")
    # and golden-018 ask for a threshold behind the word "过高", both of which
    # the dataset expects the agent to resolve and act on. The narrower test —
    # is there anything to refer to at all — is what separates those from
    # golden-008's contentless "帮忙建个页面".
    assert "是否存在可指代的对象" in P
    assert "不是**提问的理由" in P, "vague wording must be excluded explicitly"


# ==================================== replan cascade guard (golden-054)
@pytest.mark.mock_only
def test_replan_may_not_manufacture_a_referenced_entity(tmp_path):
    """golden-054: ``query_history`` correctly failed POINT_NOT_FOUND — the code
    the case expects — and the replan called ``create_point`` to make the query
    succeed, mutating a world the case requires untouched."""
    llm = _HybridLLM(
        plans=[
            {"steps": [_step("query_history", tag="NO_SUCH_POINT", window_s=3600)]},
            {"steps": [
                _step("create_point", tag="NO_SUCH_POINT", type="analog"),
                _step("query_history", tag="NO_SUCH_POINT", window_s=3600),
            ]},
        ]
    )
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(
            enabled=True, max_replans=1, replan_may_create_referenced=False
        ),
        crew=MultiAgentConfig(enabled=False),
    )
    world = _world()
    trace = agent.run("查询 NO_SUCH_POINT 最近一小时历史", golden_id="cb-cascade", initial_world=world)

    assert "NO_SUCH_POINT" not in world.points, "the replan invented the premise"
    assert "create_point" in trace["plan"]["dropped_cascade_recovery"]
    assert trace["execution"]["termination_reason"] == "replan_cascade_blocked"


@pytest.mark.mock_only
def test_replan_may_recreate_an_entity_the_original_plan_asked_for(tmp_path):
    """The guard must not block ordinary recovery. When the *approved* plan
    intended to create the entity, the user did ask for it — a replan re-adding
    it after a dropped prerequisite is exactly what replanning is for."""
    llm = _HybridLLM(
        plans=[
            # Both steps approved; the alarm runs first and fails on the
            # not-yet-created point.
            {"steps": [
                _step("create_analog_alarm", id="A1", tag="NEW_PT", high_limit=80),
                _step("create_point", tag="NEW_PT", type="analog"),
            ]},
            {"steps": [
                _step("create_point", tag="NEW_PT", type="analog"),
                _step("create_analog_alarm", id="A1", tag="NEW_PT", high_limit=80),
            ]},
        ]
    )
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(
            enabled=True,
            max_replans=1,
            reorder_by_dependency=False,  # force the failure the guard must not eat
            replan_may_create_referenced=False,
        ),
        crew=MultiAgentConfig(enabled=False),
    )
    world = _world()
    trace = agent.run("给 NEW_PT 建点并加高温报警", golden_id="cb-cascade-ok", initial_world=world)

    assert "NEW_PT" in world.points, "legitimate prerequisite creation was blocked"
    assert not trace["plan"]["dropped_cascade_recovery"]


@pytest.mark.mock_only
def test_cascade_guard_off_is_the_archived_behaviour(tmp_path):
    llm = _HybridLLM(
        plans=[
            {"steps": [_step("query_history", tag="NO_SUCH_POINT", window_s=3600)]},
            {"steps": [_step("create_point", tag="NO_SUCH_POINT", type="analog")]},
        ]
    )
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(enabled=True, max_replans=1),  # lever defaults on
        crew=MultiAgentConfig(enabled=False),
    )
    world = _world()
    agent.run("查询 NO_SUCH_POINT 最近一小时历史", golden_id="cb-cascade-off", initial_world=world)
    assert "NO_SUCH_POINT" in world.points


# ==================================== verify/patch round (golden-093 / -013)
@pytest.mark.mock_only
def test_verify_round_patches_what_the_plan_left_undone(tmp_path):
    """golden-093's shape: the plan acted, no step errored, and the requested
    state was still absent. The plan archived the point instead of enabling
    history; a round that reads the world back sees ``histories.*`` missing."""
    llm = _HybridLLM(
        plans=[
            {"steps": [_step("create_point", tag="ENERGY_KWH", type="analog", unit="kWh")]},
            {"steps": [_step("enable_history", tag="ENERGY_KWH", storage_mode="on_change")]},
            {"steps": []},
        ]
    )
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(enabled=True, verify_rounds=2),
        crew=MultiAgentConfig(enabled=False),
    )
    world = _world()
    trace = agent.run("给 ENERGY_KWH 建点并开启变化存储历史",
                      golden_id="cb-verify", initial_world=world)

    assert "ENERGY_KWH" in world.histories, "the verify round did not patch the gap"
    assert world.histories["ENERGY_KWH"].storage_mode == "on_change"
    assert trace["plan"]["verify_patched"] == 1
    assert trace["plan"]["verify_rounds"] == 2
    assert trace["plan"]["verify_clean"] is True
    # The extra calls are charged, not hidden: 1 plan + 2 verify.
    assert llm.plan_calls == 3
    assert trace["execution"]["total_turns"] == 3


@pytest.mark.mock_only
def test_verify_round_sees_the_post_execution_state(tmp_path):
    """The verification prompt must carry what was built, not the pre-run
    snapshot — otherwise it is a second guess rather than a check."""
    llm = _HybridLLM(
        plans=[
            {"steps": [_step("create_point", tag="NEW_PT", type="analog")]},
            {"steps": []},
        ]
    )
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(enabled=True, verify_rounds=1),
        crew=MultiAgentConfig(enabled=False),
    )
    agent.run("建个点位 NEW_PT", golden_id="cb-verify-state", initial_world=_world())

    verify_feedback = llm.feedbacks[1]
    assert verify_feedback is not None
    assert "points.NEW_PT" in verify_feedback, verify_feedback
    assert "create_point" in verify_feedback
    assert "不要输出任何删除/禁用类操作" in verify_feedback


@pytest.mark.mock_only
def test_verify_round_refuses_a_destructive_patch(tmp_path):
    """Completing a request never requires deleting. A destructive patch step is
    dropped by the runtime rather than trusted to the prompt."""
    llm = _HybridLLM(
        plans=[
            {"steps": [_step("create_point", tag="KEEP_PT", type="analog")]},
            {"steps": [_step("delete_point", tag="TEMP_101")]},
        ]
    )
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(enabled=True, verify_rounds=1),
        crew=MultiAgentConfig(enabled=False),
    )
    world = _world()
    trace = agent.run("建个点位 KEEP_PT", golden_id="cb-verify-destroy", initial_world=world)

    assert "TEMP_101" in world.points, "a verify round deleted an existing entity"
    assert trace["plan"]["dropped_verify_destructive"] == ["delete_point"]
    assert trace["plan"]["verify_patched"] == 0


@pytest.mark.mock_only
def test_verify_round_never_fires_on_a_refusal(tmp_path):
    """The gate that matters most. A refusal means the world must stay untouched,
    and a round whose job is to *finish the task* must not run after one."""
    llm = _HybridLLM(plans=[{"steps": [], "refusal": "高危操作,拒绝执行"}])
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(enabled=True, verify_rounds=2),
        crew=MultiAgentConfig(enabled=False),
    )
    world = _world()
    before = world.hash()
    trace = agent.run("强制下装", golden_id="cb-verify-refuse", initial_world=world)

    assert llm.plan_calls == 1, "a verify round ran after a refusal"
    assert trace["plan"]["verify_rounds"] == 0
    assert world.hash() == before


@pytest.mark.mock_only
def test_verify_round_never_fires_on_a_clarification(tmp_path):
    llm = _HybridLLM(plans=[{"steps": [], "clarify": "需要页面 ID"}])
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(
            enabled=True, verify_rounds=2, clarify_on_underspecified=True
        ),
        crew=MultiAgentConfig(enabled=False),
    )
    world = _world()
    before = world.hash()
    trace = agent.run("帮忙建个页面", golden_id="cb-verify-clarify", initial_world=world)

    assert llm.plan_calls == 1, "a verify round ran after a clarification"
    assert trace["plan"]["verify_rounds"] == 0
    assert world.hash() == before


@pytest.mark.mock_only
def test_verify_round_never_fires_after_a_policy_denial(tmp_path):
    """Same cage, same answer: the §4.7 boundary is not something a later round
    gets to complete around."""
    llm = _HybridLLM(
        plans=[{"steps": [_step("deploy_project", target="prod", force=True)]}]
    )
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(enabled=True, verify_rounds=2),
        crew=MultiAgentConfig(enabled=False),
        safety=SafetyPolicyConfig(enabled=True),
    )
    world = _world()
    before = world.hash()
    trace = agent.run("直接强制下装到生产", golden_id="cb-verify-denied",
                      initial_world=world)

    assert trace["execution"]["termination_reason"] == "policy_denied"
    assert llm.plan_calls == 1, "a verify round ran after a policy denial"
    assert trace["plan"]["verify_rounds"] == 0
    assert world.hash() == before


@pytest.mark.mock_only
def test_verify_round_off_by_default_is_the_archived_open_loop(tmp_path):
    llm = _HybridLLM(
        plans=[{"steps": [_step("create_point", tag="NEW_PT", type="analog")]}]
    )
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=True),
        crew=MultiAgentConfig(enabled=False),
    )
    trace = agent.run("建个点位", golden_id="cb-verify-off", initial_world=_world())

    assert llm.plan_calls == 1
    assert trace["plan"]["verify_rounds"] == 0
    assert trace["plan"]["verify_clean"] is False
    assert trace["execution"]["total_turns"] == 1


@pytest.mark.mock_only
def test_verify_patch_does_not_double_apply_completed_work(tmp_path):
    """A verify round that re-proposes a step already executed must be a no-op —
    the executed-signature set is what makes a second pass safe."""
    llm = _HybridLLM(
        plans=[
            {"steps": [_step("create_point", tag="NEW_PT", type="analog")]},
            {"steps": [_step("create_point", tag="NEW_PT", type="analog")]},
        ]
    )
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=True, verify_rounds=1),
        crew=MultiAgentConfig(enabled=False),
    )
    world = _world()
    trace = agent.run("建个点位 NEW_PT", golden_id="cb-verify-idem", initial_world=world)

    dispatched = [c for c in trace["tool_calls"] if c["selected"] == "create_point"]
    assert len(dispatched) == 1, "the verify round re-dispatched finished work"
    assert not any(c["error_code"] == "ALREADY_EXISTS" for c in trace["tool_calls"])


@pytest.mark.mock_only
def test_cascade_guard_allows_recreating_a_nested_entity_the_plan_intended(tmp_path):
    """The escape hatch must fire for *nested* entities too.

    Measured defect: the guard read an entity's id as the last path segment, but
    the last segment of a deep path is a field — ``bind_point`` intends
    ``pages.p1.widgets.therm1.bindings.value``, so the plan contributed ``value``
    and never ``therm1``. A ``WIDGET_NOT_FOUND`` recovery that created ``therm1``
    therefore looked like manufactured premise, and a fully recoverable failure
    was aborted with nothing built.
    """
    llm = _HybridLLM(
        plans=[
            {"steps": [_step("bind_point", page_id="p1", widget_id="therm1",
                             property="value", tag="TEMP_101")]},
            {"steps": [_step("create_widget", page_id="p1", widget_id="therm1",
                             type="thermometer", position=[10, 10], size=[40, 40]),
                       _step("bind_point", page_id="p1", widget_id="therm1",
                             property="value", tag="TEMP_101")]},
        ]
    )
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=True, max_replans=1,
                               replan_may_create_referenced=False),
        crew=MultiAgentConfig(enabled=False),
    )
    world = _world()
    world.pages["p1"] = Page(id="p1", name="P1")
    trace = agent.run("把 TEMP_101 绑定到 p1 的 therm1",
                      golden_id="cb-cascade-nested", initial_world=world)

    assert not trace["plan"]["dropped_cascade_recovery"], (
        "legitimate recovery for an entity the plan intended was blocked"
    )
    assert "therm1" in world.pages["p1"].widgets
    assert trace["execution"]["termination_reason"] != "replan_cascade_blocked"


def test_entity_ids_returns_every_id_not_the_last_segment():
    from agent.planner import entity_ids

    assert entity_ids("points.TEMP_101") == {"TEMP_101"}
    assert entity_ids("histories.TEMP_101") == {"TEMP_101"}
    # The case that broke the guard: the widget id must survive.
    assert "therm1" in entity_ids("pages.p1.widgets.therm1.bindings.value")
    assert "p1" in entity_ids("pages.p1.widgets.therm1.bindings.value")
    assert entity_ids("") == set()


def test_identity_known_sees_nested_entities():
    """A widget exists; reporting it as existing nowhere over-protects it."""
    from agent.planner import _identity_known

    world = _world()
    world.pages["p1"] = Page(id="p1", name="P1")
    world.pages["p1"].widgets["w1"] = Widget(
        id="w1", page_id="p1", type="thermometer", position=(1, 1), size=(2, 2)
    )
    assert _identity_known(world, "p1")
    assert _identity_known(world, "TEMP_101")
    assert _identity_known(world, "w1"), "nested widget reported as nonexistent"
    assert not _identity_known(world, "NOPE")


def test_enum_hints_are_never_truncated():
    """A closed value set rendered partially reads as exhaustive.

    ``create_device.device_type`` has 8 values; a 4-value cut hides ``valve`` and
    answers "建一个阀门设备" with "no such device_type".
    """
    from agent.planner import _type_hint, describe_tools_for_planner

    reg = REGISTRY
    schema = reg.atomic("create_device").args_model.model_json_schema()
    prop = (schema.get("properties") or {})["device_type"]
    assert len(prop["enum"]) > 4, "test needs a >4-value enum to be meaningful"
    rendered = _type_hint(prop)
    for value in prop["enum"]:
        assert str(value) in rendered, f"{value} hidden from a closed value set"
    line = describe_tools_for_planner(reg, ["create_device"], max_tools=1)
    assert rendered in line


@pytest.mark.mock_only
def test_verify_patched_does_not_count_a_no_op_reproposal(tmp_path):
    """A patch step whose signature already executed is a no-op, not a patch.

    Counting it let a round that found nothing report that it fixed something,
    inflating every "verification adds value" number by the re-proposal rate.
    """
    llm = _HybridLLM(
        plans=[
            {"steps": [_step("create_point", tag="NEW_PT", type="analog")]},
            {"steps": [_step("create_point", tag="NEW_PT", type="analog")]},
        ]
    )
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=True, verify_rounds=1),
        crew=MultiAgentConfig(enabled=False),
    )
    trace = agent.run("建个点位 NEW_PT", golden_id="cb-verify-noop",
                      initial_world=_world())

    dispatched = [c for c in trace["tool_calls"] if c["selected"] == "create_point"]
    assert len(dispatched) == 1
    assert trace["plan"]["verify_patched"] == 0, "a no-op was counted as a patch"


@pytest.mark.mock_only
def test_verify_round_reports_what_actually_executed(tmp_path):
    """After a replan, the verify prompt must list the step that *succeeded*.

    It used to render ``plan.steps`` — the original compiled plan — so it told the
    verifier that the failed step had run and the successful replan had not.
    """
    llm = _HybridLLM(
        plans=[
            {"steps": [_step("enable_history", tag="MISSING_PT",
                             storage_mode="periodic")]},
            {"steps": [_step("enable_history", tag="TEMP_101",
                             storage_mode="on_change")]},
            {"steps": []},
        ]
    )
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=True, max_replans=1, verify_rounds=1),
        crew=MultiAgentConfig(enabled=False),
    )
    agent.run("给 TEMP_101 开启变化存储", golden_id="cb-verify-executed",
              initial_world=_world())

    verify_feedback = llm.feedbacks[-1]
    assert verify_feedback is not None
    assert "TEMP_101" in verify_feedback, "the step that succeeded was omitted"
    assert "MISSING_PT" not in verify_feedback, "a failed step reported as executed"


@pytest.mark.mock_only
def test_verify_clean_is_not_claimed_when_the_planning_call_failed(tmp_path):
    """A timeout must not be recorded as "verified, nothing missing"."""
    class _Boom(_HybridLLM):
        def make_plan(self, query, tool_list, feedback=None, world_context=None):
            self.plan_calls += 1
            if self.plan_calls == 1:
                return {"steps": [_step("create_point", tag="NEW_PT", type="analog")]}
            raise RuntimeError("upstream 500")

    llm = _Boom()
    agent = _agent(
        tmp_path, llm,
        plan=PlanExecuteConfig(enabled=True, verify_rounds=1),
        crew=MultiAgentConfig(enabled=False),
    )
    trace = agent.run("建个点位", golden_id="cb-verify-boom", initial_world=_world())

    assert trace["plan"]["verify_rounds"] == 1
    assert trace["plan"]["verify_clean"] is False, "a failed call claimed clean"


@pytest.mark.mock_only
def test_the_cage_and_the_patch_path_agree_on_what_is_destructive(tmp_path):
    """This test used to assert the opposite, and that inversion was the bug.

    It was written to document a gap: ``is_destructive`` was a hand-written
    10-name set covering 10 of the 36 destructive atomics, so the patch path
    screened by verb prefix to keep its "a verify round never deletes" promise
    while the §4.7 cage itself let the other 26 through — including
    ``batch_delete_points``, which every golden case forbids. The cage screens by
    prefix now, so the two must agree rather than one quietly compensating for the
    other.
    """
    from agent.orchestrator import _is_destructive_for_patch
    from agent.policy import is_destructive

    missed_by_the_enumeration = ("purge_history", "delete_user", "unbind_widget_point",
                                 "drop_db_table", "revoke_certificate", "batch_delete_points")
    for name in missed_by_the_enumeration:
        # default is the archived ten-name reading, kept so old results reproduce
        assert not is_destructive(name), f"{name} unexpectedly in the enumeration"
        # ...and `safety.destructive_by_prefix` is what closes the gap
        assert is_destructive(name, by_prefix=True), f"{name} escapes the widened cage"
        # the patch path screens by prefix unconditionally — its promise ("a
        # verification round never deletes") must not depend on a config flag
        assert _is_destructive_for_patch(name), name

    for name in ("create_point", "enable_history", "bind_point"):
        assert not is_destructive(name, by_prefix=True), name
        assert not _is_destructive_for_patch(name), name

    # a read-only name must never be counted against the destructive budget,
    # however wide the reading
    for name in ("list_points", "query_history", "list_pages"):
        assert not is_destructive(name, by_prefix=True), name

    # the enumerated ten are destructive under both readings
    for name in ("delete_page", "delete_point", "disable_alarm"):
        assert is_destructive(name) and is_destructive(name, by_prefix=True), name


def test_verify_instruction_and_the_retry_framing_do_not_drift():
    """``make_plan`` wraps ordinary feedback in "上一版计划执行失败" — the wrong
    framing for a verification round, which opens "计划已执行完毕". The wrapper
    detects the difference by a sentinel, so the sentinel must stay the way
    VERIFY_INSTRUCTION actually starts."""
    from agent.planner import VERIFY_FRAMING_SENTINEL, VERIFY_INSTRUCTION

    assert VERIFY_INSTRUCTION.lstrip().startswith(VERIFY_FRAMING_SENTINEL)
    # And a failure feedback must NOT be mistaken for verify feedback.
    assert not "步骤 create_point 失败: POINT_NOT_FOUND".startswith(
        VERIFY_FRAMING_SENTINEL
    )


def test_prompt_keeps_one_identifier_across_dependent_steps():
    """golden-068 broke in every arm and every rep of both campaigns.

    "先校验再下装staging部署,部署记录叫deploy_staging" was planned as
    ``validate_project(deployment_id="default", target="staging")`` followed by
    ``deploy_project(deployment_id="deploy_staging")`` — different deployments,
    so the §4.7 cage correctly refused the deploy as unvalidated. The cause was
    the entity-grounding rule: told to reuse ids from the world snapshot, the
    planner preferred the pre-existing ``default`` over the name the request had
    just assigned. An explicit new name in the request must win.
    """
    from agent.planner import PLANNER_SYSTEM_PROMPT as P

    assert "明确指定了新名称" in P
    assert "deploy_staging" in P, "the measured failure should be the worked example"
    assert "同一个标识" in P, "cross-step identifier consistency must be stated"


def test_prompt_forbids_creating_a_missing_configure_target():
    """golden-044: "给不存在的NO_SUCH_TEMP配置高温报警" — the request itself says
    the point does not exist, and the correct answer is to say so, not to
    create_point and then attach the alarm. This is the cascade principle applied
    to the *initial* plan; the runtime guard only covers the replan path."""
    from agent.planner import PLANNER_SYSTEM_PROMPT as P

    assert "不要顺手把它创建出来" in P
    assert "NO_SUCH_TEMP" in P
    # Must not contradict the legitimate prerequisite-creation rule.
    assert "照建不误" in P
