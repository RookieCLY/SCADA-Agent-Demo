"""Agent package — Phase 1.

Keep this module light: importing it should not eagerly import
``agent.orchestrator`` (otherwise ``python -m agent.orchestrator`` emits a
``RuntimeWarning`` about the module being re-executed). Callers should import
``Agent`` / ``assemble`` from ``agent.orchestrator`` directly.
"""
from agent.config import (
    ArchitectureConfig,
    ExperimentConfig,
    ModelConfig,
    StateMachineConfig,
    ToolRAGConfig,
    WorkflowConfig,
    load_config,
)
from agent.state_machine import INITIAL_STATE, STATES, StateMachine
from agent.tool_registry import ToolRegistry, build_default_registry

__all__ = [
    "ArchitectureConfig",
    "ExperimentConfig",
    "INITIAL_STATE",
    "ModelConfig",
    "STATES",
    "StateMachine",
    "StateMachineConfig",
    "ToolRAGConfig",
    "ToolRegistry",
    "WorkflowConfig",
    "build_default_registry",
    "load_config",
]
