"""manage_alarms — Domain Tool covering alarm CRUD.

Each action is a MockTool subclass so it carries its own Pydantic args model,
its own L2/L3 checks, and the mandatory ``intended_entities`` / ``referenced_entities``
metadata. The Domain Tool itself is just a discriminated-union dispatcher.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator

from tools._base import ErrorCode, MockTool, ToolResult, fail, ok
from world import AlarmType, MockWorld, Priority
from world.models import Alarm

DOMAIN = "manage_alarms"


# ---------------------------------------------------------------- create_analog
class CreateAnalogAlarmArgs(BaseModel):
    action: Literal["create_analog_alarm"] = "create_analog_alarm"
    id: str = Field(description="Unique alarm ID, e.g. 'alarm_temp_high_101'")
    tag: str = Field(description="The SCADA point tag to monitor, e.g. 'TEMP_101'")
    high_limit: float | None = None
    low_limit: float | None = None
    deadband: float = Field(default=0.0, ge=0)
    priority: Priority = "medium"

    @model_validator(mode="after")
    def at_least_one_limit(self):
        if self.high_limit is None and self.low_limit is None:
            raise ValueError("at least one of high_limit / low_limit required")
        return self


class CreateAnalogAlarm(MockTool):
    name = "create_analog_alarm"
    domain = DOMAIN
    action = "create_analog_alarm"
    description = "Create an analog alarm with high/low limits on an existing analog point."
    args_model = CreateAnalogAlarmArgs
    examples = [
        "给反应釜温度加个超限报警",
        "TEMP_101 配置高温报警,阈值 80",
        "set up over-temperature alarm for the reactor",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"alarms.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"points.{args.tag}"]

    def run(self, args: CreateAnalogAlarmArgs, world: MockWorld) -> ToolResult:
        if args.id in world.alarms:
            return fail(ErrorCode.ALREADY_EXISTS, f"alarm {args.id} already exists")
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        point = world.points[args.tag]
        if point.type != "analog":
            return fail(
                ErrorCode.TYPE_MISMATCH,
                f"analog alarm expects analog point, got {point.type}",
            )
        alarm = Alarm(
            id=args.id,
            tag=args.tag,
            type="analog",
            high_limit=args.high_limit,
            low_limit=args.low_limit,
            deadband=args.deadband,
            priority=args.priority,
        )
        world.alarms[args.id] = alarm
        return ok(
            data={"alarm_id": args.id},
            world_diff={"added_or_modified": {f"alarms.{args.id}": alarm.model_dump()}, "removed": []},
        )


# ---------------------------------------------------------------- create_digital
class CreateDigitalAlarmArgs(BaseModel):
    action: Literal["create_digital_alarm"] = "create_digital_alarm"
    id: str
    tag: str
    priority: Priority = "medium"


class CreateDigitalAlarm(MockTool):
    name = "create_digital_alarm"
    domain = DOMAIN
    action = "create_digital_alarm"
    description = "Create a digital alarm that fires when a boolean tag becomes true."
    args_model = CreateDigitalAlarmArgs
    examples = ["pump fault alarm", "digital input goes high — alarm"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"alarms.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"points.{args.tag}"]

    def run(self, args: CreateDigitalAlarmArgs, world: MockWorld) -> ToolResult:
        if args.id in world.alarms:
            return fail(ErrorCode.ALREADY_EXISTS, f"alarm {args.id} already exists")
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        point = world.points[args.tag]
        if point.type != "digital":
            return fail(
                ErrorCode.TYPE_MISMATCH,
                f"digital alarm expects digital point, got {point.type}",
            )
        alarm = Alarm(id=args.id, tag=args.tag, type="digital", priority=args.priority)
        world.alarms[args.id] = alarm
        return ok(
            data={"alarm_id": args.id},
            world_diff={"added_or_modified": {f"alarms.{args.id}": alarm.model_dump()}, "removed": []},
        )


# ---------------------------------------------------------------- set_threshold
class SetThresholdArgs(BaseModel):
    action: Literal["set_threshold"] = "set_threshold"
    id: str
    high_limit: float | None = None
    low_limit: float | None = None
    deadband: float | None = None

    @model_validator(mode="after")
    def at_least_one(self):
        if self.high_limit is None and self.low_limit is None and self.deadband is None:
            raise ValueError("must update at least one of high_limit / low_limit / deadband")
        return self


class SetThreshold(MockTool):
    name = "set_threshold"
    domain = DOMAIN
    action = "set_threshold"
    description = "Update the threshold/deadband on an existing analog alarm."
    args_model = SetThresholdArgs
    examples = ["调整温度报警上限到 90", "raise the high limit"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"alarms.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"alarms.{args.id}"]

    def run(self, args: SetThresholdArgs, world: MockWorld) -> ToolResult:
        if args.id not in world.alarms:
            return fail(ErrorCode.ALARM_NOT_FOUND, f"alarm {args.id} not found")
        alarm = world.alarms[args.id]
        if alarm.type != "analog":
            return fail(ErrorCode.TYPE_MISMATCH, "set_threshold only valid for analog alarms")
        if args.high_limit is not None:
            alarm.high_limit = args.high_limit
        if args.low_limit is not None:
            alarm.low_limit = args.low_limit
        if args.deadband is not None:
            alarm.deadband = args.deadband
        return ok(
            data={"alarm_id": args.id},
            world_diff={"added_or_modified": {f"alarms.{args.id}": alarm.model_dump()}, "removed": []},
        )


# ---------------------------------------------------------------- enable / disable
class EnableAlarmArgs(BaseModel):
    action: Literal["enable_alarm"] = "enable_alarm"
    id: str


class EnableAlarm(MockTool):
    name = "enable_alarm"
    domain = DOMAIN
    action = "enable_alarm"
    description = "Enable a previously disabled alarm."
    args_model = EnableAlarmArgs
    examples = ["重新打开报警", "enable alarm"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"alarms.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"alarms.{args.id}"]

    def run(self, args: EnableAlarmArgs, world: MockWorld) -> ToolResult:
        if args.id not in world.alarms:
            return fail(ErrorCode.ALARM_NOT_FOUND, f"alarm {args.id} not found")
        world.alarms[args.id].enabled = True
        return ok(
            data={"alarm_id": args.id},
            world_diff={"added_or_modified": {f"alarms.{args.id}.enabled": True}, "removed": []},
        )


class DisableAlarmArgs(BaseModel):
    action: Literal["disable_alarm"] = "disable_alarm"
    id: str


class DisableAlarm(MockTool):
    name = "disable_alarm"
    domain = DOMAIN
    action = "disable_alarm"
    description = "Temporarily disable an alarm without deleting it."
    args_model = DisableAlarmArgs
    examples = ["停用报警", "disable alarm"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"alarms.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"alarms.{args.id}"]

    def run(self, args: DisableAlarmArgs, world: MockWorld) -> ToolResult:
        if args.id not in world.alarms:
            return fail(ErrorCode.ALARM_NOT_FOUND, f"alarm {args.id} not found")
        world.alarms[args.id].enabled = False
        return ok(
            data={"alarm_id": args.id},
            world_diff={"added_or_modified": {f"alarms.{args.id}.enabled": False}, "removed": []},
        )


# ---------------------------------------------------------------- delete
class DeleteAlarmArgs(BaseModel):
    action: Literal["delete_alarm"] = "delete_alarm"
    id: str


class DeleteAlarm(MockTool):
    name = "delete_alarm"
    domain = DOMAIN
    action = "delete_alarm"
    description = "Remove an alarm definition entirely."
    args_model = DeleteAlarmArgs
    examples = ["删除报警", "remove this alarm"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"alarms.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"alarms.{args.id}"]

    def run(self, args: DeleteAlarmArgs, world: MockWorld) -> ToolResult:
        if args.id not in world.alarms:
            return fail(ErrorCode.ALARM_NOT_FOUND, f"alarm {args.id} not found")
        del world.alarms[args.id]
        return ok(
            data={"alarm_id": args.id},
            world_diff={"added_or_modified": {}, "removed": [f"alarms.{args.id}"]},
        )


# ---------------------------------------------------------------- registry hookup
ALARM_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (
        CreateAnalogAlarm,
        CreateDigitalAlarm,
        SetThreshold,
        EnableAlarm,
        DisableAlarm,
        DeleteAlarm,
    )
}

# Discriminated union for the Domain-Tool LLM-facing schema (hierarchical mode)
ManageAlarmsArgs = Annotated[
    Union[
        CreateAnalogAlarmArgs,
        CreateDigitalAlarmArgs,
        SetThresholdArgs,
        EnableAlarmArgs,
        DisableAlarmArgs,
        DeleteAlarmArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "ALARM_ACTIONS",
    "DOMAIN",
    "ManageAlarmsArgs",
    "CreateAnalogAlarm",
    "CreateAnalogAlarmArgs",
    "CreateDigitalAlarm",
    "CreateDigitalAlarmArgs",
    "DeleteAlarm",
    "DeleteAlarmArgs",
    "DisableAlarm",
    "DisableAlarmArgs",
    "EnableAlarm",
    "EnableAlarmArgs",
    "SetThreshold",
    "SetThresholdArgs",
]
