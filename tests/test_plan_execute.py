"""Plan-and-Execute (规划-执行) — `architecture.plan_execute`.

The interleaved A–H loop pays one LLM call per tool call and checks nothing
before dispatching. These tests pin what the structure buys back:

* one planning call for an N-step task  → `total_turns`, `input_tokens`, `cost_usd`
* hallucinated names dropped at compile → `hallucinated_tool_rate`
* arguments validated at compile        → `schema_violation_rate`
* dependency-driven reordering          → `cascade_failure_rate`, `order_correctness`

and the invariants that must survive: the state machine still gates execution
(legal transitions only), the §4.7 cage still refuses before dispatch, and a
backend that cannot plan degrades to the unchanged interleaved loop.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import (
    ArchitectureConfig,
    ExperimentConfig,
    PlanExecuteConfig,
    SafetyPolicyConfig,
    StateMachineConfig,
    load_config,
)
from agent.llm import LLMResponse, LLMToolCall, _extract_json_object
from agent.orchestrator import Agent
from agent.planner import (
    compile_plan,
    describe_tools_for_planner,
    state_route,
    states_exposing,
    summarize_world_for_planner,
)
from agent.tool_registry import build_default_registry
from agent.tracer import Tracer
from world import MockWorld, Point

from tests._llm_factory import make_test_model_config

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"
REGISTRY = build_default_registry()


# ============================================================ helpers
class _PlanningLLM:
    """Returns a scripted plan from `make_plan`; its `call` is a hard error.

    Making `call` raise is the assertion that matters most: if the executor
    ever reaches for the interleaved loop, the cost claim is void.
    """

    def __init__(self, *plans) -> None:
        self.plans = list(plans)
        self.plan_calls = 0
        self.feedbacks: list[str | None] = []

    def make_plan(self, query, tool_list, feedback=None):
        self.plan_calls += 1
        self.feedbacks.append(feedback)
        self.last_tool_list = tool_list
        if not self.plans:
            return {"steps": []}
        return self.plans.pop(0)

    def call(self, system_prompt, user_query, visible_tools, history, state):
        raise AssertionError("the interleaved loop must not run once a plan compiled")

    def reset(self) -> None:
        return None


class _AbstainingLLM:
    """No `make_plan` at all — e.g. MockLLM. Must degrade gracefully."""

    def __init__(self) -> None:
        self.calls = 0

    def call(self, system_prompt, user_query, visible_tools, history, state):
        self.calls += 1
        return LLMResponse(
            text=None,
            tool_calls=[
                LLMToolCall(name="create_point", arguments={"tag": "TEMP_900", "type": "analog"})
            ],
            stop_reason="tool_use",
        )

    def reset(self) -> None:
        return None


def _agent(
    tmp_path: Path,
    llm,
    *,
    plan: PlanExecuteConfig | None = None,
    state_machine: bool = True,
    safety: SafetyPolicyConfig | None = None,
    max_turns: int = 12,
) -> Agent:
    cfg = ExperimentConfig(
        name="plan_test",
        architecture=ArchitectureConfig(
            hierarchical_tools=False,
            state_machine=StateMachineConfig(enabled=state_machine),
            plan_execute=plan if plan is not None else PlanExecuteConfig(enabled=True),
        ),
        safety=safety or SafetyPolicyConfig(),
        model=make_test_model_config(force_mock=True),
    )
    tracer = Tracer(results_root=str(tmp_path), config_name=cfg.name, model_name="plan-stub")
    return Agent(
        config=cfg, registry=REGISTRY, llm=llm, tracer=tracer, max_turns=max_turns
    )


def _world() -> MockWorld:
    w = MockWorld()
    w.points["TEMP_101"] = Point(tag="TEMP_101", type="analog", unit="C")
    return w


def _step(tool, **arguments):
    return {"tool": tool, "arguments": arguments, "rationale": "t"}


# ============================================================ state routing
def test_route_to_self_is_empty():
    assert state_route("CONFIG_ALARM", "CONFIG_ALARM") == []


def test_route_walks_only_legal_transitions():
    """The router must never invent an edge — the whitelist graph *is* the cage."""
    from agent.state_machine import STATES

    route = state_route("CONFIG_ALARM", "CONFIG_POINT")
    assert route, "CONFIG_POINT should be reachable via VALIDATE → ANALYZE_INTENT"
    node = "CONFIG_ALARM"
    for hop in route:
        assert hop in STATES[node].next_states, f"illegal hop {node} → {hop}"
        node = hop
    assert node == "CONFIG_POINT"


def test_route_returns_none_out_of_a_terminal_state():
    assert state_route("DONE", "CONFIG_POINT") is None


def test_states_exposing_finds_the_owning_state():
    assert "CONFIG_ALARM" in states_exposing("create_analog_alarm")
    assert states_exposing("definitely_not_a_tool") == []


# ============================================================ compile: rejections
def test_hallucinated_tool_never_reaches_dispatch():
    plan = compile_plan(
        [_step("create_point", tag="T1", type="analog"), _step("summon_unicorn", x=1)],
        REGISTRY,
        MockWorld(),
    )
    assert [s.tool for s in plan.steps] == ["create_point"]
    assert plan.diagnostics.dropped_unknown_tool == ["summon_unicorn"]


def test_schema_invalid_step_is_dropped_not_dispatched():
    """`SCHEMA_ERROR` is prevented here rather than recorded by the dispatcher."""
    plan = compile_plan(
        [_step("create_point", tag="T1"), _step("create_point", tag="T2", type="analog")],
        REGISTRY,
        MockWorld(),
    )
    assert [s.tool for s in plan.steps] == ["create_point"]
    assert plan.steps[0].arguments["tag"] == "T2"
    assert plan.diagnostics.dropped_schema_invalid == ["create_point"]


def test_duplicate_steps_collapse():
    plan = compile_plan(
        [
            _step("create_point", tag="T1", type="analog"),
            _step("create_point", tag="T1", type="analog"),
        ],
        REGISTRY,
        MockWorld(),
    )
    assert len(plan.steps) == 1
    assert plan.diagnostics.dropped_duplicate == ["create_point"]


def test_max_steps_is_a_hard_ceiling():
    raw = [_step("create_point", tag=f"T{i}", type="analog") for i in range(10)]
    plan = compile_plan(raw, REGISTRY, MockWorld(), max_steps=3)
    assert len(plan.steps) == 3
    assert plan.diagnostics.dropped_over_budget == 7


def test_allowed_atomics_is_enforced_at_compile_time():
    plan = compile_plan(
        [_step("create_point", tag="T1", type="analog")],
        REGISTRY,
        MockWorld(),
        allowed_atomics=["create_analog_alarm"],
    )
    assert plan.steps == []
    assert plan.diagnostics.dropped_unknown_tool == ["create_point"]


def test_domain_style_step_is_accepted():
    """A planner may name the Domain Tool with an `action` discriminator; both
    tool views have to compile, or the lever would only work under one of them."""
    plan = compile_plan(
        [{"tool": "manage_points", "arguments": {"action": "create_point", "tag": "T1",
                                                 "type": "analog"}}],
        REGISTRY,
        MockWorld(),
    )
    assert [s.tool for s in plan.steps] == ["create_point"]


def test_domain_prefixed_atomic_is_unwrapped_not_dropped():
    """Under replan pressure models qualify the atomic with its domain
    ("manage_pages.create_page") — the tool is real, the spelling is not.
    results_w23 dropped it as *unknown* on golden-069/-073 and the page was
    never created. A suffix that is not a known atomic must still die."""
    plan = compile_plan(
        [
            {"tool": "manage_points.create_point",
             "arguments": {"tag": "T1", "type": "analog"}},
            {"tool": "manage_unicorns.summon_unicorn", "arguments": {}},
        ],
        REGISTRY,
        MockWorld(),
    )
    assert [s.tool for s in plan.steps] == ["create_point"]
    assert plan.diagnostics.dropped_unknown_tool == ["manage_unicorns.summon_unicorn"]


def test_hex_literal_case_is_normalized_to_documented_form():
    """golden-007, 3 of 3 reps: the model wrote ``#ffffff`` itself, so the
    CSS-name map never fired, and the lowercase literal landed verbatim against
    a catalogue whose every documented hex default is uppercase."""
    plan = compile_plan(
        [_step("set_page_background", page_id="main_page", background="#ffffff")],
        REGISTRY,
        MockWorld(),
    )
    assert plan.steps and plan.steps[0].arguments["background"] == "#FFFFFF"


def test_world_summary_includes_histories_and_deployments():
    """The snapshot is the planner's only view of the world, so an omitted
    collection is an entity that does not exist: golden-104's world holds only
    ``histories.TEMP_101`` and the planner clarified "TEMP_101 不存在" in 3 of 3
    reps for a request the flat baseline satisfied every time."""
    from world import Deployment, HistoryConfig

    w = MockWorld()
    w.histories["TEMP_101"] = HistoryConfig(tag="TEMP_101")
    w.deployments["deploy_staging"] = Deployment(id="deploy_staging")
    text = summarize_world_for_planner(w)
    assert "TEMP_101" in text and "histories" in text
    assert "deploy_staging" in text and "deployments" in text


def test_split_limit_step_is_pulled_into_the_creator():
    """59 of 107 replayed w23 drops: ``create_analog_alarm`` with no limit while
    the model's own next step was ``set_threshold(id=..., high_limit=80)``. The
    donated value comes from the plan itself, never from a guess."""
    plan = compile_plan(
        [
            _step("create_point", tag="PT-100", type="analog"),
            _step("create_analog_alarm", id="PT-100_H", tag="PT-100"),
            _step("set_threshold", id="PT-100_H", high_limit=80),
        ],
        REGISTRY,
        MockWorld(),
    )
    tools = [s.tool for s in plan.steps]
    assert "create_analog_alarm" in tools
    create = plan.steps[tools.index("create_analog_alarm")]
    assert create.arguments["high_limit"] == 80
    assert plan.diagnostics.dropped_schema_invalid == []


def test_split_position_step_is_pulled_into_the_creator():
    """golden-048: ``create_valve`` without the required position, followed by
    ``move_widget([300,120])`` on the same widget."""
    plan = compile_plan(
        [
            _step("create_valve", page_id="p1", widget_id="valve1"),
            _step("move_widget", page_id="p1", widget_id="valve1", position=[300, 120]),
        ],
        REGISTRY,
        MockWorld(),
    )
    tools = [s.tool for s in plan.steps]
    assert "create_valve" in tools
    create = plan.steps[tools.index("create_valve")]
    assert tuple(create.arguments["position"]) == (300, 120)


def test_bare_key_is_qualified_when_exactly_one_field_matches():
    """``set_alarm_high_limit`` takes ``alarm_id``; the planner writes ``id`` —
    22 drops in the w23 replay. Renamed only when unambiguous."""
    plan = compile_plan(
        [_step("set_alarm_high_limit", id="A1", high_limit=90)],
        REGISTRY,
        MockWorld(),
    )
    assert plan.steps and plan.steps[0].arguments["alarm_id"] == "A1"


def test_out_of_bounds_numeric_is_clamped_to_the_documented_bound():
    """``query_history(max_samples=5000)`` against ``le=1000``: dropped three
    times in results_w23 for asking too precisely. The clamp is the tool's own
    contract, not a guess."""
    plan = compile_plan(
        [_step("query_history", tag="T1", max_samples=5000)],
        REGISTRY,
        MockWorld(),
    )
    assert plan.steps and plan.steps[0].arguments["max_samples"] == 1000


def test_invalid_optional_field_does_not_sink_the_step():
    """A bad ``expected_binding_types`` entry killed six otherwise-valid
    ``create_widget`` steps in the replay; required fields must win."""
    plan = compile_plan(
        [_step("create_widget", page_id="p1", widget_id="w1", type="pump",
               position=[10, 10], size=[50, 50],
               expected_binding_types={"status": "digital"})],
        REGISTRY,
        MockWorld(),
    )
    assert plan.steps and plan.steps[0].tool == "create_widget"
    assert plan.steps[0].arguments.get("expected_binding_types") in (None, {})


def test_every_rejection_is_named_in_the_diagnostics():
    """A silently shortened plan would read as a well-planned one."""
    plan = compile_plan(
        [_step("nope"), _step("create_point", tag="T1"), _step("create_point", tag="T2",
                                                               type="analog")],
        REGISTRY,
        MockWorld(),
    )
    d = plan.diagnostics.as_dict()
    assert d["proposed"] == 3 and d["compiled"] == 1
    assert d["dropped_unknown_tool"] and d["dropped_schema_invalid"]


# ============================================================ compile: ordering
def test_producer_is_moved_ahead_of_its_consumer():
    """The cascade-failure prevention. `create_analog_alarm` references
    `points.TEMP_777`, which `create_point` intends to create."""
    plan = compile_plan(
        [
            _step("create_analog_alarm", id="a1", tag="TEMP_777", high_limit=80.0),
            _step("create_point", tag="TEMP_777", type="analog"),
        ],
        REGISTRY,
        MockWorld(),
    )
    assert [s.tool for s in plan.steps] == ["create_point", "create_analog_alarm"]
    assert plan.diagnostics.reordered is True


def test_no_reorder_when_the_entity_already_exists():
    """An entity already in the world imposes no ordering constraint — inventing
    one would shuffle a perfectly good plan for nothing."""
    plan = compile_plan(
        [
            _step("create_analog_alarm", id="a1", tag="TEMP_101", high_limit=80.0),
            _step("create_point", tag="TEMP_202", type="analog"),
        ],
        REGISTRY,
        _world(),
    )
    assert [s.tool for s in plan.steps] == ["create_analog_alarm", "create_point"]
    assert plan.diagnostics.reordered is False


def test_reordering_can_be_switched_off():
    plan = compile_plan(
        [
            _step("create_analog_alarm", id="a1", tag="TEMP_777", high_limit=80.0),
            _step("create_point", tag="TEMP_777", type="analog"),
        ],
        REGISTRY,
        MockWorld(),
        reorder=False,
    )
    assert [s.tool for s in plan.steps] == ["create_analog_alarm", "create_point"]


def test_self_referencing_step_does_not_deadlock():
    """`update_point` both intends and references the same entity; a self-edge
    would make the topological sort drop every step."""
    plan = compile_plan(
        [_step("update_point", tag="TEMP_101", unit="K")], REGISTRY, _world()
    )
    assert len(plan.steps) == 1


# ============================================================ planner prompt
def test_catalogue_lists_required_fields():
    """Telling the planner what a tool needs is cheaper than dropping its step."""
    text = describe_tools_for_planner(REGISTRY, ["create_point"])
    assert "create_point" in text and "必填" in text and "tag" in text
    assert "action" not in text.split("必填")[1].split("\n")[0]


def test_catalogue_lists_the_remainder_by_name():
    """The docode-trial fix: tools past the detail budget are still *named*
    (grouped per domain), so the planner can never conclude the catalogue lacks
    a tool it needs and refuse a legitimate task (golden-025's refusal was
    exactly '可用工具清单中没有 validate_project/deploy_project')."""
    text = describe_tools_for_planner(REGISTRY, ["create_point", "update_point"], max_tools=1)
    assert "其余可用工具" in text
    assert "manage_points: update_point" in text
    # The detailed section still carries the schema info for the ranked head.
    assert "必填" in text.split("其余可用工具")[0]


# ============================================================ JSON extraction
def test_json_survives_fences_and_preamble():
    assert _extract_json_object('好的\n```json\n{"steps": []}\n```') == {"steps": []}
    assert _extract_json_object('{"steps": [{"tool": "a"}]}')["steps"][0]["tool"] == "a"


def test_braces_inside_strings_do_not_break_extraction():
    assert _extract_json_object('{"refusal": "不要用 {force: true}"}') == {
        "refusal": "不要用 {force: true}"
    }


def test_unparseable_reply_yields_none():
    assert _extract_json_object("I cannot help with that.") is None


# ============================================================ end-to-end
@pytest.mark.mock_only
def test_a_three_step_task_costs_one_llm_call(tmp_path: Path):
    """The headline cost claim: N tool calls, one planning call."""
    llm = _PlanningLLM(
        {
            "steps": [
                _step("create_point", tag="TEMP_301", type="analog"),
                _step("create_point", tag="TEMP_302", type="analog"),
                _step("create_analog_alarm", id="a1", tag="TEMP_301", high_limit=80.0),
            ]
        }
    )
    agent = _agent(tmp_path, llm)
    record = agent.run("建两个点位并加个报警", golden_id="pe-cost", initial_world=_world())

    assert llm.plan_calls == 1
    assert record["execution"]["total_turns"] == 1
    assert len(record["tool_calls"]) == 3
    assert all(c["result_ok"] for c in record["tool_calls"])
    assert record["execution"]["terminal_state"] == "DONE"
    assert record["execution"]["early_terminated"] is False


@pytest.mark.mock_only
def test_execution_crosses_states_by_legal_transitions_only(tmp_path: Path):
    from agent.state_machine import STATES

    llm = _PlanningLLM(
        {
            "steps": [
                _step("create_point", tag="TEMP_301", type="analog"),
                _step("create_analog_alarm", id="a1", tag="TEMP_301", high_limit=80.0),
            ]
        }
    )
    agent = _agent(tmp_path, llm)
    record = agent.run("建点位并加报警", golden_id="pe-states", initial_world=_world())

    visited = [s["name"] for s in record["states"]]
    assert "CONFIG_POINT" in visited and "CONFIG_ALARM" in visited
    for prev, nxt in zip(visited, visited[1:], strict=False):
        assert nxt in STATES[prev].next_states, f"illegal transition {prev} → {nxt}"


@pytest.mark.mock_only
def test_dependency_repair_prevents_the_cascade_failure(tmp_path: Path):
    """Plan proposed backwards; every call still succeeds because the compiler
    put the producer first."""
    llm = _PlanningLLM(
        {
            "steps": [
                _step("create_analog_alarm", id="a1", tag="TEMP_777", high_limit=80.0),
                _step("create_point", tag="TEMP_777", type="analog"),
            ]
        }
    )
    agent = _agent(tmp_path, llm)
    record = agent.run("给 TEMP_777 加报警", golden_id="pe-order", initial_world=_world())

    assert [c["selected"] for c in record["tool_calls"]] == [
        "create_point",
        "create_analog_alarm",
    ]
    assert all(c["result_ok"] for c in record["tool_calls"])
    assert record["plan"]["reordered"] is True


@pytest.mark.mock_only
def test_failed_step_triggers_a_bounded_replan(tmp_path: Path):
    llm = _PlanningLLM(
        {"steps": [_step("create_point", tag="TEMP_101", type="analog")]},  # already exists
        {"steps": [_step("create_point", tag="TEMP_303", type="analog")]},
    )
    agent = _agent(tmp_path, llm, plan=PlanExecuteConfig(enabled=True, max_replans=1))
    record = agent.run("建个点位", golden_id="pe-replan", initial_world=_world())

    assert llm.plan_calls == 2
    assert llm.feedbacks[1] and "create_point" in llm.feedbacks[1]
    assert record["plan"]["replans"] == 1
    assert record["plan"]["completed"] is True
    assert "TEMP_303" in str(record["tool_calls"][-1]["args"])


@pytest.mark.mock_only
def test_replans_are_bounded(tmp_path: Path):
    """Without a bound this is the interleaved loop again, one call per failure."""
    failing = {"steps": [_step("create_point", tag="TEMP_101", type="analog")]}
    llm = _PlanningLLM(failing, dict(failing), dict(failing), dict(failing))
    agent = _agent(tmp_path, llm, plan=PlanExecuteConfig(enabled=True, max_replans=1))
    record = agent.run("建个点位", golden_id="pe-bound", initial_world=_world())

    assert llm.plan_calls == 2
    assert record["plan"]["completed"] is False
    assert record["execution"]["termination_reason"] == "plan_step_failed"


@pytest.mark.mock_only
def test_a_replan_does_not_redo_completed_work(tmp_path: Path):
    """A replan re-proposes steps that already succeeded; applying them twice
    would corrupt the world and inflate `step_count`."""
    llm = _PlanningLLM(
        {
            "steps": [
                _step("create_point", tag="TEMP_401", type="analog"),
                _step("create_point", tag="TEMP_101", type="analog"),  # fails
            ]
        },
        {
            "steps": [
                _step("create_point", tag="TEMP_401", type="analog"),  # already done
                _step("create_point", tag="TEMP_402", type="analog"),
            ]
        },
    )
    agent = _agent(tmp_path, llm, plan=PlanExecuteConfig(enabled=True, max_replans=1))
    record = agent.run("建点位", golden_id="pe-idem", initial_world=_world())

    dispatched = [c["args"]["tag"] for c in record["tool_calls"]]
    assert dispatched.count("TEMP_401") == 1
    assert "TEMP_402" in dispatched


# ============================================================ cages
@pytest.mark.mock_only
def test_the_runtime_policy_still_refuses_before_dispatch(tmp_path: Path):
    """§4.7 is evaluated on compiled steps too — planning must not be a way
    around the outer cage, and a denial must not trigger a replan."""
    llm = _PlanningLLM(
        {"steps": [_step("deploy_project", deployment_id="d1", force=True)]},
        {"steps": [_step("deploy_project", deployment_id="d2", force=True)]},
    )
    agent = _agent(
        tmp_path,
        llm,
        plan=PlanExecuteConfig(enabled=True, max_replans=2),
        safety=SafetyPolicyConfig(enabled=True),
    )
    record = agent.run("强制下装,跳过校验", golden_id="pe-policy", initial_world=_world())

    denied = [c for c in record["tool_calls"] if c["error_code"] == "POLICY_DENIED"]
    assert denied, "the forced deploy was not refused"
    assert llm.plan_calls == 1, "a policy denial must not be replanned around"
    assert record["execution"]["termination_reason"] == "policy_denied"
    assert record["world_snapshots"]["initial_hash"] == record["world_snapshots"]["final_hash"]


@pytest.mark.mock_only
def test_a_refusal_ends_the_run_without_touching_the_world(tmp_path: Path):
    """The golden `reject` cases: an empty plan *with* a refusal is a result,
    not a planner failure, so it must not fall back into the loop."""
    llm = _PlanningLLM({"steps": [], "refusal": "跳过校验下装风险过高"})
    agent = _agent(tmp_path, llm)
    record = agent.run("强制下装", golden_id="pe-refuse", initial_world=_world())

    assert record["tool_calls"] == []
    assert record["plan"]["refusal"] == "跳过校验下装风险过高"
    assert record["world_snapshots"]["initial_hash"] == record["world_snapshots"]["final_hash"]


@pytest.mark.mock_only
def test_backend_without_a_planner_falls_back_to_the_interleaved_loop(tmp_path: Path):
    """MockLLM has no usable planner; the archived behaviour must be unchanged."""
    llm = _AbstainingLLM()
    agent = _agent(tmp_path, llm, state_machine=False, max_turns=2)
    record = agent.run("建个点位", golden_id="pe-fallback", initial_world=_world())

    assert llm.calls == 2, "the interleaved loop did not take over"
    assert record["plan"]["supported"] is False
    assert record["execution"]["total_turns"] == 2


@pytest.mark.mock_only
def test_empty_plan_falls_back_when_configured(tmp_path: Path):
    llm_plans_nothing = _PlanningLLM({"steps": []})
    llm_plans_nothing.call = _AbstainingLLM().call  # type: ignore[method-assign]
    agent = _agent(tmp_path, llm_plans_nothing, state_machine=False, max_turns=1)
    record = agent.run("做点什么", golden_id="pe-empty", initial_world=_world())
    assert record["plan"]["executed"] is False
    assert len(record["tool_calls"]) == 1


# ============================================================ config wiring
def test_config_I_is_F_plus_the_lever():
    on = load_config(CONFIGS_DIR / "I_plan_execute.yaml").architecture
    off = load_config(CONFIGS_DIR / "F_full_four_in_one.yaml").architecture
    assert on.plan_execute.enabled is True
    assert off.plan_execute.enabled is False
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
