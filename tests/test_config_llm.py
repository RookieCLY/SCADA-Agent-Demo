"""Config loader + LLM client (mock)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import load_config
from agent.llm import MockLLM, build_llm


def test_load_d_minimal():
    cfg = load_config(Path("configs/D_minimal.yaml"))
    assert cfg.name == "D_minimal"
    assert cfg.architecture.hierarchical_tools is True
    assert cfg.architecture.state_machine.enabled is True
    assert cfg.model.provider == "mock"


def test_load_a_baseline():
    cfg = load_config(Path("configs/A_flat_baseline.yaml"))
    assert cfg.architecture.hierarchical_tools is False
    assert cfg.architecture.state_machine.enabled is False


@pytest.mark.mock_only
def test_build_llm_mock():
    # mock_only opts out of the auto-upgrade fixture so build_llm() returns
    # the real MockLLM instance instead of being swapped to xiaomi-mimo.
    cfg = load_config(Path("configs/D_minimal.yaml"))
    llm = build_llm(cfg.model)
    assert isinstance(llm, MockLLM)


def test_mock_llm_default_falls_through():
    # Direct MockLLM instantiation — unaffected by the build_llm patch and
    # therefore valid in both mock and real-LLM test modes.
    llm = MockLLM()
    resp = llm.call(
        system_prompt="",
        user_query="random question unrelated to alarms",
        visible_tools=[],
        history=[],
        state="ANALYZE_INTENT",
    )
    assert resp.stop_reason == "end_turn"
    assert not resp.tool_calls


def test_mock_llm_high_temp_alarm_script():
    llm = MockLLM()
    resp = llm.call(
        system_prompt="",
        user_query="给反应釜1加个高温报警,超过80度告警",
        visible_tools=[],
        history=[],
        state="ANALYZE_INTENT",
    )
    assert resp.next_state == "CONFIG_ALARM"

    resp2 = llm.call(
        system_prompt="",
        user_query="给反应釜1加个高温报警,超过80度告警",
        visible_tools=[],
        history=[],
        state="CONFIG_ALARM",
    )
    assert resp2.tool_calls and resp2.tool_calls[0].name == "manage_alarms"
    args = resp2.tool_calls[0].arguments
    assert args["action"] == "create_analog_alarm"
    assert args["high_limit"] == 80


def test_build_llm_openrouter():
    import os
    from unittest.mock import patch

    from agent.config import ModelConfig
    from agent.llm import OpenAICompatibleLLM

    cfg = ModelConfig(
        provider="openrouter",
        name="nvidia/nemotron-3-ultra-550b-a55b:free",
        temperature=0.0,
        max_tokens=4096,
    )
    original_get = os.environ.get
    def mock_get(key, default=None):
        if key == "OPENROUTER_API_KEY":
            return "test-key"
        return original_get(key, default)

    with patch.object(os.environ, "get", side_effect=mock_get):
        llm = build_llm(cfg)
        assert isinstance(llm, OpenAICompatibleLLM)
        assert llm.model_name == "nvidia/nemotron-3-ultra-550b-a55b:free"
        assert llm._client.api_key == "test-key"
        assert "openrouter.ai" in str(llm._client.base_url)



# ============================================================ docode failover
def test_parse_docode_endpoints():
    from agent.llm import _parse_docode_endpoints

    parsed = _parse_docode_endpoints(
        "k1@https://a.example/v1, k2@https://b.example/v1,,malformed"
    )
    assert parsed == [("k1", "https://a.example/v1"), ("k2", "https://b.example/v1")]
    assert _parse_docode_endpoints(None) == []


class _Stub403(Exception):
    status_code = 403


class _Stub524(Exception):
    status_code = 524


class _StubClient:
    """Duck-types .chat.completions.create like the OpenAI client."""

    def __init__(self, fail_with=None, result="ok"):
        self.calls = 0
        self._fail = fail_with
        self._result = result

    @property
    def chat(self):
        outer = self

        class _C:
            class completions:  # noqa: N801 - mirrors the OpenAI surface
                @staticmethod
                def create(**kwargs):
                    outer.calls += 1
                    if outer._fail is not None:
                        raise outer._fail
                    return outer._result

        return _C


def test_failover_rotates_on_billing_error_and_sticks():
    """A dead endpoint (403 billing) rotates to the next; the working index is
    sticky at class level so the next request starts there, not at the corpse.
    The wegoo balance died mid-wave twice; this is what keeps a wave alive."""
    from agent.llm import _FailoverClients

    fo = _FailoverClients.__new__(_FailoverClients)
    dead = _StubClient(fail_with=_Stub403("insufficient balance"))
    alive = _StubClient(result="answer")
    fo.clients = [dead, alive]
    from agent.llm import _FailoverChat

    fo.chat = _FailoverChat(fo)
    _FailoverClients.sticky = 0

    assert fo.chat.completions.create(model="m") == "answer"
    assert dead.calls == 1 and alive.calls == 1
    # sticky: second call goes straight to the live endpoint
    assert fo.chat.completions.create(model="m") == "answer"
    assert dead.calls == 1 and alive.calls == 2
    _FailoverClients.sticky = 0


def test_failover_rotates_on_cloudflare_origin_timeout():
    """A Cloudflare 524 is an upstream availability failure, not a bad request."""
    from agent.llm import _FailoverChat, _FailoverClients

    fo = _FailoverClients.__new__(_FailoverClients)
    timed_out = _StubClient(fail_with=_Stub524("origin timeout"))
    alive = _StubClient(result="answer")
    fo.clients = [timed_out, alive]
    fo.chat = _FailoverChat(fo)
    _FailoverClients.sticky = 0

    assert fo.chat.completions.create(model="m") == "answer"
    assert timed_out.calls == 1 and alive.calls == 1
    _FailoverClients.sticky = 0


def test_failover_propagates_request_errors_without_rotating():
    """A 400 (content filter, schema error) is the request's fault — every
    endpoint would reproduce it, so it must NOT burn the other endpoints."""
    from agent.llm import _FailoverChat, _FailoverClients

    class _Stub400(Exception):
        status_code = 400

    first = _StubClient(fail_with=_Stub400("flagged"))
    second = _StubClient(result="never")
    fo = _FailoverClients.__new__(_FailoverClients)
    fo.clients = [first, second]
    fo.chat = _FailoverChat(fo)
    _FailoverClients.sticky = 0

    import pytest as _pytest

    with _pytest.raises(_Stub400):
        fo.chat.completions.create(model="m")
    assert second.calls == 0
    _FailoverClients.sticky = 0


def test_failover_raises_last_error_when_all_endpoints_dead():
    from agent.llm import _FailoverChat, _FailoverClients

    fo = _FailoverClients.__new__(_FailoverClients)
    fo.clients = [_StubClient(fail_with=_Stub403("a")), _StubClient(fail_with=_Stub403("b"))]
    fo.chat = _FailoverChat(fo)
    _FailoverClients.sticky = 0

    import pytest as _pytest

    with _pytest.raises(_Stub403):
        fo.chat.completions.create(model="m")
    _FailoverClients.sticky = 0
