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
    #: Plan-and-Execute turn structure. Composes with every other lever — it
    #: changes *when* the model is asked, not *what* it may see.
    plan_execute: PlanExecuteConfig = Field(default_factory=PlanExecuteConfig)


class ModelConfig(BaseModel):
    provider: Literal["mock", "anthropic", "openai", "deepseek", "xiaomi-mimo", "openrouter", "glm", "docode"] = "mock"
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
