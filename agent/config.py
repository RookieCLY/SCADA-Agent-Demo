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


class StateMachineConfig(BaseModel):
    enabled: bool = False


class TraceConfig(BaseModel):
    record_llm_io: bool = False


class ArchitectureConfig(BaseModel):
    hierarchical_tools: bool = False
    tool_rag: ToolRAGConfig = Field(default_factory=ToolRAGConfig)
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    state_machine: StateMachineConfig = Field(default_factory=StateMachineConfig)
    resources_separation: bool = False


class ModelConfig(BaseModel):
    provider: Literal["mock", "anthropic", "openai", "deepseek", "xiaomi-mimo", "openrouter", "glm"] = "mock"
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
