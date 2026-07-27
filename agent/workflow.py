"""Workflow Engine — YAML definitions loaded into an executable graph.

A workflow is a *named* multi-step recipe that the orchestrator can drop into
the loop when it detects an applicable user intent. Each step is one of:

* ``llm_step``       — defer to the LLM, with a narrowed ``allowed_tools``
                       whitelist and a target state.
* ``deterministic_step`` — a pure-Python handler (e.g. ``handlers.validate_screen``)
                       run against the world without LLM involvement.
* ``tool_call_step`` — the engine dispatches a fixed tool with pre-bound args,
                       no LLM turn (§4.3.3).
* ``conditional_step`` — branch on a registered predicate over the world.
* ``loop_step``      — bounded repeat of a single body step while a predicate
                       holds (``max_iterations`` guards against runaway).
* ``sub_workflow_step`` — nest another (engine-driven) workflow as one step;
                       its compensations merge onto the parent's Saga stack.

Only ``llm_step`` is ever shown to the model; the rest are resolved by the
engine, which is what makes "the engine owns control flow" (§4.3.1) real rather
than the LLM improvising sequencing. ``depends_on`` is a DAG checked for cycles
at load and enforced on every *linear* forward transition (explicit
conditional/loop jumps are author-directed control flow and are not gated).
Steps may declare a ``compensate`` inverse; on failure the engine unwinds the
completed-step compensations in reverse (§4.3.4 Saga), falling back to a
whole-world checkpoint restore when no per-step compensation is declared.

The engine accomplishes three things at once:

1. **Validate** the YAML against a Pydantic schema at load time so authoring
   errors surface immediately (not at experiment run-time).
2. **Compile** the workflow into a sequence the orchestrator can step through,
   with the per-step allowed-tools whitelist surfacing as a hard filter
   layered on top of the global state-machine whitelist.
3. **Match** an incoming user query against the catalogue of workflows so the
   orchestrator can decide which workflow (if any) to enter.

The matching is intentionally cheap and deterministic — the workflow YAML
carries a list of trigger keywords / regexes; the engine returns the first
workflow whose trigger matches (ties broken by the workflow ``priority`` field,
defaulting to insertion order). Phase 4 can switch to an LLM-based router
without changing this interface.

LangGraph integration is optional: ``compile_to_langgraph`` lifts the workflow
into a real ``StateGraph`` for Phase-4 experiments that want LangGraph's
checkpointing / fan-out. Phase-2 callers can use the in-process executor.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.state_machine import STATES


# ============================================================ YAML schema
class CompensationAction(BaseModel):
    """§4.3.4 Saga — the inverse action for a step, dispatched to undo its
    effect when a later step fails. A single fixed tool call (e.g. the step
    created an alarm → compensate by deleting it)."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(description="Atomic or domain tool that reverses the step")
    arguments: dict[str, Any] = Field(default_factory=dict)


class StepBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    state: str
    description: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    on_failure: str | None = None  # next step id when this step fails
    #: §4.3.4 per-step compensation. When declared, a *successful* run of this
    #: step registers this inverse on the workflow's compensation stack; if the
    #: workflow later fails, the engine unwinds the stack in reverse (true Saga,
    #: as opposed to the coarse whole-world checkpoint restore).
    compensate: CompensationAction | None = None


class LLMStep(StepBase):
    type: Literal["llm_step"] = "llm_step"
    allowed_tools: list[str] = Field(min_length=1)
    must_call_tool: bool = True
    expected_action: str | None = None


class DeterministicStep(StepBase):
    type: Literal["deterministic_step"] = "deterministic_step"
    handler: str = Field(
        description="Dotted-path callable, e.g. 'handlers.validate_screen'"
    )


