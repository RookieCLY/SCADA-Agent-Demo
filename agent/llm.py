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
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

from agent.config import ArchitectureConfig, ModelConfig


@lru_cache(maxsize=2048)
def _cached_json_schema(model_cls: type) -> dict[str, Any]:
    """``model_cls.model_json_schema()`` is static per class but non-trivial to
    build; the tool schemas are otherwise regenerated for every visible tool on
    every turn. Cache keyed on the (hashable) model class. Callers must treat
    the returned dict as read-only — the two builders below only read it or copy
    sub-sections, never mutate it in place.
    """
    return model_cls.model_json_schema()


# ============================================================ data classes
@dataclass
class LLMToolCall:
    """LLM-emitted tool invocation (provider-agnostic)."""

    name: str
    arguments: dict[str, Any]
    # Provider-assigned tool_call id (OpenAI-compatible `tc.id`). Used to
    # correlate each tool *result* back to the exact call it answers when
    # threading history across turns. None for backends that don't emit ids
    # (e.g. MockLLM), which don't rely on cross-turn result threading.
    call_id: str | None = None


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

    # Mapping: state -> list[(query_pattern, response_factory)].
    # Instantiated per instance in __init__ — a class-level mutable default
    # would accumulate a fresh copy of every scripted rule on each MockLLM()
    # construction (the test suite builds many), growing the shared dict
    # unboundedly.
    SCRIPT: dict[str, list[tuple[re.Pattern[str], Any]]]

    def __init__(self, model_name: str = "mock") -> None:
        self.model_name = model_name
        self.SCRIPT = {}
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
                "已创建高温报警,阈值生效。", next_state=None
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

    def select_workflow(
        self, query: str, options: list[dict[str, str]]
    ) -> str | None:
        """The scripted mock abstains — the orchestrator then falls back to the
        deterministic keyword router, which keeps mock runs reproducible."""
        return None

    def make_plan(self, query: str, tool_list: str, feedback: str | None = None) -> None:
        """The scripted mock cannot plan, so it abstains.

        Plan-and-Execute then falls back to the interleaved loop, which is what
        keeps every existing mock-scripted test deterministic — the mock's canned
        tool sequences are still what runs.
        """
        return None


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


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first top-level JSON object out of a model reply.

    Planners are asked for bare JSON but routinely wrap it in ```json fences or
    a sentence of preamble. Scanning for the outermost balanced ``{...}`` is
    more robust than a strict ``json.loads`` and costs nothing when the model
    complied.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1] if "```" in stripped[3:] else stripped[3:]
        if stripped.startswith("json"):
            stripped = stripped[4:]
    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(stripped[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(stripped[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


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
        self._pending_tool_ids: list[tuple[str, str]] = []
        self._first_call = True
        # Assembled domain-tool schemas are static per (domain, allowed-actions)
        # set but were rebuilt (oneOf branches + doc string) on every turn.
        # Cache them; callers treat the result as read-only (same contract as
        # _cached_json_schema).
        self._domain_schema_cache: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}

    def reset(self) -> None:
        """Discard cross-turn state so the next ``call()`` starts a fresh chat.

        The orchestrator invokes this at the top of every ``agent.run()`` so
        that re-using the same ``Agent`` instance (e.g. iterating a dev set)
        does not bleed messages from a previous query into the next prompt.
        """
        self._messages = []
        self._pending_tool_ids = []
        self._first_call = True

    def select_workflow(
        self, query: str, options: list[dict[str, str]]
    ) -> str | None:
        """§4.3.1 workflow entry decision — a *stateless* one-shot classification
        that deliberately does not touch ``self._messages`` (so it can't pollute
        the main conversation). Returns a workflow name from ``options`` or None.
        """
        if not options:
            return None
        catalogue = "\n".join(
            f"- {o['name']}: {o.get('description', '')}" for o in options
        )
        sys_prompt = (
            "你是工业 SCADA Agent 的工作流路由器。根据用户请求，从下列工作流中选出"
            "最匹配的一个，只输出它的 name(原样);若都不匹配则只输出 NONE。"
            "不要输出任何解释或其它文字。\n可选工作流:\n" + catalogue
        )
        try:
            resp = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": query},
                ],
                temperature=0.0,
                max_tokens=32,
            )
            text = (resp.choices[0].message.content or "").strip()
        except Exception:
            return None
        if not text or "NONE" in text.upper():
            return None
        # The model may wrap the name in punctuation/quotes; match by containment.
        for o in options:
            if o["name"] in text:
                return o["name"]
        return None

    def make_plan(
        self, query: str, tool_list: str, feedback: str | None = None
    ) -> dict[str, Any] | None:
        """One-shot whole-task planning call (Plan-and-Execute).

        Deliberately *stateless* — like ``select_workflow`` it must not touch
        ``self._messages``, both so a replan cannot inherit a half-finished
        function-call exchange and so the planning cost stays a single flat
        prompt rather than the growing conversation the interleaved loop pays.

        Returns the decoded ``{"steps": [...], "refusal": ...}`` object, or
        ``None`` on any transport/parse failure so the caller can fall back.
        """
        from agent.planner import PLANNER_SYSTEM_PROMPT

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT.format(tool_list=tool_list)},
            {"role": "user", "content": query},
        ]
        if feedback:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "上一版计划执行失败,请给出修正后的**剩余**步骤计划(同样的 JSON 格式):\n"
                        + feedback
                    ),
                }
            )
        try:
            resp = self._client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=0.0,
                max_tokens=self.max_tokens,
            )
            text = (resp.choices[0].message.content or "").strip()
        except Exception:
            return None
        payload = _extract_json_object(text)
        if payload is None:
            return None
        usage = getattr(resp, "usage", None)
        payload.setdefault("_usage", {
            "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
        })
        return payload

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
                        "parameters": _cached_json_schema(a.args_model),
                    },
                }
            )
        return out

    def _domain_tool_schemas(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Hierarchical: one tool per domain with a filtered action union.

        Each domain descriptor may carry ``allowed_actions`` from the orchestrator's
        state/workflow whitelist. Only those sub-actions are exposed to the model;
        otherwise a visible domain like ``manage_pages`` would leak every page action
        and let the model choose state-forbidden actions that later become
        ``OUT_OF_SCOPE``.
        """
        out: list[dict[str, Any]] = []
        if self.registry is None:
            return out
        for tool in tools:
            name = tool.get("name")
            if not isinstance(name, str):
                continue
            try:
                d = self.registry.domain(name)
            except KeyError:
                continue
            allowed = [
                action
                for action in tool.get("allowed_actions", [])
                if isinstance(action, str) and action in d.actions
            ]
            if not allowed:
                allowed = list(d.actions.keys())
            cache_key = (name, tuple(allowed))
            cached = self._domain_schema_cache.get(cache_key)
            if cached is not None:
                out.append(cached)
                continue
            actions_doc = "\n".join(
                f"- {action}: {d.actions[action].description}" for action in allowed
            )
            branches: list[dict[str, Any]] = []
            for action_name in allowed:
                atomic = d.actions[action_name]
                sub_schema = _cached_json_schema(atomic.args_model)
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
                # Wrap oneOf inside a proper object schema — some providers
                # (e.g. DeepSeek) reject top-level schemas without "type".
                params: dict[str, Any] = {
                    "type": "object",
                    "oneOf": branches,
                }
            else:
                # Domain with no actions — defensive fallback.
                params = {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [],
                        }
                    },
                    "required": ["action"],
                }
            func_schema = {
                "type": "function",
                "function": {
                    "name": d.name,
                    "description": f"{d.description}\nCurrently allowed actions:\n{actions_doc}",
                    "parameters": params,
                },
            }
            self._domain_schema_cache[cache_key] = func_schema
            out.append(func_schema)
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
            # Two-pass: tool messages first (must immediately follow the
            # assistant's tool_calls for strict providers like DeepSeek),
            # then user messages (e.g. state-transition nudges).
            tool_buf: list[dict[str, Any]] = []
            user_buf: list[dict[str, Any]] = []
            for h in reversed(buf):
                if h.get("role") == "user":
                    user_buf.append(h)
                else:
                    tool_buf.append(h)
            # `buf` walks the whole trailing run of tool/user rows, which
            # accumulates across every consecutive tool-executing turn (the
            # orchestrator only inserts an assistant row on talk-only turns).
            # Correlate each result to its call by the exact tool_call_id, not
            # by tool name: name-matching paired the *oldest* same-named row to
            # the newest pending call, feeding the model stale data and dropping
            # the real result. Exact-id matching also skips rows from earlier
            # turns (their ids are no longer pending — they were injected when
            # they were current).
            pending = list(self._pending_tool_ids)
            for h in tool_buf:
                hid = h.get("tool_call_id")
                tname = h.get("name") or ""
                tid = None
                if hid:
                    for idx, (_name, call_id) in enumerate(pending):
                        if call_id == hid:
                            tid = call_id
                            pending.pop(idx)
                            break
                    # id present but not pending -> already answered earlier.
                    if tid is None:
                        continue
                else:
                    # Legacy rows without an id: fall back to first name match.
                    for idx, (name, call_id) in enumerate(pending):
                        if name == tname:
                            tid = call_id
                            pending.pop(idx)
                            break
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
            for h in user_buf:
                self._messages.append({"role": "user", "content": h.get("content") or ""})
            self._pending_tool_ids.clear()

        names = [t.get("name") for t in (visible_tools or []) if t.get("name")]
        if self.hierarchical:
            tools_schema = self._domain_tool_schemas(visible_tools or [])
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
                tool_calls.append(LLMToolCall(name=fn.name, arguments=args, call_id=tc.id))
                self._pending_tool_ids.append((fn.name, tc.id))
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
    if cfg.provider == "deepseek":
        _load_dotenv_into_environ()
        api_key = _env("DEEPSEEK_API_KEY")
        base_url = _env("DEEPSEEK_API_URL") or "https://api.deepseek.com/v1"
        model_name = cfg.name or "deepseek-chat"
        if not api_key:
            raise RuntimeError(
                "deepseek: missing DEEPSEEK_API_KEY (set it in .env or env)."
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
    if cfg.provider == "openrouter":
        _load_dotenv_into_environ()
        api_key = _env("OPENROUTER_API_KEY")
        base_url = _env("OPENROUTER_API_URL") or "https://openrouter.ai/api/v1"
        model_name = cfg.name or "nvidia/nemotron-3-ultra-550b-a55b:free"
        if not api_key:
            raise RuntimeError(
                "openrouter: missing OPENROUTER_API_KEY (set it in .env or env)."
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
    if cfg.provider == "glm":
        _load_dotenv_into_environ()
        api_key = _env("GLM_API_KEY")
        base_url = _env("GLM_API_URL") or "https://open.bigmodel.cn/api/paas/v4"
        model_name = cfg.name or "glm-4-flash"
        if not api_key:
            raise RuntimeError(
                "glm: missing GLM_API_KEY (set it in .env or env)."
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
    if cfg.provider == "docode":
        _load_dotenv_into_environ()
        api_key = _env("DOCODE_API_KEY")
        base_url = _env("DOCODE_API_URL") or "https://docode.cc/v1"
        model_name = cfg.name or _env("DOCODE_MODEL") or "gpt-5.6-terra"
        if not api_key:
            raise RuntimeError(
                "docode: missing DOCODE_API_KEY (set it in .env or env)."
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
