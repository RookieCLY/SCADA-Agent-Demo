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
from world import MockWorld, Point

from tests._llm_factory import make_test_model_config

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
            multi_agent=crew if crew is not None else MultiAgentConfig(enabled=True),
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
def test_config_J_turns_everything_on_and_matches_F_surface():
    on = load_config(CONFIGS_DIR / "J_combined.yaml").architecture
    off = load_config(CONFIGS_DIR / "F_full_four_in_one.yaml").architecture
    assert on.plan_execute.enabled and on.react.enabled and on.multi_agent.enabled
    assert on.plan_execute.include_world_context and on.plan_execute.replan_on_compile_drop
    assert on.multi_agent.min_domains == 2
    assert not (off.plan_execute.enabled or off.react.enabled or off.multi_agent.enabled)
    assert (
        on.hierarchical_tools, on.tool_rag.enabled, on.workflow.enabled,
        on.state_machine.enabled, on.resources_separation,
    ) == (
        off.hierarchical_tools, off.tool_rag.enabled, off.workflow.enabled,
        off.state_machine.enabled, off.resources_separation,
    )
