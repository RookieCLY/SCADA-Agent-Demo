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
    initial_value: float | None = None
    simulation_mode: str | None = None


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
    #: Annunciation state. ``enabled`` says whether the alarm is configured;
    #: these say whether an operator would ever hear it. All three tools that
    #: write them — acknowledge/shelve/suppress — previously returned ``ok`` and
    #: changed nothing, so a run that silenced a safety interlock was
    #: indistinguishable from one that refused to.
    acknowledged: bool = False
    suppressed: bool = False
    shelved_minutes: int | None = None


class Device(BaseModel):
    """A registered device in the project catalogue.

    Every field past ``tags`` was added when the ``manage_devices`` tools were
    made to write. Before that the domain's 20 tools validated, returned ``ok``
    and left the world untouched, so the collection only ever held whatever a
    golden case seeded — the one failure shape a trace cannot show, since a
    silent no-op and a correct write are the same successful call. All default,
    so worlds serialized against the old four-field model still validate.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: str
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    location: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    area_id: str | None = None
    template_id: str | None = None
    #: Communication parameters, written by ``configure_device_params``.
    protocol: str | None = None
    address: str | None = None
    port: int | None = None
    polling_interval_ms: int | None = None
    timeout_ms: int | None = None
    retry_count: int | None = None
    #: Template-carried alarm defaults, written by ``set_device_alarm_limits``.
    low_limit: float | None = None
    high_limit: float | None = None
    #: Last calibration reference recorded by ``calibrate_device``.
    calibration_reference: float | None = None


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
    #: The archive itself, coarsely: how far back stored data reaches and how
    #: much of it there is. Added so ``purge_history`` has something to destroy.
    #: It is described as destructive and is named in the ``forbidden_tools`` of
    #: every golden case, but it only ever validated the config and returned
    #: ``ok`` — its ``intended_entities`` claimed ``histories.<tag>.data``, a
    #: path that could not exist. A purge that removes nothing makes the §4.7
    #: safety probe untestable: denying it prevents no loss.
    stored_days: int = 0
    stored_samples: int = 0
    #: How the archive is kept, as distinct from ``storage_mode`` (when a sample
    #: is taken). Written by ``set_storage_policy``.
    storage_policy: Literal["raw", "compressed", "aggregated"] = "raw"


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
