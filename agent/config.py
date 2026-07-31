"""Experiment-configuration models (loaded from configs/*.yaml).

Phase 1 only consumes ``architecture.hierarchical_tools`` and
``architecture.state_machine``; the other flags exist so that Phase 2 modules
(RAG, Workflow, Resources) can be slotted in without rewriting the loader.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class ToolRAGConfig(BaseModel):
    enabled: bool = False
    top_n: int = 30
    top_k: int = 12
    alpha_dense: float = 0.6
    use_reranker: bool = True


class WorkflowConfig(BaseModel):
    enabled: bool = False
    yaml_path: str | None = None
    #: ``filter``  — legacy behaviour: the workflow only intersects the per-step
    #:               ``allowed_tools`` into the visibility pipeline, while the LLM
    #:               still owns turn-to-turn control flow.
    #: ``engine``  — §4.3.1 behaviour: the Workflow Engine owns control flow. The
    #:               LLM is called per step for a *local* decision only; it cannot
    #:               drive state transitions, and the engine advances the cursor.
    mode: Literal["filter", "engine"] = "filter"
    #: Saga compensation (§4.3.4). When a workflow fails, restore the world to
    #: the checkpoint taken when the workflow was entered, so a partial run does
    #: not leave half-built configuration behind (§2.5(5)).
    rollback_on_failure: bool = False
    #: Workflow entry selection (§4.3.1 "LLM 是 Workflow 的入口决策器").
    #: ``keyword`` — deterministic trigger keyword/regex match (default; keeps
    #:               experiments reproducible and cheap).
    #: ``llm``     — ask the model to pick the best-matching workflow (or none)
    #:               from the catalogue, falling back to keyword match if the
    #:               model abstains or names an unknown workflow.
    selection: Literal["keyword", "llm"] = "keyword"


class StateMachineConfig(BaseModel):
    enabled: bool = False
    #: Tell the model *which* state exposes a tool it was blocked from calling,
    #: instead of returning a bare "not in whitelist". Without this the model has
    #: no signal to act on and simply retries the same blocked call.
    oos_guidance: bool = True
    #: Circuit breaker (§4.6.3(6)). After this many identical out-of-scope calls,
    #: stop feeding the error back and divert to ASK_USER. ``0`` disables.
    oos_repeat_limit: int = 3


class PlanExecuteConfig(BaseModel):
    """Plan-and-Execute (规划-执行) — the *agent-loop* lever.

    Orthogonal to the four architecture levers: those all gate the tool
    *surface*, none of them change the fact that the loop is **interleaved**
    (one LLM call per tool call, each re-reading the whole conversation).

    With this on, ``Agent.run`` first asks the model for the **whole** ordered
    tool sequence, compiles it deterministically (drop hallucinated tools,
    validate every argument object against its Pydantic schema, collapse
    duplicates, topologically repair the order from the
    ``intended_entities``/``referenced_entities`` contract), then executes the
    compiled steps with no LLM in the loop — replanning only when a step
    actually fails.

    Off by default so the archived A–F results stay reproducible.
    """

    enabled: bool = False
    #: Replans allowed after a step failure. ``0`` = execute the first plan or
    #: stop; each replan costs one LLM call, which is the whole budget question.
    max_replans: int = 2
    #: Hard ceiling on compiled steps, so a runaway plan cannot outrun the turn
    #: budget the interleaved loop would have had.
    max_steps: int = 12
    #: Apply the dependency-driven topological repair to the proposed order.
    reorder_by_dependency: bool = True
    #: Number of atomics shown to the planner (the planning prompt is the one
    #: place the whole catalogue has to fit).
    planner_tool_budget: int = 60
    #: If the planner abstains or every step is dropped, fall back to the
    #: standard interleaved loop instead of ending the run empty-handed.
    fallback_to_interleaved: bool = True
    #: Include a compact world snapshot (existing points/devices/pages/…) in
    #: the planning prompt. The docode trial showed the planner refusing
    #: legitimate tasks it could not ground ("无法确定 PumpA 点位类型") because it
    #: could not see the world it was planning against.
    include_world_context: bool = True
    #: When the compiler drops proposed steps (unknown tool / schema-invalid /
    #: unreachable), spend one replan telling the planner exactly what was
    #: dropped and why, instead of silently executing the shortened plan. The
    #: trial's golden-013 failed precisely this way: 2 proposed → 1 compiled →
    #: half the task silently missing.
    replan_on_compile_drop: bool = True
    #: Repair recoverable argument-shape defects (double-encoded JSON, explicit
    #: nulls, invented keys) instead of dropping the step. Default on — it is
    #: what moves cases off the expensive escalation path. Switchable because
    #: repair converts "could not compile, so refuse" into "repaired, so
    #: execute", and the frozen no-repair arm scored markedly *safer* on reject
    #: cases (97.0% vs 81.8%); this flag isolates whether repair caused that.
    repair_schema_invalid: bool = True
    #: Render argument *types* in the planning catalogue (W2): arity-aware tuple
    #: hints for required fields, plus shaped optionals. Default on — it cut the
    #: compile-drop rate. Switchable because W6 showed reverting W3 recovered
    #: only 10.6 of the 22.7pp reject-safety loss, so something else in W1/W2
    #: also costs safety; better hints mean fewer accidental refusals, and on
    #: reject cases an accidental refusal scored as correct behaviour.
    typed_tool_hints: bool = True
    #: Per-collection cap on the world snapshot above. This is the *only* view
    #: of the world the planner gets — §4.5 takes the read tools away, and the
    #: plan is fixed before execution starts, so no read during execution can
    #: correct a plan built on a partial snapshot. A silent truncation here
    #: surfaces later as "acted, but final state mismatch". Raised from the
    #: original hard-coded 25; truncation is now counted into the trace.
    world_context_max_items: int = 60
    #: Let the planner answer "I need more information" on a channel of its own
    #: (``clarify``) instead of folding it into the safety ``refusal``. Default
    #: off, in which case the field is *ignored* exactly as an unrecognised key
    #: was before it existed — deliberately not folded into ``refusal``, which
    #: gates ``replan_on_compile_drop`` and both crew escalations.
    #:
    #: NOTE: "levers off ⇒ archived behaviour" holds for this module but **not**
    #: for the tree as a whole. ``PLANNER_SYSTEM_PROMPT`` gained the clarify
    #: instructions, an entity-grounding rule and a destructive-refusal rule, and
    #: those are unconditional — every plan-tier arm re-run on this code plans
    #: against a different prompt than the archived runs did. The W9 ``K0`` arm
    #: measures exactly that difference.
    #:
    #: Measured motivation (A vs shipping J, 106×2, LongCat): the planner
    #: fabricated an identity rather than asking on 4 of the 25 runs A won —
    #: golden-008 planned ``create_page(id="main_page", name="主页面")`` for the
    #: bare "帮忙建个页面", golden-060 invented ``SCRIPT_001`` with an
    #: ``on_event`` trigger for a script whose trigger the user explicitly
    #: deferred. Each mutated the world on a case whose expected diff is empty.
    #: The same prompt slot produced the opposite error on golden-018, a
    #: legitimate request pushed down the safety channel for a missing field.
    #: One channel cannot carry both answers, and they do not even share a
    #: terminal state: a refusal ends on DONE, a clarification on ASK_USER.
    clarify_on_underspecified: bool = False
    #: Forbid a replan from *creating* an entity that the step it is recovering
    #: from merely *referenced*. Default off (archived behaviour).
    #:
    #: This is the cascade the architecture claims to prevent, re-entered
    #: through the recovery path: on golden-054 ``query_history`` correctly
    #: failed ``POINT_NOT_FOUND`` — the error the case expects — and the replan
    #: "fixed" it by calling ``create_point`` to make the query succeed, on a
    #: case that forbids ``create_point`` and expects an untouched world. A
    #: missing referenced entity is a signal to report, not to manufacture.
    replan_may_create_referenced: bool = True
    #: Rounds of post-execution verification. 0 (default) is the archived
    #: open-loop behaviour: the plan is built from a snapshot, executed blind,
    #: and never checked against what it produced.
    #:
    #: This closes the loop that made A win. A acts, reads the result, corrects,
    #: and converges; the plan tier commits once and never looks. The dominant
    #: residual failure is exactly that shape — 11 of the 25 runs A won are
    #: "acted, final state mismatch" with no error anywhere in the trace
    #: (golden-093 archived a point instead of enabling history, golden-013
    #: created one of two requested pages). Each is trivially visible by reading
    #: the world back, and invisible to a plan that never re-reads.
    #:
    #: Deliberately bounded: the cost argument for this whole structure is that
    #: it is O(1) LLM calls, so verification is capped rather than iterated to
    #: convergence.
    verify_rounds: int = 0


class ReActConfig(BaseModel):
    """ReAct (Reasoning + Acting) turn structure — the *agent-loop* lever.

    Persists a bounded Thought → Action → Observation scratchpad rendered into
    the prompt, compresses tool payloads into observations before threading
    them back, annotates failures with error-code-keyed repair hints, and
    answers a repeated identical action from the scratchpad. Dedupe runs
    *after* the state-machine whitelist and the §4.7 policy check.

    In the combined structure this is the loop hygiene applied to **every**
    LLM-interleaved path: the fallback loop and each Specialist's private
    conversation. Off by default so archived results stay reproducible.
    """

    enabled: bool = False
    #: How many past Thought/Action/Observation triples to render into the
    #: prompt. Bounded so the scratchpad cannot itself become the token bill.
    scratchpad_window: int = 6
    #: Per-observation character budget after compression.
    max_observation_chars: int = 320
    #: Max list elements kept per field when compressing a tool payload.
    max_observation_items: int = 5
    #: Answer a repeated identical action from the scratchpad instead of
    #: re-dispatching it (only while no successful world mutation intervened).
    #: Default **off**: measured ``suppressed_repeats: 0`` across three runs and
    #: two models — the models simply do not emit the identical repeated action
    #: this guards against. Kept (not deleted) because that is a statement about
    #: two strong models, not about the mechanism; re-test on a genuinely weak
    #: model before concluding it is useless.
    dedupe_repeat_actions: bool = False
    #: Append an error-code-keyed repair hint to failed observations. Default
    #: **off** for the same reason: ``hints_emitted: 0`` over the same runs.
    #: Observation *compression* — which does measurably work — is unaffected
    #: by either flag and stays on whenever ReAct is enabled.
    repair_hints: bool = False


class MultiAgentConfig(BaseModel):
    """Multi-Agent (多智能体协作) — Supervisor / Specialists / Critic.

    A deterministic Supervisor routes the query to per-state Specialists (from
    the existing Tool-RAG ranking — no extra LLM call), each Specialist runs a
    bounded private conversation over its own state's tools, a Blackboard hands
    the actually-created entity IDs forward, and a deterministic Critic re-runs
    a Specialist once when its slice produced no world change.

    In the combined structure the crew is the **escalation tier**: it takes
    over when the compiled plan spans ``min_domains``+ registry domains, or
    when plan execution fails with its replan budget exhausted. It is the
    highest-accuracy and most expensive path, so it is gated, not default.
    """

    enabled: bool = False
    #: Ceiling on Specialists per run (each is a bounded sub-conversation).
    max_specialists: int = 3
    #: LLM turns each Specialist may use for its sub-task.
    turns_per_specialist: int = 4
    #: Tools handed to one Specialist (its state's whitelist ∩ the ranking).
    tools_per_specialist: int = 8
    #: Give an unproductive Specialist one Critic-prompted retry.
    critic_retry: bool = True
    #: Escalate on plan *shape* — the compiled plan spanning ``min_domains``+
    #: registry domains. Default **off**: it fired 22 of 51 escalations without
    #: any observed problem, purely because a task touched two domains, and the
    #: crew costs ~+31% tokens. The other two triggers (``compile_drop`` and
    #: failure-recovery) key off something that actually went wrong, and they
    #: are kept. Flag rather than deletion so the arm stays measurable.
    domain_gate: bool = False
    #: Domain count at which ``domain_gate`` fires, when enabled.
    #: Single-domain tasks stay on the cheap plan path — the docode trial
    #: showed the crew costing ~13× Plan-Execute tokens, so it must buy
    #: accuracy only where decomposition can actually help.
    min_domains: int = 2
    #: Plan-guided escalations scale the crew's turn budget with the task:
    #: ``max(max_turns, start + ceil(turns_per_step × plan_steps))``. A 12-step
    #: plan getting the same 12-turn budget as a 1-step one is why golden-019
    #: died on ``max_turns exhausted`` mid-crew in the trial.
    turns_per_step: float = 1.5


class SafetyPolicyConfig(BaseModel):
    """The §4.7 functional-safety cage — enforced in the runtime, not the prompt.

    Independent of ``ArchitectureConfig`` on purpose: the paper's argument is
    that this is a *second, redundant* cage that must not be ablatable together
    with the software-engineering one.
    """

    enabled: bool = False
    #: ``design_time``     —组态期: reads plus configuration writes are allowed.
    #: ``operations_time`` — 运行态: reads only; every write is refused at the
    #:                       runtime layer regardless of prompt or user phrasing.
    runtime_mode: Literal["design_time", "operations_time"] = "design_time"
    #: Per-session cap on delete/disable operations; negative disables the cap.
    max_destructive_ops: int = 3
    #: Subset of ``agent.policy.POLICY_RULES`` ids to enable; ``None`` = all.
    rules: list[str] | None = None


class TraceConfig(BaseModel):
    record_llm_io: bool = False


class ArchitectureConfig(BaseModel):
    hierarchical_tools: bool = False
    tool_rag: ToolRAGConfig = Field(default_factory=ToolRAGConfig)
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    state_machine: StateMachineConfig = Field(default_factory=StateMachineConfig)
    resources_separation: bool = False
    #: The three agent-loop levers. Orthogonal to the four surface levers and
    #: to each other; when several are on, ``Agent.run`` arbitrates:
    #: plan (cheap, 1 LLM call) → crew (accurate, gated by ``min_domains`` or
    #: plan failure) → ReAct interleaved loop (fallback hygiene).
    plan_execute: PlanExecuteConfig = Field(default_factory=PlanExecuteConfig)
    react: ReActConfig = Field(default_factory=ReActConfig)
    multi_agent: MultiAgentConfig = Field(default_factory=MultiAgentConfig)


class ModelConfig(BaseModel):
    provider: Literal[
        "mock", "anthropic", "openai", "deepseek", "xiaomi-mimo", "openrouter",
        "glm", "docode", "nvidia", "longcat",
    ] = "mock"
    name: str = "mock"
    temperature: float = 0.0
    max_tokens: int = 4096


class DatasetConfig(BaseModel):
    path: str | None = None
    sample_size: int | None = None


class WorldConfig(BaseModel):
    backend: Literal["memory", "sqlite", "redis"] = "memory"


class ExperimentConfig(BaseModel):
    name: str
    description: str = ""
    architecture: ArchitectureConfig = Field(default_factory=ArchitectureConfig)
    #: The functional-safety cage (§4.7). Off by default so the archived A–F
    #: results stay comparable; turn it on to measure runtime-enforced refusal
    #: against the prompt-only baseline.
    safety: SafetyPolicyConfig = Field(default_factory=SafetyPolicyConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    dataset: DatasetConfig = Field(default_factory=DatasetConfig)
    world: WorldConfig = Field(default_factory=WorldConfig)
    trace: TraceConfig = Field(default_factory=TraceConfig)
    repetitions: int = 1
    seed_base: int = 42
    tool_count: int | None = None


def load_config(path: str | Path) -> ExperimentConfig:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return ExperimentConfig.model_validate(raw)