class ToolCallStep(StepBase):
    """§4.3.3 Tool Call Step — the engine dispatches a *fixed* tool with
    pre-bound arguments, no LLM involvement. Used for steps whose action is
    fully determined by the recipe (e.g. ``deploy_project`` after validation).
    """

    type: Literal["tool_call_step"] = "tool_call_step"
    tool: str = Field(description="Atomic or domain tool name to dispatch")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ConditionalStep(StepBase):
    """§4.3.3 Conditional Step — the engine evaluates a registered predicate
    against the world and branches. Pure control flow: consumes no LLM turn and
    dispatches no tool; it only moves the cursor.
    """

    type: Literal["conditional_step"] = "conditional_step"
    predicate: str = Field(description="Registered predicate name, see register_predicate")
    if_true: str = Field(description="Step id to jump to when the predicate holds")
    if_false: str | None = Field(
        default=None,
        description="Step id when the predicate is false; None falls through to the next step",
    )


class LoopStep(StepBase):
    """§4.3.3 Loop Step — repeat a single body step while a predicate holds,
    bounded by ``max_iterations`` so a mis-authored predicate can never spin
    forever (the §4.6.3 rate-limit / circuit-breaker principle at the graph
    level). The body's completion returns control to this step, which
    re-evaluates the predicate.

    Layout convention: declare ``body`` as the step immediately after the loop.
    While the predicate holds the engine jumps to ``body`` and the body's
    completion returns here; once it fails, control continues at the step
    *after* ``body`` (so the exit path does not re-enter the body).
    """

    type: Literal["loop_step"] = "loop_step"
    predicate: str = Field(description="Loop continues while this predicate holds")
    body: str = Field(description="Step id run once per iteration; returns here after")
    max_iterations: int = Field(default=100, ge=1)


class SubWorkflowStep(StepBase):
    """§4.3.3 Sub-workflow Step — nest another workflow as one step (e.g. a
    reusable "create sub-device" procedure). The named workflow is run to
    completion inline by the engine; its per-step compensations are merged onto
    the parent's Saga stack, so a later parent failure unwinds the child too.

    Constraint: the referenced workflow must be fully engine-driven (no
    ``llm_step``) so it can run without surfacing a turn to the model; a
    sub-workflow that stalls on an ``llm_step`` is treated as a failure.
    """

    type: Literal["sub_workflow_step"] = "sub_workflow_step"
    workflow: str = Field(description="Name of the workflow to run as a sub-step")


# Discriminated on ``type`` so YAML parses unambiguously to the right subclass
# (smart-union guessing gets fragile once several members share field shapes).
Step = Annotated[
    LLMStep
    | DeterministicStep
    | ToolCallStep
    | ConditionalStep
    | LoopStep
    | SubWorkflowStep,
    Field(discriminator="type"),
]

# Step types the engine executes/resolves on its own, without an LLM turn.
_ENGINE_DRIVEN = (
    DeterministicStep,
    ToolCallStep,
    ConditionalStep,
    LoopStep,
    SubWorkflowStep,
)


# Characters that mean a trigger keyword is really a regex pattern. Several
# workflow YAMLs author entries like ``新建.*点位`` directly in the ``keywords``
# list, but the matcher historically did literal substring containment, so
# ``.*`` was searched for verbatim and every such Chinese query silently missed
# its workflow (English ``create point`` still matched). Rather than migrate the
# YAMLs and risk missing one, we detect metacharacters and match those entries
# as regexes; a keyword with no metacharacters still matches by containment,
# which is byte-for-byte the old behaviour.
_REGEX_METACHARS = frozenset(r".*+?[](){}|^$\\")


def _keyword_matches(keyword: str, query_lower: str, query_raw: str) -> bool:
    if any(ch in _REGEX_METACHARS for ch in keyword):
        try:
            return re.search(keyword, query_raw, re.IGNORECASE) is not None
        except re.error:
            # Malformed pattern — fall back to literal containment rather than
            # crash the whole catalogue on one bad YAML entry.
            return keyword.lower() in query_lower
    return keyword.lower() in query_lower


class WorkflowTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list)
    regex: list[str] = Field(default_factory=list)
    require_all_keywords: bool = False


class WorkflowDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = "1.0.0"
    description: str = ""
    priority: int = 100  # lower = preferred when multiple match
    trigger: WorkflowTrigger = Field(default_factory=WorkflowTrigger)
    steps: list[Step] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_steps(self):
        ids = [s.id for s in self.steps]
        id_set = set(ids)
        if len(ids) != len(id_set):
            raise ValueError("duplicate step ids in workflow")

        by_id = {s.id: s for s in self.steps}

        def _ref(step_id: str, field: str, target: str | None) -> None:
            if target is not None and target not in id_set:
                raise ValueError(
                    f"step {step_id!r}.{field}={target!r} is not a known step"
                )

        for s in self.steps:
            if s.state not in STATES:
                raise ValueError(f"step {s.id!r} references unknown state {s.state!r}")
            for dep in s.depends_on:
                _ref(s.id, "depends_on", dep)
            _ref(s.id, "on_failure", s.on_failure)
            if isinstance(s, ConditionalStep):
                _ref(s.id, "if_true", s.if_true)
                _ref(s.id, "if_false", s.if_false)
            elif isinstance(s, LoopStep):
                _ref(s.id, "body", s.body)
                # The single-step-body loop model uses one shared return stack
                # that a plain advance() pops; a control-flow body (another loop
                # or a conditional) would corrupt that stack (its exit does not
                # honour the outer return edge). Reject it at load rather than
                # mis-execute silently — nested/branching loop bodies are not
                # supported by this engine.
                body = by_id.get(s.body)
                if isinstance(body, (LoopStep, ConditionalStep)):
                    raise ValueError(
                        f"loop {s.id!r}.body={s.body!r} must be a plain step, "
                        f"not a {type(body).__name__}"
                    )

        # §4.3 "cycles ... raise immediately": depends_on must be a DAG. Loop
        # back-edges live in LoopStep.body, not depends_on, so intentional loops
        # are unaffected — only a genuine prerequisite cycle is rejected.
        cycle = _find_dependency_cycle(self.steps)
        if cycle is not None:
            raise ValueError(
                "depends_on forms a cycle: " + " -> ".join(cycle)
            )
        return self


# ============================================================ dependency graph
def _find_dependency_cycle(steps: list[Step]) -> list[str] | None:
    """Return a cycle in the ``depends_on`` graph as a node path, or None.

    Edge convention: ``dep -> step`` (a dependency must precede its dependent),
    so a returned path reads in execution order.
    """
    deps: dict[str, list[str]] = {s.id: list(s.depends_on) for s in steps}
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {sid: WHITE for sid in deps}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        colour[node] = GREY
        stack.append(node)
        for pre in deps.get(node, ()):  # pre must run before node
            if colour.get(pre) == GREY:
                # Found a back-edge: extract the cycle from the stack.
                i = stack.index(pre)
                return stack[i:] + [pre]
            if colour.get(pre) == WHITE:
                found = visit(pre)
                if found is not None:
                    return found
        stack.pop()
        colour[node] = BLACK
        return None

    for sid in deps:
        if colour[sid] == WHITE:
            found = visit(sid)
            if found is not None:
                return found
    return None


# ============================================================ loader
def load_workflow(path: str | Path) -> WorkflowDef:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return WorkflowDef.model_validate(raw)


# ============================================================ runtime
class WorkflowError(RuntimeError):
    """Raised when the engine detects an illegal transition at runtime
    (e.g. a step's ``depends_on`` pre-conditions are not met)."""


@dataclass
class WorkflowExecutionState:
    """Per-step execution state recorded in trace as well."""

    workflow_id: str
    current_step_id: str
    completed_steps: list[str] = field(default_factory=list)
    failed_step: str | None = None
    finished: bool = False
    # Loop bookkeeping: iterations taken per LoopStep id, and a stack of loop
    # step ids whose single-step body is mid-flight (so the body's completion
    # returns control to the loop instead of falling through).
    loop_counters: dict[str, int] = field(default_factory=dict)
    loop_return: list[str] = field(default_factory=list)
    # §4.3.4 Saga: (step_id, inverse action) pushed as each compensable step
    # completes successfully; unwound in reverse by the orchestrator on failure.
    compensations: list[tuple[str, CompensationAction]] = field(default_factory=list)

    def advance_to(self, step_id: str) -> None:
        self.current_step_id = step_id


