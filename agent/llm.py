"""LLM client wrapper.

Phase-1 ships only a ``mock`` provider so the orchestrator can be exercised
without API keys. The provider returns a deterministic stream of tool calls
keyed by the query and (optionally) the current state — enough to verify the
end-to-end skeleton (config → state machine → dispatcher → world → trace).

Phase-2 adds an OpenAI-compatible adapter that can drive any chat-completion
endpoint exposing ``/v1/chat/completions`` with function-calling. The
``xiaomi-mimo`` provider in ``ModelConfig`` is wired through this adapter.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from agent.config import ArchitectureConfig, ModelConfig


# ============================================================ data classes
@dataclass
class LLMToolCall:
    """LLM-emitted tool invocation (provider-agnostic)."""

    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[LLMToolCall]
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "stop_sequence"]
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    # If non-None, the agent should attempt a state transition to this label.
    next_state: str | None = None
    # Chain-of-thought / reasoning from thinking-mode providers (e.g. xiaomi-mimo).
    reasoning: str | None = None


class LLMProvider(Protocol):
    def call(
        self,
        system_prompt: str,
        user_query: str,
        visible_tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
        state: str,
    ) -> LLMResponse: ...


# ============================================================ Mock provider
class MockLLM:
    """Deterministic scripted backend keyed by simple regex rules.

    The mock is *not* trying to be smart — it only needs to produce a credible
    sequence of tool calls for a handful of seed queries so that the
    orchestrator, dispatcher, world, and tracer can be tested end-to-end.

    For unrecognised queries it emits a single ``end_turn`` response so the
    main loop terminates cleanly rather than spinning.
    """

    # Mapping: state -> list[(query_pattern, response_factory)]
    SCRIPT: dict[str, list[tuple[re.Pattern[str], Any]]] = {}

    def __init__(self, model_name: str = "mock") -> None:
        self.model_name = model_name
        self._install_default_script()

    # ----------------------------------------- factories return LLMResponse
    @staticmethod
    def _resp_tool(
        name: str,
        arguments: dict[str, Any],
        *,
        next_state: str | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            text=None,
            tool_calls=[LLMToolCall(name=name, arguments=arguments)],
            stop_reason="tool_use",
            input_tokens=120,
            output_tokens=40,
            latency_ms=1.0,
            next_state=next_state,
        )

    @staticmethod
    def _resp_text(text: str, *, next_state: str | None = None) -> LLMResponse:
        return LLMResponse(
            text=text,
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=80,
            output_tokens=20,
            latency_ms=1.0,
            next_state=next_state,
        )

    # ----------------------------------------- the canned scripts ---------
    def _install_default_script(self) -> None:
        # "给反应釜1加个高温报警,超过80度告警" — happy path
        rx_alarm_high = re.compile(r"(高温|over.?temp).*报警|报警.*温度", re.IGNORECASE)

        def alarm_high_intent(_: str) -> LLMResponse:
            return self._resp_text(
                "好的,先切换到报警配置阶段。", next_state="CONFIG_ALARM"
            )

        def alarm_high_action(query: str) -> LLMResponse:
            tag = "TEMP_101"
            m = re.search(r"\b([A-Z_]{3,}_\d+)\b", query)
            if m:
                tag = m.group(1)
            limit = 80.0
            m = re.search(r"(\d+(?:\.\d+)?)\s*度", query)
            if m:
                limit = float(m.group(1))
            return self._resp_tool(
                "manage_alarms",
                {
                    "action": "create_analog_alarm",
                    "id": f"alarm_high_{tag.lower()}",
                    "tag": tag,
                    "high_limit": limit,
                    "deadband": 1.0,
                    "priority": "high",
                },
                next_state="DONE",
            )

        def alarm_done(_: str) -> LLMResponse:
            return self._resp_text(
                f"已创建高温报警,阈值生效。", next_state=None
            )

        self.SCRIPT.setdefault("ANALYZE_INTENT", []).append((rx_alarm_high, alarm_high_intent))
        self.SCRIPT.setdefault("CONFIG_ALARM", []).append((rx_alarm_high, alarm_high_action))
        # after the tool call, we re-enter the state machine; orchestrator advances to DONE.
        self.SCRIPT.setdefault("DONE", []).append((rx_alarm_high, alarm_done))

        # "新建一个温度点位 TEMP_201 量程 0~200" — point creation path
        rx_create_point = re.compile(
            r"(新建|创建|添加|create|add).*(点位|point|tag)", re.IGNORECASE
        )

        def create_point_intent(_: str) -> LLMResponse:
            return self._resp_text(
                "切换到点位配置阶段。", next_state="CONFIG_POINT"
            )

        def create_point_action(query: str) -> LLMResponse:
            tag = "TEMP_201"
            m = re.search(r"\b([A-Z_]{3,}_\d+)\b", query)
            if m:
                tag = m.group(1)
            lo, hi = 0.0, 200.0
            m = re.search(r"(\d+(?:\.\d+)?)\s*[~\-到至]\s*(\d+(?:\.\d+)?)", query)
            if m:
                lo, hi = float(m.group(1)), float(m.group(2))
            return self._resp_tool(
                "manage_points",
                {"action": "create_point", "tag": tag, "type": "analog", "min": lo, "max": hi},
                next_state="DONE",
            )

        def create_point_done(_: str) -> LLMResponse:
            return self._resp_text("点位创建完毕。", next_state=None)

        self.SCRIPT.setdefault("ANALYZE_INTENT", []).append(
            (rx_create_point, create_point_intent)
        )
        self.SCRIPT.setdefault("CONFIG_POINT", []).append(
            (rx_create_point, create_point_action)
        )
        self.SCRIPT.setdefault("DONE", []).append((rx_create_point, create_point_done))

        # "查最近一分钟的温度历史" — history-query path exercising Resources + history domain
        rx_history = re.compile(r"(历史|趋势|history|trend|曲线)", re.IGNORECASE)

        def history_intent(_: str) -> LLMResponse:
            # First peek at resources before deciding what tool to call
            return LLMResponse(
                text=None,
                tool_calls=[
                    LLMToolCall(name="read_resource", arguments={"uri": "scada://points?filter=TEMP"})
                ],
                stop_reason="tool_use",
                input_tokens=110,
                output_tokens=20,
                latency_ms=1.0,
                next_state="CONFIG_HISTORY",
            )

        def history_action(query: str) -> LLMResponse:
            tag = "TEMP_101"
            m = re.search(r"\b([A-Z_]{3,}_\d+)\b", query)
            if m:
                tag = m.group(1)
            return self._resp_tool(
                "manage_history",
                {"action": "query_history", "tag": tag, "window_s": 60.0, "max_samples": 5},
                next_state="DONE",
            )

        def history_done(_: str) -> LLMResponse:
            return self._resp_text("历史曲线已读取。", next_state=None)

        self.SCRIPT.setdefault("ANALYZE_INTENT", []).append((rx_history, history_intent))
        self.SCRIPT.setdefault("CONFIG_HISTORY", []).append((rx_history, history_action))
        self.SCRIPT.setdefault("DONE", []).append((rx_history, history_done))

        # "画一个矩形" — graphics primitive path
        rx_rect = re.compile(r"(画|draw|create).*(矩形|rect|框)", re.IGNORECASE)

        def rect_intent(_: str) -> LLMResponse:
            return self._resp_text("好,进入布局阶段。", next_state="GENERATE_LAYOUT")

        def rect_action(_: str) -> LLMResponse:
            return self._resp_tool(
                "manage_graphics",
                {
                    "action": "create_rect",
                    "page_id": "p1",
                    "widget_id": "r1",
                    "position": [50, 50],
                    "size": [120, 80],
                    "style": {"color": "red"},
                },
                next_state="DONE",
            )

        def rect_done(_: str) -> LLMResponse:
            return self._resp_text("矩形已绘制。", next_state=None)

        self.SCRIPT.setdefault("ANALYZE_INTENT", []).append((rx_rect, rect_intent))
        self.SCRIPT.setdefault("GENERATE_LAYOUT", []).append((rx_rect, rect_action))
        self.SCRIPT.setdefault("DONE", []).append((rx_rect, rect_done))

        # "校验项目能不能下装" — deployment path
        rx_validate = re.compile(r"(校验|检查|validate).*(项目|deploy)", re.IGNORECASE)

        def validate_intent(_: str) -> LLMResponse:
            return self._resp_text("好的,进入验证。", next_state="VALIDATE")

        def validate_action(_: str) -> LLMResponse:
            return self._resp_tool(
                "deployment",
                {"action": "validate_project", "deployment_id": "d1"},
                next_state="DONE",
            )

        def validate_done(_: str) -> LLMResponse:
            return self._resp_text("已完成项目校验。", next_state=None)

        self.SCRIPT.setdefault("ANALYZE_INTENT", []).append((rx_validate, validate_intent))
        self.SCRIPT.setdefault("VALIDATE", []).append((rx_validate, validate_action))
        self.SCRIPT.setdefault("DONE", []).append((rx_validate, validate_done))

    # ----------------------------------------- LLMProvider impl ----------
    def call(
        self,
        system_prompt: str,
        user_query: str,
        visible_tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
        state: str,
    ) -> LLMResponse:
        rules = self.SCRIPT.get(state, [])
        for pat, factory in rules:
            if pat.search(user_query):
                resp = factory(user_query)
                resp.raw = {"matched_state": state, "model": self.model_name}
                return resp
        # default — finish gracefully
        return LLMResponse(
            text=f"[mock LLM] no script matched in state {state!r}; ending turn.",
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=15,
            latency_ms=1.0,
            raw={"matched_state": state, "model": self.model_name},
        )


# ============================================================ OpenAI-compatible provider
def _coerce_nested_json_strings(args: Any) -> Any:
    """Defensively decode string values that look like JSON arrays/objects.

    Some function-calling models (notably mimo-v2.5-pro) sometimes
    double-encode complex arg values — e.g. they emit
    ``"position": "[50, 50]"`` (a JSON string containing JSON) instead of
    ``"position": [50, 50]``. Pydantic strict-mode validation then rejects
    these as ``Input should be a valid tuple``. The cure is to walk the args
    once and, when a string value parses as a JSON array or object, swap in
    the decoded value.

    Heuristic is conservative: only attempts the decode when the string
    starts with ``[`` or ``{`` and ends with the matching bracket.
    """
    if isinstance(args, dict):
        return {k: _coerce_nested_json_strings(v) for k, v in args.items()}
    if isinstance(args, list):
        return [_coerce_nested_json_strings(v) for v in args]
    if isinstance(args, str):
        s = args.strip()
        if (s.startswith("[") and s.endswith("]")) or (
            s.startswith("{") and s.endswith("}")
        ):
            try:
                decoded = json.loads(s)
            except json.JSONDecodeError:
                return args
            return _coerce_nested_json_strings(decoded)
    return args


def _load_dotenv_into_environ(path: str | Path = ".env") -> None:
    """Best-effort .env loader. Silently skips when the file is missing or
    python-dotenv is unavailable.

    Falls back to a tiny hand-rolled parser so the demo runs even if
    ``python-dotenv`` isn't installed.
    """
    p = Path(path)
    if not p.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=str(p), override=False)
        return
    except ImportError:
        pass
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _env(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


class OpenAICompatibleLLM:
    """Adapter for any provider speaking the OpenAI Chat-Completions wire format.

    Used to drive the xiaomi-mimo endpoint. Maintains an internal conversation
    list across turns so that function-call results can be threaded back to the
    model with their original ``tool_call_id``s — the orchestrator's lightweight
    ``history`` parameter (which only carries ``ok`` / ``error_code``) is not
    enough by itself to satisfy the OpenAI message contract.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        registry: Any | None = None,
        hierarchical: bool = False,
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model_name = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.registry = registry
        self.hierarchical = hierarchical
        # cross-turn conversation state
        self._messages: list[dict[str, Any]] = []
        self._pending_tool_ids: dict[str, str] = {}
        self._first_call = True

    def reset(self) -> None:
        """Discard cross-turn state so the next ``call()`` starts a fresh chat.

        The orchestrator invokes this at the top of every ``agent.run()`` so
        that re-using the same ``Agent`` instance (e.g. iterating a dev set)
        does not bleed messages from a previous query into the next prompt.
        """
        self._messages = []
        self._pending_tool_ids = {}
        self._first_call = True

    # ----------------------------------------- tool schema builders
    def _flat_tool_schemas(self, names: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self.registry is None:
            return out
        for n in names:
            try:
                a = self.registry.atomic(n)
            except KeyError:
                continue
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": a.name,
                        "description": a.description,
                        "parameters": a.args_model.model_json_schema(),
                    },
                }
            )
        return out

    def _domain_tool_schemas(self, names: list[str]) -> list[dict[str, Any]]:
        """Hierarchical: one tool per domain with a discriminated union.

        We expose each sub-action as a fully-typed ``oneOf`` branch so the
        model can see *exactly which fields are required for each action*.
        Without this, mimo (and other function-calling models) tend to emit
        only the action discriminator and forget the action-specific args,
        producing endless SCHEMA_ERROR loops in hierarchical mode.
        """
        out: list[dict[str, Any]] = []
        if self.registry is None:
            return out
        for n in names:
            try:
                d = self.registry.domain(n)
            except KeyError:
                continue
            actions_doc = "\n".join(
                f"- {a.action}: {a.description}" for a in d.actions.values()
            )
            branches: list[dict[str, Any]] = []
            for action_name, atomic in d.actions.items():
                sub_schema = atomic.args_model.model_json_schema()
                props = dict(sub_schema.get("properties") or {})
                # Force the discriminator to the literal action name
                props["action"] = {"type": "string", "const": action_name}
                required = list(sub_schema.get("required") or [])
                if "action" not in required:
                    required.append("action")
                branches.append(
                    {
                        "type": "object",
                        "title": action_name,
                        "description": atomic.description,
                        "properties": props,
                        "required": required,
                        "additionalProperties": False,
                    }
                )
            if branches:
                params: dict[str, Any] = {"oneOf": branches}
            else:
                # Domain with no actions — defensive fallback.
                params = {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": list(d.actions.keys()),
                        }
                    },
                    "required": ["action"],
                }
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": d.name,
                        "description": f"{d.description}\nActions:\n{actions_doc}",
                        "parameters": params,
                    },
                }
            )
        return out

    # ----------------------------------------- LLMProvider impl
    def call(
        self,
        system_prompt: str,
        user_query: str,
        visible_tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
        state: str,
    ) -> LLMResponse:
        # First call: seed with system + user. Subsequent calls: append any
        # tool results since the last assistant turn.
        if self._first_call:
            self._messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query},
            ]
            self._first_call = False
        else:
            if self._messages and self._messages[0].get("role") == "system":
                self._messages[0]["content"] = system_prompt
            # walk backward through history collecting consecutive tool and user rows
            buf: list[dict[str, Any]] = []
            i = len(history) - 1
            while i >= 0 and history[i].get("role") in ("tool", "user"):
                buf.append(history[i])
                i -= 1
            for h in reversed(buf):
                if h.get("role") == "user":
                    self._messages.append({"role": "user", "content": h.get("content") or ""})
                    continue
                tname = h.get("name") or ""
                tid = self._pending_tool_ids.get(tname)
                if not tid:
                    continue
                payload: dict[str, Any] = {
                    "ok": h.get("ok"),
                    "error_code": h.get("error_code"),
                }
                # Pass back the tool's actual data so the model can reason on
                # it (e.g. list_points returning the point list, query_history
                # returning samples). Earlier revisions dropped this and the
                # model only saw {ok, error_code}, which silently broke the
                # workflow-driven configs.
                data = h.get("data")
                if data:
                    payload["data"] = data
                err_msg = h.get("error_msg")
                if err_msg:
                    payload["error_msg"] = err_msg
                self._messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tid,
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                )
            self._pending_tool_ids.clear()

        names = [t.get("name") for t in (visible_tools or []) if t.get("name")]
        if self.hierarchical:
            tools_schema = self._domain_tool_schemas(names)
        else:
            tools_schema = self._flat_tool_schemas(names)

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": self._messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools_schema:
            kwargs["tools"] = tools_schema
            kwargs["tool_choice"] = "auto"

        t0 = time.perf_counter()
        resp = self._client.chat.completions.create(**kwargs)
        lat = (time.perf_counter() - t0) * 1000

        choice = resp.choices[0]
        msg = choice.message
        text = msg.content or None
        reasoning = getattr(msg, "reasoning_content", None)

        tool_calls: list[LLMToolCall] = []
        assistant_record: dict[str, Any] = {"role": "assistant", "content": text}
        if reasoning:
            # xiaomi-mimo (and other "thinking-mode" providers) require the
            # reasoning_content from the previous assistant turn to be echoed
            # back in the next request, otherwise the API rejects with 400.
            assistant_record["reasoning_content"] = reasoning
        raw_tcs = getattr(msg, "tool_calls", None) or []
        if raw_tcs:
            assistant_record["tool_calls"] = []
            for tc in raw_tcs:
                fn = tc.function
                try:
                    args = json.loads(fn.arguments) if fn.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                args = _coerce_nested_json_strings(args)
                tool_calls.append(LLMToolCall(name=fn.name, arguments=args))
                self._pending_tool_ids[fn.name] = tc.id
                assistant_record["tool_calls"].append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": fn.name, "arguments": fn.arguments or "{}"},
                    }
                )
        self._messages.append(assistant_record)

        stop_reason: Literal[
            "end_turn", "tool_use", "max_tokens", "stop_sequence"
        ]
        finish = choice.finish_reason or ""
        if tool_calls:
            stop_reason = "tool_use"
        elif finish == "length":
            stop_reason = "max_tokens"
        elif finish == "stop":
            stop_reason = "end_turn"
        else:
            stop_reason = "end_turn"

        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        next_state = None
        if text:
            m = re.search(r"next_state:\s*([A-Z_]+)", text)
            if m:
                next_state = m.group(1)

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=lat,
            raw={"model": self.model_name, "response_id": getattr(resp, "id", "")},
            next_state=next_state,
            reasoning=reasoning,
        )


