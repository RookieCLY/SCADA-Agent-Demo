"""Regression: tool calls narrated as text must still be dispatched.

LongCat-2.0 emits tool calls as a proprietary text block instead of populating
the OpenAI ``tool_calls`` field. ``OpenAICompatibleLLM`` read only the structured
field, so the call was recorded as ``end_turn`` with zero tool calls and the
model's work was silently discarded.

Measured on a 5-case LongCat smoke before the fix:

    F_noresources  5/5 turns carried a discarded text tool call   (0 tools run)
    F              5/8 turns                                      (0 tools run)
    J_combined     0/5 turns                                      (5 tools run)

J was unaffected only because its Plan-Execute path parses a JSON payload out of
message *content* and never touches the function-calling field. So on this
provider every function-calling arm scored ~0 while J scored normally — an
A-vs-J comparison would have measured the parser, not the architecture.

The payloads below are copied verbatim from those traces.
"""
from __future__ import annotations

from agent.llm import _longcat_arg_value, _parse_longcat_tool_calls

# Verbatim from results/smoke_F_nores golden-001 turn 1.
REAL_SINGLE = (
    "<longcat_tool_call>manage_pages\n"
    "<longcat_arg_key>action</longcat_arg_key>\n"
    "<longcat_arg_value>list_pages</longcat_arg_value>\n"
    "</longcat_tool_call>\n"
)

# Verbatim from results/smoke_F_nores golden-002 turn 1 (two args).
REAL_TWO_ARGS = (
    "<longcat_tool_call>manage_points\n"
    "<longcat_arg_key>action</longcat_arg_key>\n"
    "<longcat_arg_value>list_points</longcat_arg_value>\n"
    "<longcat_arg_key>type_filter</longcat_arg_key>\n"
    "<longcat_arg_value>analog</longcat_arg_value>\n"
    "</longcat_tool_call>\n"
)


def test_parses_the_real_single_arg_payload():
    calls, cleaned = _parse_longcat_tool_calls(REAL_SINGLE)
    assert calls == [("manage_pages", {"action": "list_pages"})]
    assert cleaned == ""


def test_parses_the_real_two_arg_payload():
    calls, _ = _parse_longcat_tool_calls(REAL_TWO_ARGS)
    assert calls == [
        ("manage_points", {"action": "list_points", "type_filter": "analog"})
    ]


def test_narration_is_kept_and_the_block_stripped():
    """The prose around the call is real assistant output and must survive; the
    block itself must not, or it gets replayed to the model as narration."""
    text = "我来帮您新增点位。\n" + REAL_SINGLE + "\n请稍候。"
    calls, cleaned = _parse_longcat_tool_calls(text)
    assert len(calls) == 1
    assert "longcat_tool_call" not in cleaned
    assert "我来帮您新增点位。" in cleaned
    assert "请稍候。" in cleaned


def test_multiple_calls_in_one_turn():
    calls, cleaned = _parse_longcat_tool_calls(REAL_SINGLE + REAL_TWO_ARGS)
    assert [name for name, _ in calls] == ["manage_pages", "manage_points"]
    assert cleaned == ""


def test_absent_marker_parses_to_nothing():
    calls, cleaned = _parse_longcat_tool_calls("就是一段普通的说明文字。")
    assert calls == []
    assert cleaned == "就是一段普通的说明文字。"


# ------------------------------------------------------- value decoding
def test_containers_are_json_decoded():
    """Pydantic cannot coerce a string into a list, so tuples must be decoded —
    this is the create_page/create_widget `resolution`/`position` case."""
    assert _longcat_arg_value("[1920, 1080]") == [1920, 1080]
    assert _longcat_arg_value('{"color": "#000"}') == {"color": "#000"}


def test_numeric_looking_identifiers_stay_strings():
    """The reason this is not a blanket json.loads: a tag like "101" must not
    become an int and fail its ``str`` field. Pydantic coerces the other way
    (str → int) on its own, so leaving scalars alone is strictly safer."""
    assert _longcat_arg_value("101") == "101"
    assert _longcat_arg_value("PT101") == "PT101"
    assert _longcat_arg_value("#000000") == "#000000"
    assert _longcat_arg_value("analog") == "analog"


def test_malformed_container_falls_back_to_the_raw_string():
    assert _longcat_arg_value("[1920, ") == "[1920,"


def test_values_are_stripped():
    assert _longcat_arg_value("  list_points \n") == "list_points"


# ------------------------------------- FSM transitions wrapped in the tag
# Observed verbatim in results/fixed_F_full_four_in_one golden-019 turns 2/6/8:
# the model wraps a *state transition* in the tool-call tag, with a mismatched
# closing tag. These are not tool calls — the `next_state:` regex owns them.
REAL_MALFORMED_TRANSITION = (
    "现在需要创建泵站画面并添加泵图元，这需要切换到页面管理功能。"
    "<longcat_tool_call>next_state: MANAGE_PAGES</longcat_arg_value>\n"
)


def test_malformed_transition_is_not_a_tool_call():
    calls, cleaned = _parse_longcat_tool_calls(REAL_MALFORMED_TRANSITION)
    assert calls == []
    # Left intact so the `next_state:` regex downstream can still find it.
    assert "next_state: MANAGE_PAGES" in cleaned


def test_well_formed_transition_tag_is_still_not_a_tool_call():
    """The observed payloads have a mismatched closing tag, so they never match
    the block regex. One correct closing tag away, they would — and would
    dispatch as an unknown tool. Guard the name explicitly."""
    text = "<longcat_tool_call>next_state\n<longcat_arg_key>x</longcat_arg_key>\n<longcat_arg_value>1</longcat_arg_value>\n</longcat_tool_call>"
    calls, _ = _parse_longcat_tool_calls(text)
    assert calls == []


def test_a_real_call_alongside_a_transition_still_parses():
    text = REAL_MALFORMED_TRANSITION + REAL_SINGLE
    calls, cleaned = _parse_longcat_tool_calls(text)
    assert calls == [("manage_pages", {"action": "list_pages"})]
    assert "next_state: MANAGE_PAGES" in cleaned