# ---- handlers registry --------------------------------------------------
DeterministicHandler = Callable[[Any, dict[str, Any]], dict[str, Any]]
"""Signature: handler(world, ctx) -> result_dict; raise to fail the step."""

_HANDLERS: dict[str, DeterministicHandler] = {}


def register_handler(name: str, fn: DeterministicHandler) -> None:
    _HANDLERS[name] = fn


def get_handler(name: str) -> DeterministicHandler:
    if name not in _HANDLERS:
        raise KeyError(f"deterministic handler {name!r} is not registered")
    return _HANDLERS[name]


# ---- predicate registry (for conditional / loop steps) ------------------
WorkflowPredicate = Callable[[Any, dict[str, Any]], bool]
"""Signature: predicate(world, ctx) -> bool. Pure read over the world."""

_PREDICATES: dict[str, WorkflowPredicate] = {}


def register_predicate(name: str, fn: WorkflowPredicate) -> None:
    _PREDICATES[name] = fn


def get_predicate(name: str) -> WorkflowPredicate:
    if name not in _PREDICATES:
        raise KeyError(f"workflow predicate {name!r} is not registered")
    return _PREDICATES[name]


# ---- executor -----------------------------------------------------------
class WorkflowEngine:
    """In-process executor used by the orchestrator main loop.

    Control flow (§4.3.1): the engine — not the LLM — owns the cursor. It walks
    the step list in YAML order, but that walk is genuinely non-linear:

    * ``on_failure`` diverts to a recovery step,
    * ``conditional_step`` branches on a predicate (``resolve_conditional``),
    * ``loop_step`` repeats a single-step body while a predicate holds
      (``resolve_loop``), bounded by ``max_iterations``,
    * ``depends_on`` is a DAG asserted at load (cycles raise immediately) and
      enforced on every *linear* forward transition (a step whose prerequisites
      have not completed raises ``WorkflowError`` rather than running out of
      order). Explicit conditional/loop jumps are author-directed and not gated.

    ``deterministic_step`` / ``tool_call_step`` bodies are executed by the
    orchestrator glue (it holds the world, dispatcher and tracer); everything
    else — sequencing, branching, looping, precondition checks — lives here.
    For distributed scheduling / durable checkpointing we'd lift to LangGraph
    (see ``compile_to_langgraph``); the in-process engine covers the demo.
    """

    def __init__(self, wf: WorkflowDef) -> None:
        self.wf = wf
        self._by_id = {s.id: s for s in wf.steps}
        self._order = [s.id for s in wf.steps]

    # ------------------------------------------------------------------ trigger
    def matches(self, query: str) -> bool:
        t = self.wf.trigger
        q_lower = query.lower()
        kw_hits = [_keyword_matches(k, q_lower, query) for k in t.keywords]
        if t.keywords:
            if t.require_all_keywords and not all(kw_hits):
                return False
            if not t.require_all_keywords and not any(kw_hits):
                return False
        for rx in t.regex:
            if re.search(rx, query, re.IGNORECASE):
                return True
        return bool(t.keywords) and (
            all(kw_hits) if t.require_all_keywords else any(kw_hits)
        )

    # ------------------------------------------------------------------ stepping
    def initial_state(self) -> WorkflowExecutionState:
        return WorkflowExecutionState(
            workflow_id=self.wf.name, current_step_id=self._order[0]
        )

    def current_step(self, exec_state: WorkflowExecutionState) -> Step:
        return self._by_id[exec_state.current_step_id]

    def step_order(self) -> list[str]:
        """Step ids in YAML declaration order (used to render progress)."""
        return list(self._order)

    def step_allowed_tools(self, exec_state: WorkflowExecutionState) -> set[str] | None:
        """Per-step Tool whitelist (``None`` unless the step is an ``llm_step``)."""
        step = self.current_step(exec_state)
        if isinstance(step, LLMStep):
            return set(step.allowed_tools)
        return None

    # -- kind predicates used by the orchestrator glue ----------------------
    @staticmethod
    def is_llm_step(step: Step) -> bool:
        return isinstance(step, LLMStep)

    @staticmethod
    def is_engine_driven(step: Step) -> bool:
        """True for steps the engine executes/resolves without an LLM turn."""
        return isinstance(step, _ENGINE_DRIVEN)

    def _advance_after(self, exec_state: WorkflowExecutionState, step_id: str) -> None:
        """Move the cursor to the step following *step_id* in YAML order,
        enforcing that the landing step's ``depends_on`` prerequisites have all
        completed. Finishes the workflow if *step_id* is the last step."""
        idx = self._order.index(step_id)
        if idx + 1 >= len(self._order):
            exec_state.finished = True
            return
        nxt_id = self._order[idx + 1]
        missing = [
            d for d in self._by_id[nxt_id].depends_on
            if d not in exec_state.completed_steps
        ]
        if missing:
            raise WorkflowError(
                f"step {nxt_id!r} depends_on {missing} which have not completed"
            )
        exec_state.advance_to(nxt_id)

    def _advance_linear(self, exec_state: WorkflowExecutionState) -> None:
        """Advance from the *current* step to the next in YAML order."""
        self._advance_after(exec_state, exec_state.current_step_id)

    def advance(self, exec_state: WorkflowExecutionState, *, succeeded: bool) -> None:
        step = self.current_step(exec_state)
        if not succeeded:
            exec_state.failed_step = step.id
            # A failure inside a loop body aborts the loop: drop the pending
            # return edge and iteration counter so the on_failure/terminate path
            # does not later pop a stale return and jump back into the loop.
            if exec_state.loop_return:
                loop_id = exec_state.loop_return.pop()
                exec_state.loop_counters.pop(loop_id, None)
            if step.on_failure and step.on_failure in self._by_id:
                exec_state.advance_to(step.on_failure)
                return
            exec_state.finished = True
            return
        exec_state.completed_steps.append(step.id)
        # §4.3.4 Saga: register this step's inverse so a later failure can undo
        # it (in reverse order). Loop bodies push once per iteration — each
        # iteration's effect gets its own compensation.
        if step.compensate is not None:
            exec_state.compensations.append((step.id, step.compensate))
        # A single-step loop body returns control to its LoopStep, which then
        # re-evaluates the predicate (see resolve_loop).
        if exec_state.loop_return:
            exec_state.advance_to(exec_state.loop_return.pop())
            return
        self._advance_linear(exec_state)

    # -- control-flow resolution (no LLM turn, no tool dispatch) -------------
    def resolve_conditional(
        self, exec_state: WorkflowExecutionState, world: Any, ctx: dict[str, Any]
    ) -> bool:
        """Evaluate a ConditionalStep's predicate and jump. Returns the branch
        taken (True = if_true)."""
        step = self.current_step(exec_state)
        assert isinstance(step, ConditionalStep)
        holds = bool(get_predicate(step.predicate)(world, ctx))
        exec_state.completed_steps.append(step.id)
        target = step.if_true if holds else step.if_false
        if target is None:
            self._advance_linear(exec_state)
        else:
            exec_state.advance_to(target)
        return holds

    def resolve_loop(
        self, exec_state: WorkflowExecutionState, world: Any, ctx: dict[str, Any]
    ) -> bool:
        """Evaluate a LoopStep. If the predicate holds and the iteration budget
        is not exhausted, jump into the body (recording a return edge);
        otherwise fall through. Returns whether another iteration was entered."""
        step = self.current_step(exec_state)
        assert isinstance(step, LoopStep)
        count = exec_state.loop_counters.get(step.id, 0)
        holds = count < step.max_iterations and bool(
            get_predicate(step.predicate)(world, ctx)
        )
        if holds:
            exec_state.loop_counters[step.id] = count + 1
            exec_state.loop_return.append(step.id)
            exec_state.advance_to(step.body)
        else:
            # Loop done: continue *after* the body (by convention the body is the
            # step declared immediately after the loop), not at the loop's own
            # linear successor — that would re-enter the body.
            exec_state.loop_counters.pop(step.id, None)
            exec_state.completed_steps.append(step.id)
            self._advance_after(exec_state, step.body)
        return holds

    def fast_forward_for_atomic(
        self, exec_state: WorkflowExecutionState, atomic_name: str
    ) -> bool:
        """Advance past optional (`must_call_tool: false`) steps until the
        current step's whitelist contains *atomic_name*. Returns True if a
        match was found, False otherwise (in which case the cursor is left
        on the next non-optional step, or finished if none).

        Used by the orchestrator when an LLM emits a tool that the current
        step bans but a downstream step welcomes — skipping optional
        intermediates instead of forcing the LLM to emit a no-op tool.
        """
        while not exec_state.finished:
            step = self.current_step(exec_state)
            if isinstance(step, LLMStep) and atomic_name in step.allowed_tools:
                return True
            # Only fast-forward across LLM-optional steps; deterministic and
            # required steps still gate the workflow.
            if not isinstance(step, LLMStep) or step.must_call_tool:
                return False
            exec_state.completed_steps.append(step.id)
            idx = self._order.index(step.id)
            if idx + 1 >= len(self._order):
                exec_state.finished = True
                return False
            exec_state.advance_to(self._order[idx + 1])
        return False