# ============================================================ factory
def build_llm(
    cfg: ModelConfig,
    *,
    registry: Any | None = None,
    arch: ArchitectureConfig | None = None,
) -> LLMProvider:
    if cfg.provider == "mock":
        return MockLLM(model_name=cfg.name or "mock")
    if cfg.provider == "xiaomi-mimo":
        _load_dotenv_into_environ()
        api_key = _env("XIAOMI-MIMO_API_KEY", "XIAOMI_MIMO_API_KEY")
        base_url = _env("XIAOMI-MIMO_API_URL", "XIAOMI_MIMO_API_URL")
        model_name = (
            _env("XIAOMI-MIMO_MODEL", "XIAOMI_MIMO_MODEL") or cfg.name or "mimo-v2.5-pro"
        )
        if not api_key:
            raise RuntimeError(
                "xiaomi-mimo: missing XIAOMI-MIMO_API_KEY (set it in .env or env)."
            )
        if not base_url:
            raise RuntimeError(
                "xiaomi-mimo: missing XIAOMI-MIMO_API_URL (set it in .env or env)."
            )
        hierarchical = arch.hierarchical_tools if arch is not None else False
        return OpenAICompatibleLLM(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            registry=registry,
            hierarchical=hierarchical,
        )
    raise NotImplementedError(
        f"Provider {cfg.provider!r} is not wired yet."
    )


__all__ = [
    "LLMProvider",
    "LLMResponse",
    "LLMToolCall",
    "MockLLM",
    "OpenAICompatibleLLM",
    "build_llm",
]
