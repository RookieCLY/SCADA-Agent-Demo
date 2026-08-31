"""Regression tests for cross-turn tool-result threading in OpenAICompatibleLLM.

The adapter threads each turn's tool *results* (carried on the orchestrator's
lightweight ``history``) back into the OpenAI message list so the model can
reason over them. The trailing run of tool rows in ``history`` accumulates
across every consecutive tool-executing turn (an assistant row is only inserted
on talk-only turns), so results must be correlated to their originating call by
the exact ``tool_call_id`` — not by tool name.

The bug this guards against: name-matching paired the *oldest* same-named tool
row to the *newest* pending call, feeding the model stale data from an earlier
turn and silently dropping the real result. It only surfaced on real providers
(MockLLM does not thread history), so the mock suite never caught it.
"""

from __future__ import annotations

import json

from agent.llm import OpenAICompatibleLLM


# --------------------------------------------------------------- fake client
class _FakeFn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str = "{}") -> None:
        self.id = id
        self.function = _FakeFn(name, arguments)


class _FakeMessage:
    def __init__(self, content, tool_calls) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message, finish_reason: str) -> None:
        self.message = message
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, choice) -> None:
        self.choices = [choice]
        self.usage = None
        self.id = "resp-fake"


class _FakeCompletions:
    def __init__(self, scripted: list[_FakeResponse]) -> None:
        self._scripted = scripted
        self.sent: list[dict] = []

    def create(self, **kwargs):
        # Snapshot the messages actually sent this turn (deep-ish copy so later
        # mutation of the live _messages list can't rewrite history under us).
        self.sent.append([dict(m) for m in kwargs["messages"]])
        return self._scripted.pop(0)


class _FakeChat:
    def __init__(self, completions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, chat) -> None:
        self.chat = chat


def _make_llm(scripted: list[_FakeResponse]) -> tuple[OpenAICompatibleLLM, _FakeCompletions]:
    llm = OpenAICompatibleLLM(
        model="fake", api_key="x", base_url="http://localhost", hierarchical=False
    )
    completions = _FakeCompletions(scripted)
    llm._client = _FakeClient(_FakeChat(completions))
    return llm, completions


def _tool_response(call_id: str, name: str) -> _FakeResponse:
    tc = _FakeToolCall(call_id, name)
    return _FakeResponse(_FakeChoice(_FakeMessage(None, [tc]), "tool_calls"))


def _text_response(text: str) -> _FakeResponse:
    return _FakeResponse(_FakeChoice(_FakeMessage(text, None), "stop"))


def _tool_messages_for(messages: list[dict], call_id: str) -> list[dict]:
    return [
        m
        for m in messages
        if m.get("role") == "tool" and m.get("tool_call_id") == call_id
    ]


# --------------------------------------------------------------- the regression
def test_same_named_calls_across_turns_are_not_mispaired():
    """Two ``list_points`` calls on consecutive turns must each receive their
    own result — no stale data, no dropped result."""
    llm, completions = _make_llm(
        [
            _tool_response("call_A", "list_points"),  # turn 1
            _tool_response("call_B", "list_points"),  # turn 2
            _text_response("done"),                    # turn 3 (triggers threading)
        ]
    )

    history: list[dict] = []

    # Turn 1: model asks for list_points; orchestrator dispatches and records.
    r1 = llm.call("sys", "q", [], list(history), "S")
    assert r1.tool_calls[0].call_id == "call_A"
    history.append(
        {"role": "tool", "name": "list_points", "tool_call_id": "call_A",
         "ok": True, "data": {"n": 1}}
    )

    # Turn 2: SAME tool name again → call_B; orchestrator records its result.
    r2 = llm.call("sys", "q", [], list(history), "S")
    assert r2.tool_calls[0].call_id == "call_B"
    history.append(
        {"role": "tool", "name": "list_points", "tool_call_id": "call_B",
         "ok": True, "data": {"n": 2}}
    )

    # Turn 3: threading of the accumulated trailing run happens here.
    llm.call("sys", "q", [], list(history), "S")

    sent = completions.sent[-1]  # messages the adapter assembled for turn 3

    a_msgs = _tool_messages_for(sent, "call_A")
    b_msgs = _tool_messages_for(sent, "call_B")

    # Each call answered exactly once, with its OWN data.
    assert len(a_msgs) == 1, "call_A should be answered exactly once"
    assert len(b_msgs) == 1, "call_B must not be dropped"
    assert json.loads(a_msgs[0]["content"])["data"] == {"n": 1}
    # The core assertion: call_B carries turn-2 data, not stale turn-1 data.
    assert json.loads(b_msgs[0]["content"])["data"] == {"n": 2}


def test_legacy_rows_without_call_id_fall_back_to_name_match():
    """History rows lacking a tool_call_id (older traces) still correlate by
    name so behaviour degrades gracefully rather than dropping the result."""
    llm, completions = _make_llm(
        [
            _tool_response("call_X", "validate_project"),
            _text_response("done"),
        ]
    )

    llm.call("sys", "q", [], [], "S")
    history = [
        {"role": "tool", "name": "validate_project", "ok": True, "data": {"valid": True}}
    ]
    llm.call("sys", "q", [], history, "S")

    sent = completions.sent[-1]
    x_msgs = _tool_messages_for(sent, "call_X")
    assert len(x_msgs) == 1
    assert json.loads(x_msgs[0]["content"])["data"] == {"valid": True}