# ============================================================ catalogue
class WorkflowCatalogue:
    """Holds N WorkflowEngine instances; routes a query to the best match."""

    def __init__(self, engines: Iterable[WorkflowEngine] = ()) -> None:
        self._engines: list[WorkflowEngine] = list(engines)

    def register(self, engine: WorkflowEngine) -> None:
        self._engines.append(engine)

    def all(self) -> list[WorkflowEngine]:
        return list(self._engines)

    def select(self, query: str) -> WorkflowEngine | None:
        """Pick the highest-priority workflow whose trigger matches."""
        hits = [e for e in self._engines if e.matches(query)]
        if not hits:
            return None
        hits.sort(key=lambda e: (e.wf.priority, e.wf.name))
        return hits[0]


def load_catalogue(dir_path: str | Path) -> WorkflowCatalogue:
    dir_path = Path(dir_path)
    if not dir_path.exists():
        return WorkflowCatalogue()
    engines: list[WorkflowEngine] = []
    for yml in sorted(dir_path.glob("*.yaml")):
        wf = load_workflow(yml)
        engines.append(WorkflowEngine(wf))
    return WorkflowCatalogue(engines)


# ============================================================ optional LangGraph compile
def compile_to_langgraph(engine: WorkflowEngine):  # pragma: no cover — optional path
    """Lift the in-process step list into a LangGraph StateGraph.

    Loaded lazily so that environments without ``langgraph`` installed can
    still use the in-process executor.
    """
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class _S(TypedDict, total=False):
        cursor: str
        finished: bool
        ctx: dict[str, Any]

    g: StateGraph = StateGraph(_S)

    def _make(step_id: str):
        def node(state: _S) -> _S:
            return {"cursor": step_id, "ctx": state.get("ctx", {})}

        return node

    order = engine._order
    for sid in order:
        g.add_node(sid, _make(sid))
    g.add_edge(START, order[0])
    for a, b in zip(order, order[1:], strict=False):
        g.add_edge(a, b)
    g.add_edge(order[-1], END)
    return g.compile()


__all__ = [
    "CompensationAction",
    "ConditionalStep",
    "DeterministicHandler",
    "DeterministicStep",
    "LLMStep",
    "LoopStep",
    "SubWorkflowStep",
    "ToolCallStep",
    "WorkflowCatalogue",
    "WorkflowDef",
    "WorkflowEngine",
    "WorkflowError",
    "WorkflowExecutionState",
    "WorkflowPredicate",
    "WorkflowTrigger",
    "compile_to_langgraph",
    "get_handler",
    "get_predicate",
    "load_catalogue",
    "load_workflow",
    "register_handler",
    "register_predicate",
]
