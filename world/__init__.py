"""Mock World — Pydantic-backed in-memory state for the SCADA agent demo.

Re-exports the canonical types so callers can ``from world import MockWorld, Page``.
"""
from world._base import WorldStore
from world.memory_backend import MockWorld, deep_copy_world
from world.models import (
    Alarm,
    AlarmType,
    Deployment,
    DeploymentStatus,
    Device,
    HistoryConfig,
    HistoryStorageMode,
    Page,
    Point,
    PointType,
    Priority,
    Script,
    ScriptTrigger,
    Widget,
)

__all__ = [
    "Alarm",
    "AlarmType",
    "Deployment",
    "DeploymentStatus",
    "Device",
    "HistoryConfig",
    "HistoryStorageMode",
    "MockWorld",
    "Page",
    "Point",
    "PointType",
    "Priority",
    "Script",
    "ScriptTrigger",
    "Widget",
    "WorldStore",
    "deep_copy_world",
]
