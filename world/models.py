"""Pydantic schemas for the Mock World.

These are the canonical entity types referenced by both Tools (write side) and
Resources (read side, Phase 2). They live in plain Pydantic models so they can
be (de)serialised to JSON for trace snapshots and Golden Dataset `initial_world`
fields.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PointType = Literal["analog", "digital", "string"]
AlarmType = Literal["analog", "digital"]
Priority = Literal["high", "medium", "low"]


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str
    type: PointType
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    description: str | None = None


class Widget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    page_id: str
    type: str
    position: tuple[int, int]
    size: tuple[int, int]
    bindings: dict[str, str] = Field(default_factory=dict)
    expected_binding_types: dict[str, list[str]] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    resolution: tuple[int, int] = (1920, 1080)
    background: str = "#FFFFFF"
    widgets: dict[str, Widget] = Field(default_factory=dict)


class Alarm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tag: str
    type: AlarmType
    high_limit: float | None = None
    low_limit: float | None = None
    deadband: float = 0.0
    priority: Priority = "medium"
    enabled: bool = True


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: str
    tags: list[str] = Field(default_factory=list)


HistoryStorageMode = Literal["on_change", "periodic"]


class HistoryConfig(BaseModel):
    """Historian configuration for a single tag — Phase 2 manage_history domain."""

    model_config = ConfigDict(extra="forbid")

    tag: str
    enabled: bool = True
    storage_mode: HistoryStorageMode = "periodic"
    sample_interval_s: float = 1.0
    deadband: float = 0.0
    retention_days: int = 30


ScriptTrigger = Literal["on_change", "on_alarm", "periodic", "on_event"]


class Script(BaseModel):
    """User script — Phase 2 manage_scripts domain.

    Source body is held verbatim; the demo runtime never executes it. We track it
    only so that workflows / agents that author scripts can be measured.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    trigger: ScriptTrigger
    bound_tag: str | None = None
    period_s: float | None = None
    body: str = ""
    enabled: bool = True


DeploymentStatus = Literal["draft", "validated", "deployed", "failed"]


class Deployment(BaseModel):
    """Project-level deployment record — Phase 2 deployment domain."""

    model_config = ConfigDict(extra="forbid")

    id: str
    target: str = "default"
    status: DeploymentStatus = "draft"
    validation_errors: list[str] = Field(default_factory=list)
    notes: str = ""
