"""ReAct (Reasoning + Acting) turn structure — `architecture.react`.

The act-only A–H loop discards the model's reasoning between turns and threads
every raw tool payload back verbatim. These tests pin the three behaviours that
buy the paper's metrics back:

* observation compression      → `input_tokens` / `cost_usd`
* repeat-action suppression    → `step_efficiency` (ideal_steps / step_count)
* error-code-keyed repair hints→ `schema_violation_rate`, `cascade_failure_rate`

and the one invariant that must survive them: dedupe runs *after* the cages, so
it can never turn a blocked call into an executed one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import (
    ArchitectureConfig,
    ExperimentConfig,
    ReActConfig,
    StateMachineConfig,
    load_config,
)
from agent.llm import LLMResponse, LLMToolCall
from agent.orchestrator import Agent
from agent.react import (
    ReActScratchpad,
    action_signature,
    compress_result_data,
    repair_hint,
)
from agent.tool_registry import build_default_registry
from agent.tracer import Tracer
from world import MockWorld, Point

from tests._llm_factory import make_test_model_config

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"


# ============================================================ helpers
class _RepeatingLLM:
    """Emits the identical tool call every turn — the redundancy ReAct exists
    to absorb. Models do this whenever a result is ambiguous to them."""

    def __init__(self, name: str, args: dict, text: str | None = None) -> None:
        self.name = name
        self.args = args
        self.text = text
        self.calls = 0

    def call(self, system_prompt, user_query, visible_tools, history, state):
        self.calls += 1
        self.last_system_prompt = system_prompt
        return LLMResponse(
            text=self.text,
            tool_calls=[LLMToolCall(name=self.name, arguments=dict(self.args))],
            stop_reason="tool_use",
        )

    def reset(self) -> None:
        return None


def _agent(tmp_path: Path, llm, react: ReActConfig, *, max_turns: int = 4) -> Agent:
    cfg = ExperimentConfig(
        name="react_test",
        architecture=ArchitectureConfig(
            hierarchical_tools=False,
            state_machine=StateMachineConfig(enabled=False),
            react=react,
        ),
        model=make_test_model_config(force_mock=True),
    )
    tracer = Tracer(
        results_root=str(tmp_path), config_name=cfg.name, model_name="react-stub"
    )
    return Agent(
        config=cfg,
        registry=build_default_registry(),
        llm=llm,
        tracer=tracer,
        max_turns=max_turns,
    )


def _world() -> MockWorld:
    w = MockWorld()
    w.points["TEMP_101"] = Point(tag="TEMP_101", type="analog", unit="C")
    return w


# ============================================================ observation compression
def test_compress_truncates_lists_and_says_so():
    """Silent truncation would trade tokens for wrong answers — the marker is
    what lets the model know the result was larger than what it can see."""
    payload = {"points": [f"TAG_{i}" for i in range(20)], "count": 20}
    out = compress_result_data(payload, max_items=3)
    assert out["points"][:3] == ["TAG_0", "TAG_1", "TAG_2"]
    assert out["points"][-1] == "…(+17 more)"
    assert out["count"] == 20


def test_compress_clips_long_strings_and_recurses():
    payload = {"body": "x" * 500, "nested": {"items": list(range(10))}}
    out = compress_result_data(payload, max_items=2, max_chars=50)
    assert out["body"].endswith("…") and len(out["body"]) == 51
    assert out["nested"]["items"] == [0, 1, "…(+8 more)"]


def test_compress_leaves_small_payloads_untouched():
    payload = {"tag": "TEMP_101", "ok": True, "samples": [1, 2]}
    assert compress_result_data(payload) == payload


# ============================================================ repair hints
def test_not_found_hint_names_the_missing_entity():
    """`cascade_failure_rate` counts a later call referencing an entity an
    earlier call failed to create. Naming it is what makes that recoverable."""
    hint = repair_hint("POINT_NOT_FOUND", "create_analog_alarm", {"tag": "TEMP_999"})
    assert hint is not None
    assert "TEMP_999" in hint
    assert "point" in hint


def test_schema_error_hint_says_stay_on_the_same_tool():
    hint = repair_hint("SCHEMA_ERROR", "create_point", {"tag": "T1"})
    assert hint is not None and "不要改用别的工具" in hint


def test_no_hint_for_success():
    assert repair_hint("OK", "create_point", {}) is None
    assert repair_hint(None, "create_point", {}) is None


# ============================================================ action signatures
def test_signature_is_argument_order_insensitive():
    a = action_signature("create_point", {"tag": "T1", "type": "analog"})
    b = action_signature("create_point", {"type": "analog", "tag": "T1"})
    assert a == b


def test_flat_and_hierarchical_views_of_one_call_share_a_signature():
    """Keyed on the atomic, so the dedupe rule behaves identically in both tool
    views — otherwise the paper's flat-vs-hierarchical comparison would be
    confounded by a lever that only fires on one side."""
    flat = action_signature("create_point", {"tag": "T1"})
    hier = action_signature("create_point", {"action": "create_point", "tag": "T1"})
    assert flat == hier
    assert flat != action_signature("delete_point", {"tag": "T1"})


def test_signature_distinguishes_different_arguments():
    assert action_signature("create_point", {"tag": "T1"}) != action_signature(
        "create_point", {"tag": "T2"}
    )


# ============================================================ scratchpad
def _observe(pad: ReActScratchpad, *, turn=1, tool="create_point", args=None, ok=True, changed=True):
    return pad.observe(
        turn=turn,
        tool=tool,
        action=None,
        atomic=tool,
        args=args if args is not None else {"tag": "T1"},
        ok=ok,
        error_code="OK" if ok else "SCHEMA_ERROR",
        error_msg=None if ok else "bad args",
        data={"tag": "T1"},
        world_changed=changed,
    )


def test_identical_successful_action_is_cached():
    pad = ReActScratchpad()
    _observe(pad)
    hit = pad.cached(action_signature("create_point", {"tag": "T1"}))
    assert hit is not None and hit.ok is True


def test_failed_action_is_not_cached():
    """Suppressing a retry after a failure would strand the run — the point of
    the hint is that the model *should* try again, differently."""
    pad = ReActScratchpad()
    _observe(pad, ok=False, changed=False)
    assert pad.cached(action_signature("create_point", {"tag": "T1"})) is None


def test_world_mutation_invalidates_the_cache():
    """A re-read after a write, or a re-create after a delete, is not a repeat.
    Without the epoch guard the dedupe rule would silently return stale data."""
    pad = ReActScratchpad()
    _observe(pad, tool="list_points", args={}, changed=False)
    sig = action_signature("list_points", {})
    assert pad.cached(sig) is not None
    _observe(pad, turn=2, tool="create_point", args={"tag": "T9"}, changed=True)
    assert pad.cached(sig) is None, "stale observation survived a world mutation"


def test_failed_observation_carries_the_hint_to_the_model():
    pad = ReActScratchpad()
    _step, _data, message = pad.observe(
        turn=1, tool="create_analog_alarm", action=None, atomic="create_analog_alarm",
        args={"tag": "TEMP_999"},
        ok=False, error_code="POINT_NOT_FOUND", error_msg="point missing",
        data={}, world_changed=False,
    )
    assert "point missing" in message
    assert "修复建议" in message and "TEMP_999" in message


def test_hints_can_be_switched_off():
    pad = ReActScratchpad(repair_hints=False)
    _step, _data, message = pad.observe(
        turn=1, tool="create_analog_alarm", action=None, atomic="create_analog_alarm",
        args={"tag": "TEMP_999"},
        ok=False, error_code="POINT_NOT_FOUND", error_msg="point missing",
        data={}, world_changed=False,
    )
    assert message == "point missing"


def test_render_is_bounded_by_the_window():
    """The scratchpad must stay a fixed-size summary; an unbounded one would
    reintroduce exactly the token growth it exists to remove."""
    pad = ReActScratchpad(window=2)
    for i in range(6):
        pad.record_thought(i, f"思考: step {i}")
        _observe(pad, turn=i, args={"tag": f"T{i}"}, changed=False)
    block = pad.render()
    assert "step 5" in block and "step 4" in block
    assert "step 0" not in block
    assert "更早的 4 步已省略" in block


def test_render_is_empty_before_anything_happens():
    assert ReActScratchpad().render() == ""


def test_thought_extraction_strips_chatter_and_control_flow():
    pad = ReActScratchpad()
    pad.record_thought(1, "好的。思考: 先创建点位 TEMP_201。\nnext_state: CONFIG_POINT")
    assert pad.steps[0].thought == "先创建点位 TEMP_201。"


def test_thought_falls_back_to_provider_reasoning():
    pad = ReActScratchpad()
    pad.record_thought(1, None, "需要先查询点位列表")
    assert pad.steps[0].thought == "需要先查询点位列表"


# ============================================================ end-to-end
@pytest.mark.mock_only
def test_repeat_call_is_answered_from_the_scratchpad(tmp_path: Path):
    """The `step_efficiency` win: a model stuck re-issuing the same successful
    call dispatches it exactly once."""
    llm = _RepeatingLLM("create_point", {"tag": "TEMP_201", "type": "analog"})
    agent = _agent(tmp_path, llm, ReActConfig(enabled=True), max_turns=4)
    record = agent.run("新建点位 TEMP_201", golden_id="react-dedupe", initial_world=_world())

    assert llm.calls == 4, "the model kept asking — that is the premise"
    assert len(record["tool_calls"]) == 1, "the repeat was dispatched again"
    assert record["tool_calls"][0]["result_ok"] is True
    assert record["react"]["suppressed_repeats"] == 3
    assert record["react"]["enabled"] is True


@pytest.mark.mock_only
def test_without_react_every_repeat_is_dispatched(tmp_path: Path):
    """Baseline arm for the A/B — same model, lever off."""
    llm = _RepeatingLLM("create_point", {"tag": "TEMP_201", "type": "analog"})
    agent = _agent(tmp_path, llm, ReActConfig(enabled=False), max_turns=4)
    record = agent.run("新建点位 TEMP_201", golden_id="react-off", initial_world=_world())

    assert len(record["tool_calls"]) == 4
    assert record["react"] == {"enabled": False}


@pytest.mark.mock_only
def test_dedupe_can_be_switched_off_independently(tmp_path: Path):
    """Compression and hints without dedupe — needed to attribute the
    `step_efficiency` movement to the dedupe rule alone."""
    llm = _RepeatingLLM("create_point", {"tag": "TEMP_201", "type": "analog"})
    agent = _agent(
        tmp_path, llm, ReActConfig(enabled=True, dedupe_repeat_actions=False), max_turns=3
    )
    record = agent.run("新建点位 TEMP_201", golden_id="react-nodedupe", initial_world=_world())
    assert len(record["tool_calls"]) == 3
    assert record["react"]["suppressed_repeats"] == 0


@pytest.mark.mock_only
def test_trace_keeps_the_raw_payload_while_the_model_sees_the_summary(tmp_path: Path):
    """Metrics compare `world_diff` / `result_data` verbatim, so compression
    must apply to the conversation only — never to the recorded trace."""
    llm = _RepeatingLLM("create_point", {"tag": "TEMP_201", "type": "analog"})
    agent = _agent(tmp_path, llm, ReActConfig(enabled=True, max_observation_items=1), max_turns=1)
    record = agent.run("新建点位 TEMP_201", golden_id="react-trace", initial_world=_world())
    call = record["tool_calls"][0]
    assert call["world_diff"] is not None
    assert call["result_data"]


@pytest.mark.mock_only
def test_scratchpad_reaches_the_prompt(tmp_path: Path):
    llm = _RepeatingLLM(
        "create_point", {"tag": "TEMP_201", "type": "analog"}, text="思考: 建一个点位"
    )
    agent = _agent(tmp_path, llm, ReActConfig(enabled=True), max_turns=2)
    agent.run("新建点位 TEMP_201", golden_id="react-prompt", initial_world=_world())
    prompt = llm.last_system_prompt
    assert "【ReAct 作业方式】" in prompt
    assert "【ReAct 轨迹】" in prompt
    assert "建一个点位" in prompt


@pytest.mark.mock_only
def test_dedupe_never_overrides_the_state_machine(tmp_path: Path):
    """The cage invariant. A tool blocked by the whitelist is never dispatched,
    so it can never enter the cache and can never be 'replayed' past the cage."""
    cfg = ExperimentConfig(
        name="react_cage",
        architecture=ArchitectureConfig(
            hierarchical_tools=False,
            state_machine=StateMachineConfig(enabled=True, oos_repeat_limit=0),
            react=ReActConfig(enabled=True),
        ),
        model=make_test_model_config(force_mock=True),
    )
    tracer = Tracer(results_root=str(tmp_path), config_name=cfg.name, model_name="react-cage")
    llm = _RepeatingLLM("create_analog_alarm", {"id": "a1", "tag": "TEMP_101", "high_limit": 80.0})
    agent = Agent(
        config=cfg, registry=build_default_registry(), llm=llm, tracer=tracer, max_turns=3
    )
    record = agent.run("加个高温报警", golden_id="react-cage", initial_world=_world())

    blocked = [c for c in record["tool_calls"] if c["error_code"] == "OUT_OF_SCOPE"]
    assert len(blocked) == 3, "blocked calls must keep being blocked, not cached away"
    assert record["react"]["suppressed_repeats"] == 0
    assert record["world_snapshots"]["initial_hash"] == record["world_snapshots"]["final_hash"]


# ============================================================ config wiring
def test_config_I_turns_the_lever_on_and_keeps_F_off():
    react_on = load_config(CONFIGS_DIR / "I_react.yaml").architecture
    react_off = load_config(CONFIGS_DIR / "F_full_four_in_one.yaml").architecture
    assert react_on.react.enabled is True
    assert react_off.react.enabled is False
    # I must be F + ReAct and nothing else, or the A/B is confounded.
    assert (
        react_on.hierarchical_tools,
        react_on.tool_rag.enabled,
        react_on.workflow.enabled,
        react_on.state_machine.enabled,
        react_on.resources_separation,
    ) == (
        react_off.hierarchical_tools,
        react_off.tool_rag.enabled,
        react_off.workflow.enabled,
        react_off.state_machine.enabled,
        react_off.resources_separation,
    )
