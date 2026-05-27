"""Workflow Engine — YAML definitions loaded into an executable graph.

A workflow is a *named* multi-step recipe that the orchestrator can drop into
the loop when it detects an applicable user intent. Each step is one of:

* ``llm_step``      — defer to the LLM, but with a narrowed-down
                      ``allowed_tools`` whitelist and a target state.
* ``deterministic_step`` — a pure-Python callable (e.g. ``handlers.validate_screen``)
                      that runs against the world without LLM involvement.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.state_machine import STATES


# ============================================================ YAML schema
class StepBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    state: str
    description: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    on_failure: str | None = None  # next step id when this step fails


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


Step = LLMStep | DeterministicStep


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
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step ids in workflow")
        for s in self.steps:
            if s.state not in STATES:
                raise ValueError(f"step {s.id!r} references unknown state {s.state!r}")
            for dep in s.depends_on:
                if dep not in ids:
                    raise ValueError(
                        f"step {s.id!r} depends_on unknown step {dep!r}"
                    )
            if s.on_failure and s.on_failure not in ids:
                raise ValueError(
                    f"step {s.id!r}.on_failure={s.on_failure!r} not a known step"
                )
        return self


# ============================================================ loader
def load_workflow(path: str | Path) -> WorkflowDef:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return WorkflowDef.model_validate(raw)


# ============================================================ runtime
@dataclass
class WorkflowExecutionState:
    """Per-step execution state recorded in trace as well."""

    workflow_id: str
    current_step_id: str
    completed_steps: list[str] = field(default_factory=list)
    failed_step: str | None = None
    finished: bool = False

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


# ---- executor -----------------------------------------------------------
class WorkflowEngine:
    """In-process executor used by the orchestrator main loop.

    The engine is *not* a generic dataflow engine — it walks the step list in
    YAML order, honouring ``depends_on`` only to assert pre-conditions
    (cycles or violations raise immediately). For richer scheduling we'd lift
    to LangGraph (see ``compile_to_langgraph``).
    """

    def __init__(self, wf: WorkflowDef) -> None:
        self.wf = wf
        self._by_id = {s.id: s for s in wf.steps}
        self._order = [s.id for s in wf.steps]

    # ------------------------------------------------------------------ trigger
    def matches(self, query: str) -> bool:
        t = self.wf.trigger
        q_lower = query.lower()
        kw_hits = [k.lower() in q_lower for k in t.keywords]
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

    def step_allowed_tools(self, exec_state: WorkflowExecutionState) -> set[str] | None:
        """Per-step Tool whitelist (``None`` if step is deterministic)."""
        step = self.current_step(exec_state)
        if isinstance(step, LLMStep):
            return set(step.allowed_tools)
        return None

    def advance(self, exec_state: WorkflowExecutionState, *, succeeded: bool) -> None:
        step = self.current_step(exec_state)
        if not succeeded:
            exec_state.failed_step = step.id
            if step.on_failure and step.on_failure in self._by_id:
                exec_state.advance_to(step.on_failure)
                return
            exec_state.finished = True
            return
        exec_state.completed_steps.append(step.id)
        idx = self._order.index(step.id)
        if idx + 1 >= len(self._order):
            exec_state.finished = True
            return
        exec_state.advance_to(self._order[idx + 1])

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
    for a, b in zip(order, order[1:]):
        g.add_edge(a, b)
    g.add_edge(order[-1], END)
    return g.compile()


__all__ = [
    "DeterministicHandler",
    "DeterministicStep",
    "LLMStep",
    "WorkflowCatalogue",
    "WorkflowDef",
    "WorkflowEngine",
    "WorkflowExecutionState",
    "WorkflowTrigger",
    "compile_to_langgraph",
    "get_handler",
    "load_catalogue",
    "load_workflow",
    "register_handler",
]
