"""Multi-Agent (多智能体协作) — `architecture.multi_agent`.

The single A–H agent carries the whole task in one growing context over a
tool surface ranked across every domain. These tests pin what the crew
structure buys back:

* per-state Specialists with private contexts → `input_tokens`, `cost_usd`
* one domain's tools per Specialist          → `tool_selection_f1`,
                                               `out_of_scope_tool_rate`
* the Blackboard forwards real entity IDs    → `cascade_failure_rate`
* the deterministic Critic retries idle work → `task_success` on multi-domain
                                               cases

and the invariants that must survive: the Supervisor routes with **legal** FSM
transitions only, a Specialist is narrower than the single agent (its own
assignment, checked per call), and the §4.7 cage still refuses before dispatch.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent import orchestrator as orch_mod
from agent.config import (
    ArchitectureConfig,
    ExperimentConfig,
    MultiAgentConfig,
    SafetyPolicyConfig,
    StateMachineConfig,
    load_config,
)
from agent.llm import LLMResponse, LLMToolCall
from agent.multi_agent import (
    Blackboard,
    Subtask,
    critic_feedback,
    route_subtasks,
    state_route,
    unsatisfied_subtasks,
)
from agent.orchestrator import Agent
from agent.tool_registry import build_default_registry
from agent.tracer import Tracer
from world import MockWorld, Point

from tests._llm_factory import make_test_model_config

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"
REGISTRY = build_default_registry()


# ============================================================ helpers
class _CrewLLM:
    """Scripted per-state Specialist backend.

    ``script[state]`` is a queue of responses; each Specialist turn pops one.
    Records every system prompt per state so tests can assert on context
    isolation and Blackboard hand-off.
    """

    def __init__(self, script: dict[str, list[LLMResponse]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.prompts: dict[str, list[str]] = {}
        self.calls = 0
        self.resets = 0

    def call(self, system_prompt, user_query, visible_tools, history, state):
        self.calls += 1
        self.prompts.setdefault(state, []).append(system_prompt)
        queue = self.script.get(state)
        if queue:
            return queue.pop(0)
        return LLMResponse(text="无需处理", tool_calls=[], stop_reason="end_turn")

    def reset(self) -> None:
        self.resets += 1


def _tool(name: str, args: dict) -> LLMResponse:
    return LLMResponse(
        text=None,
        tool_calls=[LLMToolCall(name=name, arguments=args)],
        stop_reason="tool_use",
        input_tokens=100,
        output_tokens=30,
    )


def _text(t: str) -> LLMResponse:
    return LLMResponse(text=t, tool_calls=[], stop_reason="end_turn")


def _agent(
    tmp_path: Path,
    llm,
    *,
    crew: MultiAgentConfig | None = None,
    state_machine: bool = True,
    safety: SafetyPolicyConfig | None = None,
    max_turns: int = 12,
) -> Agent:
    cfg = ExperimentConfig(
        name="crew_test",
        architecture=ArchitectureConfig(
            hierarchical_tools=False,
            state_machine=StateMachineConfig(enabled=state_machine),
            multi_agent=crew if crew is not None else MultiAgentConfig(enabled=True),
        ),
        safety=safety or SafetyPolicyConfig(),
        model=make_test_model_config(force_mock=True),
    )
    tracer = Tracer(results_root=str(tmp_path), config_name=cfg.name, model_name="crew-stub")
    return Agent(config=cfg, registry=REGISTRY, llm=llm, tracer=tracer, max_turns=max_turns)


def _world() -> MockWorld:
    w = MockWorld()
    w.points["TEMP_101"] = Point(tag="TEMP_101", type="analog", unit="C")
    return w


# ============================================================ supervisor routing
def test_subtasks_group_by_owning_state_in_rank_order():
    """The most relevant domain is worked first — rank order is the routing."""
    subtasks = route_subtasks(["create_analog_alarm", "create_point", "enable_alarm"])
    states = [s.state for s in subtasks]
    assert states[0] == "CONFIG_ALARM"
    assert "CONFIG_POINT" in states
    for s in subtasks:
        for atomic in s.atomics:
            from agent.state_machine import STATES

            assert atomic in STATES[s.state].allowed_tools


def test_supervisor_never_assigns_the_non_working_states():
    """ANALYZE_INTENT/ASK_USER/DONE cannot change the world; spending a whole
    Specialist on them would burn turns for zero configurable output."""
    subtasks = route_subtasks(["list_points", "list_pages", "create_point"])
    assert all(s.state not in {"ANALYZE_INTENT", "ASK_USER", "DONE"} for s in subtasks)


def test_specialist_count_and_assignment_size_are_bounded():
    ranked = ["create_point", "create_analog_alarm", "create_page", "create_script",
              "create_rect", "enable_history"]
    subtasks = route_subtasks(ranked, max_specialists=2, tools_per_specialist=1)
    assert len(subtasks) == 2
    assert all(len(s.atomics) == 1 for s in subtasks)


def test_empty_ranking_routes_nothing():
    assert route_subtasks([]) == []


# ============================================================ state routing
def test_route_walks_only_legal_transitions():
    from agent.state_machine import STATES

    route = state_route("CONFIG_ALARM", "CONFIG_POINT")
    assert route
    node = "CONFIG_ALARM"
    for hop in route:
        assert hop in STATES[node].next_states, f"illegal hop {node} → {hop}"
        node = hop
    assert node == "CONFIG_POINT"


def test_route_returns_none_out_of_terminal():
    assert state_route("DONE", "CONFIG_POINT") is None
    assert state_route("CONFIG_POINT", "CONFIG_POINT") == []


# ============================================================ blackboard
def test_blackboard_records_entities_not_field_paths():
    """One line per entity, not one per attribute — the board must stay small
    enough to hand forward without becoming its own token bill."""
    board = Blackboard()
    board.record_diff({
        "added_or_modified": {
            "points.TEMP_201": {"tag": "TEMP_201"},
            "points.TEMP_201.unit": "C",
            "alarms.a1.high_limit": 80.0,
        },
        "removed": [],
    })
    assert board.entities == ["points.TEMP_201", "alarms.a1"]


def test_blackboard_deduplicates_and_ignores_empty_diffs():
    board = Blackboard()
    board.record_diff(None)
    board.record_diff({"added_or_modified": {"points.T1": {}}, "removed": []})
    board.record_diff({"added_or_modified": {"points.T1.unit": "C"}, "removed": []})
    assert board.entities == ["points.T1"]


def test_blackboard_render_lists_real_identifiers():
    board = Blackboard()
    board.record_diff({"added_or_modified": {"points.TEMP_201": {}}, "removed": []})
    text = board.render()
    assert "points.TEMP_201" in text and "协作黑板" in text
    assert Blackboard().render() == ""


# ============================================================ critic
def test_unsatisfied_means_ran_but_changed_nothing():
    ran_ok = Subtask(state="CONFIG_POINT", atomics=[], turns_used=2, successful_calls=1)
    idle = Subtask(state="CONFIG_ALARM", atomics=[], turns_used=2, successful_calls=0)
    skipped = Subtask(state="DEPLOY", atomics=[], turns_used=0, successful_calls=0)
    assert unsatisfied_subtasks([ran_ok, idle, skipped]) == [idle]


def test_critic_feedback_names_the_state():
    msg = critic_feedback(Subtask(state="CONFIG_ALARM", atomics=[]))
    assert "CONFIG_ALARM" in msg and "协作校验" in msg


# ============================================================ end-to-end
@pytest.mark.mock_only
def test_two_domain_task_runs_two_specialists(tmp_path: Path):
    """The decomposition claim: each domain is handled by its own Specialist in
    its own state, and the run closes on DONE."""
    llm = _CrewLLM({
        "CONFIG_ALARM": [
            _tool("create_analog_alarm", {"id": "a1", "tag": "TEMP_101", "high_limit": 80.0}),
            _text("报警已配置"),
        ],
        "CONFIG_POINT": [
            _tool("create_point", {"tag": "TEMP_201", "type": "analog"}),
            _text("点位已创建"),
        ],
    })
    agent = _agent(tmp_path, llm)
    # Pin the ranking the Supervisor routes from (there is no RAG index in this
    # unit fixture; the real config ranks by query relevance).
    agent._rank_with_rag = lambda q, allowed: sorted(  # type: ignore[method-assign]
        allowed, key=lambda t: (t != "create_analog_alarm", t != "create_point", t)
    )
    record = agent.run(
        "新建点位 TEMP_201,并给 TEMP_101 加高温报警", golden_id="ma-two", initial_world=_world()
    )

    ok_calls = [c for c in record["tool_calls"] if c["result_ok"]]
    assert {c["selected"] for c in ok_calls} == {"create_analog_alarm", "create_point"}
    assert record["execution"]["terminal_state"] == "DONE"
    assert record["crew"]["executed"] is True
    assert record["crew"]["specialists"] >= 2
    # Context isolation: each Specialist got a fresh backend conversation.
    assert llm.resets >= 3  # run-start + one per specialist


@pytest.mark.mock_only
def test_states_are_entered_by_legal_transitions_only(tmp_path: Path):
    from agent.state_machine import STATES

    llm = _CrewLLM({
        "CONFIG_ALARM": [
            _tool("create_analog_alarm", {"id": "a1", "tag": "TEMP_101", "high_limit": 80.0}),
            _text("done"),
        ],
        "CONFIG_POINT": [
            _tool("create_point", {"tag": "TEMP_201", "type": "analog"}),
            _text("done"),
        ],
    })
    agent = _agent(tmp_path, llm)
    record = agent.run("点位加报警", golden_id="ma-legal", initial_world=_world())

    visited = [s["name"] for s in record["states"]]
    for prev, nxt in zip(visited, visited[1:], strict=False):
        assert nxt in STATES[prev].next_states, f"illegal transition {prev} → {nxt}"


@pytest.mark.mock_only
def test_blackboard_hands_created_entities_to_the_next_specialist(tmp_path: Path):
    """The cascade-failure prevention: the second Specialist is shown the tag
    the first one actually created, read from the world_diff."""
    llm = _CrewLLM({
        "CONFIG_POINT": [
            _tool("create_point", {"tag": "TEMP_555", "type": "analog"}),
            _text("点位已创建"),
        ],
        "CONFIG_ALARM": [
            _tool("create_analog_alarm", {"id": "a1", "tag": "TEMP_555", "high_limit": 90.0}),
            _text("报警已配置"),
        ],
    })
    # Force point-first rank order so the hand-off direction is deterministic.
    agent = _agent(tmp_path, llm)
    agent._rank_with_rag = lambda q, allowed: sorted(  # type: ignore[method-assign]
        allowed, key=lambda t: (t != "create_point", t != "create_analog_alarm")
    )
    record = agent.run("新建 TEMP_555 并加报警", golden_id="ma-board", initial_world=_world())

    assert record["crew"]["blackboard"]["entities"][0] == "points.TEMP_555"
    alarm_prompts = "\n".join(llm.prompts.get("CONFIG_ALARM", []))
    assert "points.TEMP_555" in alarm_prompts, "the board never reached the second specialist"
    assert all(c["result_ok"] for c in record["tool_calls"])


@pytest.mark.mock_only
def test_specialist_cannot_leave_its_assignment(tmp_path: Path):
    """A Specialist is narrower than the single agent: a call outside its own
    assignment is OUT_OF_SCOPE even if some other state would allow it."""
    llm = _CrewLLM({
        "CONFIG_ALARM": [
            _tool("create_page", {"page_id": "p1", "title": "总览"}),
            _text("done"),
        ],
    })
    agent = _agent(tmp_path, llm, crew=MultiAgentConfig(enabled=True, max_specialists=1))
    agent._rank_with_rag = lambda q, allowed: sorted(  # type: ignore[method-assign]
        allowed, key=lambda t: t != "create_analog_alarm"
    )
    record = agent.run("加报警", golden_id="ma-scope", initial_world=_world())

    blocked = [c for c in record["tool_calls"] if c["error_code"] == "OUT_OF_SCOPE"]
    assert blocked and blocked[0]["selected"] == "create_page"
    assert "其它专家" in blocked[0]["error_msg"]
    assert not any(c["result_ok"] for c in record["tool_calls"])


@pytest.mark.mock_only
def test_the_runtime_policy_still_refuses_inside_a_specialist(tmp_path: Path):
    """§4.7 is evaluated per dispatch, crew or no crew."""
    llm = _CrewLLM({
        "DEPLOY": [
            _tool("deploy_project", {"deployment_id": "d1", "force": True}),
            _text("done"),
        ],
    })
    agent = _agent(
        tmp_path,
        llm,
        crew=MultiAgentConfig(enabled=True, max_specialists=1),
        safety=SafetyPolicyConfig(enabled=True),
    )
    agent._rank_with_rag = lambda q, allowed: sorted(  # type: ignore[method-assign]
        allowed, key=lambda t: t != "deploy_project"
    )
    record = agent.run("强制下装", golden_id="ma-policy", initial_world=_world())

    denied = [c for c in record["tool_calls"] if c["error_code"] == "POLICY_DENIED"]
    assert denied, "the forced deploy was not refused"
    assert record["world_snapshots"]["initial_hash"] == record["world_snapshots"]["final_hash"]


@pytest.mark.mock_only
def test_critic_gives_an_idle_specialist_exactly_one_retry(tmp_path: Path):
    """First pass: the Specialist talks but does nothing. The Critic re-runs it
    once with that stated; the retry succeeds."""
    llm = _CrewLLM({
        "CONFIG_POINT": [
            _text("我以为不需要"),  # pass 1 — no tool call
            _tool("create_point", {"tag": "TEMP_601", "type": "analog"}),  # retry
            _text("补上了"),
        ],
    })
    agent = _agent(tmp_path, llm, crew=MultiAgentConfig(enabled=True, max_specialists=1))
    agent._rank_with_rag = lambda q, allowed: sorted(  # type: ignore[method-assign]
        allowed, key=lambda t: t != "create_point"
    )
    record = agent.run("新建点位 TEMP_601", golden_id="ma-critic", initial_world=_world())

    assert record["crew"]["critic_retries"] == 1
    assert any(c["result_ok"] and c["selected"] == "create_point" for c in record["tool_calls"])
    retry_prompts = "\n".join(llm.prompts["CONFIG_POINT"][1:])
    assert "协作校验" in retry_prompts, "the critic's finding never reached the retry"


@pytest.mark.mock_only
def test_critic_retry_can_be_switched_off(tmp_path: Path):
    llm = _CrewLLM({"CONFIG_POINT": [_text("我以为不需要")]})
    agent = _agent(
        tmp_path, llm,
        crew=MultiAgentConfig(enabled=True, max_specialists=1, critic_retry=False),
    )
    agent._rank_with_rag = lambda q, allowed: sorted(  # type: ignore[method-assign]
        allowed, key=lambda t: t != "create_point"
    )
    record = agent.run("新建点位", golden_id="ma-nocritic", initial_world=_world())
    assert record["crew"]["critic_retries"] == 0
    assert record["tool_calls"] == []


@pytest.mark.mock_only
def test_turn_budget_bounds_the_whole_crew(tmp_path: Path):
    """Specialist turns spend the same global `max_turns` budget the single
    agent has — the crew must not be a way to exceed it."""
    endless = [
        _tool("create_point", {"tag": f"TEMP_7{i:02d}", "type": "analog"}) for i in range(20)
    ]
    llm = _CrewLLM({"CONFIG_POINT": endless})
    agent = _agent(
        tmp_path, llm,
        crew=MultiAgentConfig(enabled=True, max_specialists=1, turns_per_specialist=99),
        max_turns=3,
    )
    agent._rank_with_rag = lambda q, allowed: sorted(  # type: ignore[method-assign]
        allowed, key=lambda t: t != "create_point"
    )
    record = agent.run("建很多点位", golden_id="ma-budget", initial_world=_world())

    assert record["execution"]["total_turns"] == 3
    assert record["execution"]["early_terminated"] is True


@pytest.mark.mock_only
def test_no_decomposition_falls_back_to_the_single_agent_loop(tmp_path: Path, monkeypatch):
    """Supervisor abstains → the archived single-agent behaviour, unchanged."""
    class _SingleLLM:
        def __init__(self) -> None:
            self.calls = 0

        def call(self, system_prompt, user_query, visible_tools, history, state):
            self.calls += 1
            return _text("nothing to do")

        def reset(self) -> None:
            return None

    monkeypatch.setattr(orch_mod, "route_subtasks", lambda *a, **k: [])
    llm = _SingleLLM()
    agent = _agent(tmp_path, llm, max_turns=2)
    record = agent.run("完全无关的问题", golden_id="ma-fallback", initial_world=_world())

    assert llm.calls >= 1, "the single-agent loop did not take over"
    assert record["crew"].get("executed") is not True


# ============================================================ config wiring
def test_config_I_is_F_plus_the_lever():
    on = load_config(CONFIGS_DIR / "I_multi_agent.yaml").architecture
    off = load_config(CONFIGS_DIR / "F_full_four_in_one.yaml").architecture
    assert on.multi_agent.enabled is True
    assert off.multi_agent.enabled is False
    assert (
        on.hierarchical_tools,
        on.tool_rag.enabled,
        on.workflow.enabled,
        on.state_machine.enabled,
        on.resources_separation,
    ) == (
        off.hierarchical_tools,
        off.tool_rag.enabled,
        off.workflow.enabled,
        off.state_machine.enabled,
        off.resources_separation,
    )
