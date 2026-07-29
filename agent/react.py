"""ReAct (Reasoning + Acting) turn structure.

The A–F loop in ``agent/orchestrator.py`` is *act-only*: every turn the model
sees the system prompt, the user query, and the raw JSON payload of every tool
it has called so far. Two costs fall out of that shape:

1. **Token cost grows quadratically in the number of tool calls.** Each raw
   payload stays in the conversation and is re-sent on every subsequent turn.
   A single ``list_points`` over a realistic world dominates the prompt for the
   rest of the run.
2. **The model has no record of *why* it did anything.** Its own reasoning is
   discarded between turns, so after a failure it re-derives intent from the
   error alone — which is how the same blocked/failing call gets re-issued.

ReAct fixes both by making the reasoning step a first-class, persisted artifact
and by turning raw results into *observations*:

    Thought  — one line of intent, captured from the model's own text
    Action   — the dispatched (tool, action, args) signature
    Observation — a compressed, hint-annotated result

The scratchpad is bounded (``scratchpad_window``), so it is a fixed-size
summary replacing an unbounded transcript. Three effects the paper's metrics
pick up directly:

* ``input_tokens`` / ``cost_usd`` fall, because payloads are summarised once
  instead of re-sent whole every turn.
* ``step_efficiency`` (= ideal_steps / step_count) rises, because an action
  identical to one already observed as successful is answered from the
  scratchpad instead of being dispatched a second time.
* ``task_success`` / ``schema_violation_rate`` improve, because a failed
  observation carries an error-code-keyed repair hint instead of a bare code.

Nothing here weakens a cage: dedupe happens *after* the state-machine whitelist
and the §4.7 policy check, so a suppressed call is one that would have been
allowed and would have re-done work already done.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "REACT_PROMPT_BLOCK",
    "ReActScratchpad",
    "ReActStep",
    "action_signature",
    "compress_result_data",
    "repair_hint",
]


#: Appended to the system prompt when ``architecture.react.enabled`` is on.
#: Deliberately short — the structure is enforced by the runtime (the scratchpad
#: is built from what actually happened), the prompt only asks for the thought.
REACT_PROMPT_BLOCK = """
【ReAct 作业方式】
每一轮按 思考 → 行动 → 观察 循环推进:
1. 先在纯文本中用一行 "思考: ..." 说明你打算做什么、为什么(不超过一句话)
2. 再发起工具调用(行动);没有可做的行动时只输出思考与结论
3. 系统会把工具结果压缩成"观察"回填到下方轨迹中,不要重复调用已经观察成功的相同调用
4. 观察若带有【修复建议】,请按建议修正参数或改用建议的工具,不要原样重试
"""

#: Error-code → actionable repair instruction. The runtime already knows what
#: went wrong; handing the model the *next move* instead of just the code is
#: what stops the retry-the-same-call loop.
_REPAIR_HINTS: dict[str, str] = {
    "SCHEMA_ERROR": (
        "参数不符合该工具的 JSON Schema。请对照 schema 修正字段类型/必填项后重试同一工具，"
        "不要改用别的工具。"
    ),
    "TYPE_MISMATCH": (
        "点位类型与工具不匹配:模拟量(analog)用 create_analog_alarm/set_threshold，"
        "数字量(digital)用 create_digital_alarm。请先确认点位类型再调用。"
    ),
    "ALREADY_BOUND": (
        "该目标已被绑定/已存在。改用对应的 update_* 工具修改，或跳过这一步继续后续任务。"
    ),
    "ALREADY_EXISTS": (
        "该实体已存在。改用对应的 update_* 工具修改，或直接进入下一步。"
    ),
    "POLICY_DENIED": (
        "运行时安全策略已拒绝该调用,重试不会成功。请用纯文本向用户说明被拒原因并给出合规替代方案。"
    ),
    "BUSINESS_RULE": (
        "违反了业务规则。请阅读错误信息中的具体约束，调整参数或改变执行顺序后再试。"
    ),
    "VALIDATION_FAILED": (
        "校验未通过。请先修复校验报告中列出的问题，再重新执行校验。"
    ),
}

#: Suffix codes that mean "you referenced something that does not exist yet".
_NOT_FOUND_SUFFIX = "_NOT_FOUND"


def repair_hint(error_code: str | None, tool: str, args: dict[str, Any]) -> str | None:
    """Return an actionable next move for *error_code*, or ``None``.

    ``*_NOT_FOUND`` is handled structurally rather than by table lookup: the
    entity kind is recoverable from the code itself, and naming the missing
    entity is what turns a cascade failure into a recoverable one (the metric
    ``cascade_failure_rate`` counts exactly the case where a later call
    references an entity an earlier failed call was supposed to create).
    """
    if not error_code or error_code in {"OK", ""}:
        return None
    if error_code.endswith(_NOT_FOUND_SUFFIX):
        kind = error_code[: -len(_NOT_FOUND_SUFFIX)].lower()
        ref = _first_identifier(args)
        target = f" {ref!r}" if ref else ""
        return (
            f"引用的 {kind}{target} 在当前世界中不存在。先用 list_* 工具(或 read_resource)确认真实"
            f"标识，或先创建它，再重试本步;不要凭猜测重复调用。"
        )
    return _REPAIR_HINTS.get(error_code)


def _first_identifier(args: dict[str, Any]) -> str | None:
    """Best-effort pick of the identifier field a failed call referenced."""
    for key in ("tag", "id", "page_id", "widget_id", "point_tag", "script_id", "device_id"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def action_signature(atomic: str, args: dict[str, Any]) -> str:
    """Canonical, order-insensitive signature of a tool invocation.

    Two calls with the same signature do the same thing, so the second one is
    redundant. Keyed on the **atomic** tool name, not the LLM-facing one, so a
    flat ``create_point`` and a hierarchical ``manage_points(action=create_point)``
    with the same arguments produce the same signature — otherwise the dedupe
    rule would silently do nothing in exactly one of the two tool views, and the
    hierarchical-vs-flat comparison the paper runs would be confounded.
    ``action`` is dropped from the argument body because it is the discriminator,
    already carried by *atomic*.
    """
    payload = {k: v for k, v in sorted(args.items()) if k != "action"}
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"{atomic}({body})"


def compress_result_data(
    data: Any,
    *,
    max_items: int = 5,
    max_chars: int = 320,
) -> Any:
    """Shrink a tool payload to something worth re-sending every turn.

    Lists are truncated to *max_items* with an explicit ``…(+N)`` marker so the
    model knows the result was larger rather than silently believing it saw
    everything — a silent truncation would trade tokens for wrong answers.
    Long strings are clipped to *max_chars*.
    """
    if isinstance(data, dict):
        return {
            key: compress_result_data(value, max_items=max_items, max_chars=max_chars)
            for key, value in data.items()
        }
    if isinstance(data, list):
        head = [
            compress_result_data(item, max_items=max_items, max_chars=max_chars)
            for item in data[:max_items]
        ]
        if len(data) > max_items:
            head.append(f"…(+{len(data) - max_items} more)")
        return head
    if isinstance(data, str) and len(data) > max_chars:
        return data[:max_chars] + "…"
    return data


def _one_line(text: str | None, limit: int = 140) -> str:
    """Collapse a model utterance to a single, bounded line."""
    if not text:
        return ""
    flat = " ".join(text.split())
    # The model is asked for "思考: ..."; keep only that clause when present so
    # the scratchpad holds intent rather than the whole chatty reply.
    for marker in ("思考:", "思考：", "Thought:", "thought:"):
        if marker in flat:
            flat = flat.split(marker, 1)[1].strip()
            break
    # `next_state:` directives are control flow, not reasoning.
    flat = flat.split("next_state:")[0].strip()
    return flat[:limit] + ("…" if len(flat) > limit else "")


@dataclass
class ReActStep:
    """One Thought → Action → Observation triple."""

    turn: int
    thought: str = ""
    tool: str = ""
    action: str | None = None
    signature: str = ""
    ok: bool | None = None
    error_code: str | None = None
    observation: str = ""
    #: World epoch at the moment the action was observed. A cached observation
    #: is only reusable while the world has not moved on.
    epoch: int = 0


@dataclass
class ReActScratchpad:
    """Bounded Thought/Action/Observation memory for a single ``Agent.run``.

    Owns the two things the plain loop throws away — the model's stated intent
    and a compact form of each result — and the one thing it never had: an
    index of what has already been done successfully.
    """

    window: int = 6
    max_observation_chars: int = 320
    max_observation_items: int = 5
    repair_hints: bool = True

    steps: list[ReActStep] = field(default_factory=list)
    #: Bumped whenever a dispatched call actually changed the world. Cached
    #: observations from before the bump are stale and must not be reused.
    epoch: int = 0
    #: signature → index into ``steps`` for successfully completed actions.
    _completed: dict[str, int] = field(default_factory=dict)
    #: Count of dispatches avoided by the dedupe rule (reported in the trace).
    suppressed_repeats: int = 0

    # ------------------------------------------------------------------ thought
    def record_thought(self, turn: int, text: str | None, reasoning: str | None = None) -> None:
        """Capture the model's stated intent for *turn*.

        Prefers the visible text (which the prompt asks to start with ``思考:``)
        and falls back to a thinking-mode provider's ``reasoning_content``.
        """
        thought = _one_line(text) or _one_line(reasoning)
        if not thought:
            return
        # A turn may dispatch several tools; the thought belongs to the turn, so
        # attach it to the pending step if one exists, else open a thought-only
        # step that the next action will fill in.
        if self.steps and self.steps[-1].turn == turn and not self.steps[-1].tool:
            self.steps[-1].thought = thought
            return
        self.steps.append(ReActStep(turn=turn, thought=thought, epoch=self.epoch))

    def _pending_thought(self, turn: int) -> str:
        for step in reversed(self.steps):
            if step.thought and (step.turn == turn or not step.tool):
                return step.thought
        return ""

    # ------------------------------------------------------------------ dedupe
    def cached(self, signature: str) -> ReActStep | None:
        """A prior successful step with the same signature, if still valid.

        "Still valid" means no successful world mutation has happened since it
        was observed. Without the epoch guard this would wrongly suppress e.g. a
        re-read after a write, or a re-create after a delete.
        """
        index = self._completed.get(signature)
        if index is None:
            return None
        step = self.steps[index]
        if step.epoch != self.epoch:
            return None
        return step

    def note_suppressed(self) -> None:
        self.suppressed_repeats += 1

    # ------------------------------------------------------------------ observe
    def observe(
        self,
        *,
        turn: int,
        tool: str,
        action: str | None,
        atomic: str,
        args: dict[str, Any],
        ok: bool,
        error_code: str | None,
        error_msg: str | None,
        data: Any,
        world_changed: bool,
    ) -> tuple[ReActStep, Any, str | None]:
        """Record an executed action and return ``(step, compact_data, message)``.

        ``compact_data`` and ``message`` are what the caller should thread back
        to the model in place of the raw payload and the bare error string.
        """
        signature = action_signature(atomic, args)
        compact = compress_result_data(
            data,
            max_items=self.max_observation_items,
            max_chars=self.max_observation_chars,
        )
        hint = repair_hint(error_code, tool, args) if (self.repair_hints and not ok) else None
        message = error_msg or None
        if hint:
            message = f"{message} 【修复建议】{hint}" if message else f"【修复建议】{hint}"

        if world_changed:
            # Everything cached before this point describes a world that no
            # longer exists.
            self.epoch += 1

        step = ReActStep(
            turn=turn,
            thought=self._pending_thought(turn),
            tool=tool,
            action=action,
            signature=signature,
            ok=ok,
            error_code=error_code,
            observation=self._render_observation(ok, error_code, compact, hint),
            epoch=self.epoch,
        )
        # Reuse the thought-only placeholder opened by record_thought.
        if self.steps and self.steps[-1].turn == turn and not self.steps[-1].tool:
            self.steps[-1] = step
        else:
            self.steps.append(step)
        if ok:
            self._completed[signature] = len(self.steps) - 1
        return step, compact, message

    def _render_observation(
        self,
        ok: bool,
        error_code: str | None,
        compact: Any,
        hint: str | None,
    ) -> str:
        if ok:
            body = json.dumps(compact, ensure_ascii=False, default=str) if compact else "{}"
            if len(body) > self.max_observation_chars:
                body = body[: self.max_observation_chars] + "…"
            return f"OK {body}"
        text = f"FAILED[{error_code or 'UNKNOWN'}]"
        if hint:
            text = f"{text} → {hint}"
        if len(text) > self.max_observation_chars:
            text = text[: self.max_observation_chars] + "…"
        return text

    # ------------------------------------------------------------------ render
    def render(self) -> str:
        """The prompt block: the last ``window`` triples, oldest first."""
        recent = [s for s in self.steps if s.tool or s.thought][-self.window :]
        if not recent:
            return ""
        lines: list[str] = []
        for i, step in enumerate(recent, start=1):
            lines.append(f"{i}. 思考: {step.thought or '(未记录)'}")
            if step.tool:
                target = f"{step.tool}.{step.action}" if step.action else step.tool
                lines.append(f"   行动: {target}")
                lines.append(f"   观察: {step.observation}")
        dropped = len([s for s in self.steps if s.tool or s.thought]) - len(recent)
        header = "\n【ReAct 轨迹】"
        if dropped > 0:
            header += f"(仅显示最近 {len(recent)} 步，更早的 {dropped} 步已省略)"
        return f"{header}\n" + "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ trace
    def summary(self) -> dict[str, Any]:
        """Per-run ReAct stats, written into the trace for offline analysis."""
        acted = [s for s in self.steps if s.tool]
        return {
            "enabled": True,
            "steps": len(acted),
            "thoughts_recorded": sum(1 for s in self.steps if s.thought),
            "suppressed_repeats": self.suppressed_repeats,
            "failed_steps": sum(1 for s in acted if s.ok is False),
            "hints_emitted": sum(1 for s in acted if "修复建议" in s.observation),
            "window": self.window,
        }
