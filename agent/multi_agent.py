"""Multi-Agent (多智能体协作) structure — Supervisor / Specialists / Critic.

The A–H runtime is a **single** agent holding one conversation. Every turn it
carries the whole task, the whole transcript, and a tool surface ranked across
every domain at once. Three costs follow, all of them measured by the paper's
metrics and none addressed by the four architecture levers:

1. **One growing context for the whole task.** Turn N re-reads the results of
   turns 1..N-1 even when they concern a different domain entirely, so
   ``input_tokens`` grows with the task, not with the sub-task being worked on.
2. **Distractors in the tool surface.** Tool RAG hands the model top-k
   candidates spanning several domains; the ones from the *other* domains are
   pure noise for the decision at hand, and they are what
   ``tool_selection_f1`` and ``out_of_scope_tool_rate`` pay for.
3. **Nothing carries entity identity across sub-tasks.** The alarm step has to
   re-derive the tag the point step invented, which is exactly the situation
   ``cascade_failure_rate`` counts.

This module decomposes the run into cooperating roles:

    Supervisor   routes the query to an ordered set of Specialists, one per
                 functional state, using the *existing* Tool-RAG ranking — no
                 extra LLM call, so routing is deterministic and reproducible
    Specialist   a bounded sub-agent pinned to one state. It sees only that
                 state's tools and its own short conversation, not the whole
                 run's transcript
    Blackboard   the shared memory between Specialists: entity IDs actually
                 created, handed forward so a later Specialist references real
                 identifiers instead of guessing them
    Critic       a deterministic post-check. A Specialist whose sub-task
                 produced no world change is re-dispatched once with that fact
                 stated, rather than the whole run being declared finished

Context isolation is the point, and it is what makes the token argument work: N
specialists with short private conversations cost less than one conversation
that is the concatenation of all of them.

No cage is weakened. A Specialist is *narrower* than the single agent — it can
only reach its own state's whitelist — and every dispatch still passes the
state-machine check and the §4.7 runtime policy. The Supervisor may only enter
states reachable by **legal** FSM transitions.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from agent.state_machine import STATES

__all__ = [
    "SPECIALIST_PROMPT_BLOCK",
    "Blackboard",
    "Subtask",
    "critic_feedback",
    "route_subtasks",
    "route_subtasks_from_plan",
    "state_route",
]


#: Injected into the Specialist's system prompt. It tells the sub-agent that it
#: owns one slice of the task — without this a Specialist tries to finish the
#: *whole* query with a single domain's tools and burns its turn budget on
#: out-of-scope calls.
SPECIALIST_PROMPT_BLOCK = """
【你的角色】你是多智能体协作中的**{role}**专家，只负责整体需求中属于你的那一部分。
1. 只使用上方列出的工具完成你负责的部分;其它部分由别的专家负责，不要越权尝试
2. 你负责的部分完成后，直接用纯文本说明做了什么并结束本轮，不要切换阶段
3. 若整体需求中没有属于你的部分，直接用纯文本说明"无需处理"并结束
{blackboard}"""


# ============================================================ state routing
def state_route(src: str, dst: str) -> list[str] | None:
    """Shortest sequence of **legal** transitions from *src* to *dst*.

    Returns the hops after *src*, or ``None`` when the FSM has no legal path.
    The Supervisor routes with this rather than ``StateMachine.force_to``: it
    schedules Specialists, it does not get to step over the cage. A Specialist
    whose state is unreachable is skipped, not forced.
    """
    if src == dst:
        return []
    if src not in STATES or dst not in STATES:
        return None
    seen = {src}
    queue: deque[tuple[str, list[str]]] = deque([(src, [])])
    while queue:
        node, path = queue.popleft()
        for nxt in sorted(STATES[node].next_states):
            if nxt in seen:
                continue
            new_path = [*path, nxt]
            if nxt == dst:
                return new_path
            seen.add(nxt)
            queue.append((nxt, new_path))
    return None


# ============================================================ supervisor
@dataclass
class Subtask:
    """One Specialist assignment: a functional state and the tools it owns."""

    state: str
    atomics: list[str]
    score: float = 0.0
    #: Plan-guided mode: the compiled plan steps this Specialist should carry
    #: out, rendered as call signatures. An escalated crew must not re-derive
    #: from the raw query the work the planner already decided — that re-search
    #: is where the crew's extra turns and off-target ``list_*`` calls went.
    worklist: list[str] = field(default_factory=list)
    #: Set by the Critic when the Specialist finished without changing anything.
    unsatisfied: bool = False
    #: Populated during execution, for the trace.
    turns_used: int = 0
    successful_calls: int = 0


def route_subtasks(
    ranked_atomics: list[str],
    *,
    max_specialists: int = 3,
    tools_per_specialist: int = 8,
    exclude_states: frozenset[str] = frozenset({"ANALYZE_INTENT", "ASK_USER", "DONE"}),
) -> list[Subtask]:
    """Group a Tool-RAG ranking into per-state Specialist assignments.

    Routing reuses the ranking the single agent would have received anyway, so
    it costs **no extra LLM call** — which matters, because a decomposition call
    would eat the token saving the structure exists to produce. It is also
    deterministic, so a multi-agent run stays as reproducible as a single-agent
    one.

    Rank order is preserved: a state's score is the best rank any of its tools
    achieved, so the domain the query is most about is worked first. States that
    hold no tools (``ASK_USER``) or only read tools for intent analysis
    (``ANALYZE_INTENT``) are not Specialists — assigning one would spend a whole
    sub-agent on a state that cannot change anything.
    """
    by_state: dict[str, list[str]] = {}
    best_rank: dict[str, int] = {}
    for rank, atomic in enumerate(ranked_atomics):
        for state in sorted(STATES):
            if state in exclude_states or atomic not in STATES[state].allowed_tools:
                continue
            bucket = by_state.setdefault(state, [])
            if len(bucket) < tools_per_specialist:
                bucket.append(atomic)
            best_rank.setdefault(state, rank)

    ordered = sorted(by_state, key=lambda s: (best_rank[s], s))
    return [
        Subtask(state=state, atomics=by_state[state], score=1.0 / (1 + best_rank[state]))
        for state in ordered[:max_specialists]
    ]


def route_subtasks_from_plan(
    plan_steps: list[Any],
    *,
    tools_per_specialist: int = 8,  # noqa: ARG001 — see docstring
) -> list[Subtask]:
    """Plan-guided Supervisor routing: one Specialist per plan state, in plan
    order, each carrying its slice of the compiled plan as an explicit worklist.

    Used when the crew is the *escalation* tier behind Plan-and-Execute. The
    compiled plan already names the states (``PlanStep.state``), the tools, and
    the intended arguments — throwing that away and re-routing from the RAG
    ranking made the escalated crew re-search from the raw query, which is
    exactly the turn/token bill and ``list_*`` recall dilution the trial
    measured. Steps whose arguments failed to compile still appear on the
    worklist (via ``plan_steps`` entries), so the Specialist retries them with
    feedback rather than silently skipping them.

    Deliberately *not* capped by ``max_specialists`` or by
    ``tools_per_specialist``: the plan's ``max_steps`` already bounds the work,
    and truncating either dimension would silently amputate the task the same
    way the compile-drop shortening did. Capping the tools in particular would
    be actively self-defeating — the worklist would still instruct the
    Specialist to make a call its own assignment no longer permits, and the
    assignment check would reject it as ``OUT_OF_SCOPE``. A plan-guided
    assignment therefore carries exactly the tools its own steps name.
    (``tools_per_specialist`` is kept in the signature for symmetry with
    :func:`route_subtasks` and is accepted-but-unused.)
    """
    by_state: dict[str, Subtask] = {}
    order: list[str] = []
    for step in plan_steps:
        state = getattr(step, "state", None)
        tool = getattr(step, "tool", None)
        if not state or not tool:
            continue
        if state not in by_state:
            by_state[state] = Subtask(state=state, atomics=[], score=1.0)
            order.append(state)
        subtask = by_state[state]
        if tool not in subtask.atomics:
            subtask.atomics.append(tool)
        args = getattr(step, "arguments", {}) or {}
        rendered = ", ".join(f"{k}={v!r}" for k, v in args.items() if k != "action")
        subtask.worklist.append(f"{tool}({rendered})")
    return [by_state[s] for s in order]


# ============================================================ blackboard
@dataclass
class Blackboard:
    """Shared memory between Specialists.

    Holds the entity IDs that were *actually* created or modified, read out of
    each tool's ``world_diff`` rather than from what the model said it did. A
    later Specialist is handed those identifiers, so it references what exists
    instead of re-deriving a tag from the query — the cascade failure the
    single-agent run can only discover by triggering it.
    """

    entities: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    max_entities: int = 20

    def record_diff(self, world_diff: dict[str, Any] | None) -> None:
        if not world_diff:
            return
        for path in (world_diff.get("added_or_modified") or {}):
            # Diff paths are ``collection.id[.field]``; the entity is the first
            # two segments, and recording the whole field path would flood the
            # board with one line per attribute. Widgets are the exception:
            # they nest under pages (``pages.p1.widgets.w1.field``), and
            # truncating them to ``pages.p1`` hid every widget ID from the
            # binding Specialist — which is precisely the identity the
            # point-binding step needs (the golden-019/025 failure chain).
            # Same 4-segment split the metrics comparator uses.
            parts = str(path).split(".")
            if len(parts) >= 4 and parts[0] == "pages" and parts[2] == "widgets":
                entity = ".".join(parts[:4])
            elif len(parts) >= 2:
                entity = ".".join(parts[:2])
            else:
                entity = str(path)
            if entity not in self.entities:
                self.entities.append(entity)

    def note(self, text: str) -> None:
        if text and text not in self.notes:
            self.notes.append(text)

    def render(self) -> str:
        if not self.entities and not self.notes:
            return ""
        lines = ["\n【协作黑板(其它专家已完成的配置,请直接引用这些真实标识)】"]
        if self.entities:
            shown = self.entities[: self.max_entities]
            lines.append("已存在实体: " + ", ".join(shown))
            if len(self.entities) > len(shown):
                lines.append(f"(另有 {len(self.entities) - len(shown)} 个未列出)")
        lines.extend(f"备注: {n}" for n in self.notes)
        return "\n".join(lines) + "\n"

    def summary(self) -> dict[str, Any]:
        return {"entities": list(self.entities), "notes": list(self.notes)}


# ============================================================ critic
def critic_feedback(subtask: Subtask) -> str:
    """The Critic's message for a Specialist that changed nothing.

    Deterministic on purpose: asking a model whether the work was done would
    cost a call *and* be exactly as unreliable as the Specialist that just
    failed. "Did the world change?" is checkable, and it is the same question
    ``final_state_match`` asks.
    """
    return (
        f"【协作校验】你负责的 {subtask.state} 部分尚未产生任何实际配置变更。"
        "如果整体需求确实需要这部分,请立刻使用上方工具完成它;"
        "如果确实无需处理,请用纯文本明确说明原因。"
    )


def unsatisfied_subtasks(subtasks: list[Subtask]) -> list[Subtask]:
    """Specialists that ran but produced no successful tool call."""
    return [s for s in subtasks if s.turns_used > 0 and s.successful_calls == 0]
