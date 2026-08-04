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


# ============================================================ extension tools
def _al_diff(alarm_id, alarm):
    return {"added_or_modified": {f"alarms.{alarm_id}": alarm.model_dump()}, "removed": []}


def _need_alarm(world, alarm_id):
    if alarm_id not in world.alarms:
        return fail(ErrorCode.ALARM_NOT_FOUND, f"alarm {alarm_id} not found")
    return None


class SetAlarmHighLimitArgs(BaseModel):
    action: Literal["set_alarm_high_limit"] = "set_alarm_high_limit"
    alarm_id: str
    high_limit: float


class SetAlarmHighLimit(MockTool):
    name = "set_alarm_high_limit"
    domain = DOMAIN; action = "set_alarm_high_limit"
    description = "Set the high trigger limit of an existing analog alarm."
    args_model = SetAlarmHighLimitArgs
    examples = ["把高限报警值改成 90", "set the high alarm limit to 90", "调整报警上限"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: SetAlarmHighLimitArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        a = world.alarms[args.alarm_id]; a.high_limit = args.high_limit
        return ok(data={"alarm_id": args.alarm_id}, world_diff=_al_diff(args.alarm_id, a))


class SetAlarmLowLimitArgs(BaseModel):
    action: Literal["set_alarm_low_limit"] = "set_alarm_low_limit"
    alarm_id: str
    low_limit: float


class SetAlarmLowLimit(MockTool):
    name = "set_alarm_low_limit"
    domain = DOMAIN; action = "set_alarm_low_limit"
    description = "Set the low trigger limit of an existing analog alarm."
    args_model = SetAlarmLowLimitArgs
    examples = ["把低限报警值设为 5", "set the low alarm limit to 5", "调整报警下限"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: SetAlarmLowLimitArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        a = world.alarms[args.alarm_id]; a.low_limit = args.low_limit
        return ok(data={"alarm_id": args.alarm_id}, world_diff=_al_diff(args.alarm_id, a))


class SetAlarmDeadbandArgs(BaseModel):
    action: Literal["set_alarm_deadband"] = "set_alarm_deadband"
    alarm_id: str
    deadband: float = Field(ge=0)


class SetAlarmDeadband(MockTool):
    name = "set_alarm_deadband"
    domain = DOMAIN; action = "set_alarm_deadband"
    description = "Set the deadband (hysteresis) of an alarm to stop chattering."
    args_model = SetAlarmDeadbandArgs
    examples = ["给报警设置死区防抖", "set a 1.0 deadband to avoid chattering", "报警一直跳，加个死区"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: SetAlarmDeadbandArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        a = world.alarms[args.alarm_id]; a.deadband = args.deadband
        return ok(data={"alarm_id": args.alarm_id}, world_diff=_al_diff(args.alarm_id, a))


class AcknowledgeAlarmArgs(BaseModel):
    action: Literal["acknowledge_alarm"] = "acknowledge_alarm"
    alarm_id: str
    operator: str | None = None


class AcknowledgeAlarm(MockTool):
    name = "acknowledge_alarm"
    domain = DOMAIN; action = "acknowledge_alarm"
    description = "Acknowledge an active alarm (design-time test / config context)."
    args_model = AcknowledgeAlarmArgs
    examples = ["确认这条报警", "acknowledge the high-temp alarm", "签收报警"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}.ack"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: AcknowledgeAlarmArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        a = world.alarms[args.alarm_id]; a.acknowledged = True
        return ok(data={"alarm_id": args.alarm_id, "acknowledged": True},
                  world_diff=_al_diff(args.alarm_id, a))


class ShelveAlarmArgs(BaseModel):
    action: Literal["shelve_alarm"] = "shelve_alarm"
    alarm_id: str
    minutes: int = Field(default=60, ge=1, le=10080)


class ShelveAlarm(MockTool):
    name = "shelve_alarm"
    domain = DOMAIN; action = "shelve_alarm"
    description = "Temporarily shelve an alarm for a fixed duration."
    args_model = ShelveAlarmArgs
    examples = ["把这条报警临时搁置一小时", "shelve this alarm for 30 minutes", "暂时屏蔽这个报警"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}.shelved"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: ShelveAlarmArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        a = world.alarms[args.alarm_id]; a.shelved_minutes = args.minutes
        return ok(data={"alarm_id": args.alarm_id, "shelved_minutes": args.minutes},
                  world_diff=_al_diff(args.alarm_id, a))


class UnshelveAlarmArgs(BaseModel):
    action: Literal["unshelve_alarm"] = "unshelve_alarm"
    alarm_id: str


class UnshelveAlarm(MockTool):
    name = "unshelve_alarm"
    domain = DOMAIN; action = "unshelve_alarm"
    description = "Return a shelved alarm to active monitoring."
    args_model = UnshelveAlarmArgs
    examples = ["取消报警的搁置", "unshelve this alarm now", "恢复这个报警的监视"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}.shelved"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: UnshelveAlarmArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        a = world.alarms[args.alarm_id]; a.shelved_minutes = None
        return ok(data={"alarm_id": args.alarm_id, "shelved": False},
                  world_diff=_al_diff(args.alarm_id, a))


class SetAlarmPriorityArgs(BaseModel):
    action: Literal["set_alarm_priority"] = "set_alarm_priority"
    alarm_id: str
    #: Must match ``world.Alarm.priority`` and the two alarm-creating tools, which
    #: are all ``high|medium|low``. This alone also accepted ``critical``, which was
    #: harmless only while ``run`` wrote nothing: once it writes, and with
    #: ``validate_assignment`` off on the model, ``critical`` lands in the field
    #: unchecked and the world then fails to re-validate on any serialisation
    #: round-trip — silent corruption rather than an error. (``list_active_alarms``
    #: keeps ``critical``/``all``; it is a query filter, not a write.)
    priority: Literal["low", "medium", "high"]


class SetAlarmPriority(MockTool):
    name = "set_alarm_priority"
    domain = DOMAIN; action = "set_alarm_priority"
    description = "Set the priority/severity of an alarm."
    args_model = SetAlarmPriorityArgs
    examples = ["把这条报警设为高优先级", "make this a critical alarm", "调整报警等级"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}.priority"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: SetAlarmPriorityArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        # This returned ok() without writing anything. ``Alarm.priority`` exists,
        # so unlike the stub tools whose target field is simply absent from the
        # model, this one had somewhere to write and reported a success it had not
        # performed. In Phase 4 it was called successfully 9 times and produced a
        # world_diff 0 times, while 8 golden cases assert ``alarms.*.priority`` —
        # golden-022 and golden-043 exist precisely to change the priority of an
        # *existing* alarm, and could not pass at all.
        alarm = world.alarms[args.alarm_id]
        alarm.priority = args.priority
        return ok(
            data={"alarm_id": args.alarm_id, "priority": args.priority},
            world_diff={
                "added_or_modified": {f"alarms.{args.alarm_id}.priority": args.priority},
                "removed": [],
            },
        )


class SetAlarmMessageArgs(BaseModel):
    action: Literal["set_alarm_message"] = "set_alarm_message"
    alarm_id: str
    message: str


class SetAlarmMessage(MockTool):
    name = "set_alarm_message"
    domain = DOMAIN; action = "set_alarm_message"
    description = "Set the operator-facing message text of an alarm."
    args_model = SetAlarmMessageArgs
    examples = ["设置报警的提示文字", "set the alarm message to 'reactor overtemp'", "改一下报警消息内容"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}.message"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: SetAlarmMessageArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        return ok(data={"alarm_id": args.alarm_id, "message_set": True})


class SetAlarmOnDelayArgs(BaseModel):
    action: Literal["set_alarm_on_delay"] = "set_alarm_on_delay"
    alarm_id: str
    delay_seconds: float = Field(ge=0, le=3600)


class SetAlarmOnDelay(MockTool):
    name = "set_alarm_on_delay"
    domain = DOMAIN; action = "set_alarm_on_delay"
    description = "Require the condition to persist N seconds before the alarm trips."
    args_model = SetAlarmOnDelayArgs
    examples = ["给报警加一个触发延时", "only trip after the condition holds 5s", "报警延时确认"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}.on_delay"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: SetAlarmOnDelayArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        return ok(data={"alarm_id": args.alarm_id, "on_delay": args.delay_seconds})


class CreateAlarmGroupArgs(BaseModel):
    action: Literal["create_alarm_group"] = "create_alarm_group"
    group_id: str
    name: str


class CreateAlarmGroup(MockTool):
    name = "create_alarm_group"
    domain = DOMAIN; action = "create_alarm_group"
    description = "Create an alarm group for organizing and bulk-managing alarms."
    args_model = CreateAlarmGroupArgs
    examples = ["新建一个报警分组", "create an alarm group for Reactor 1", "把报警归类到一个组"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarm_groups.{args.group_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: CreateAlarmGroupArgs, world: MockWorld) -> ToolResult:
        return ok(data={"group_id": args.group_id, "name": args.name})


class AddAlarmToGroupArgs(BaseModel):
    action: Literal["add_alarm_to_group"] = "add_alarm_to_group"
    group_id: str
    alarm_id: str


class AddAlarmToGroup(MockTool):
    name = "add_alarm_to_group"
    domain = DOMAIN; action = "add_alarm_to_group"
    description = "Add an alarm to an alarm group."
    args_model = AddAlarmToGroupArgs
    examples = ["把报警加到分组里", "put this alarm in the Reactor1 group", "报警归组"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarm_groups.{args.group_id}.members"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: AddAlarmToGroupArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        return ok(data={"group_id": args.group_id, "alarm_id": args.alarm_id})


class TestAlarmArgs(BaseModel):
    action: Literal["test_alarm"] = "test_alarm"
    alarm_id: str


class TestAlarm(MockTool):
    name = "test_alarm"
    domain = DOMAIN; action = "test_alarm"
    description = "Simulate the alarm's trigger condition to verify it fires correctly."
    args_model = TestAlarmArgs
    examples = ["测试一下这个报警会不会触发", "test-fire this alarm", "验证报警配置对不对"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: TestAlarmArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        return ok(data={"alarm_id": args.alarm_id, "would_trigger": True})


class ListActiveAlarmsArgs(BaseModel):
    action: Literal["list_active_alarms"] = "list_active_alarms"
    priority: Literal["low", "medium", "high", "critical", "all"] = "all"


class ListActiveAlarms(MockTool):
    name = "list_active_alarms"
    domain = DOMAIN; action = "list_active_alarms"
    description = "List currently active (unacknowledged) alarms, optionally by priority."
    args_model = ListActiveAlarmsArgs
    examples = ["列出当前活动的报警", "show all critical active alarms", "现在有哪些报警在响"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ListActiveAlarmsArgs, world: MockWorld) -> ToolResult:
        return ok(data={"active": [], "count": 0})


class GetAlarmHistoryArgs(BaseModel):
    action: Literal["get_alarm_history"] = "get_alarm_history"
    tag: str | None = None
    last_n_hours: int = Field(default=24, ge=1, le=8760)


class GetAlarmHistory(MockTool):
    name = "get_alarm_history"
    domain = DOMAIN; action = "get_alarm_history"
    description = "Retrieve the alarm event history for analysis."
    args_model = GetAlarmHistoryArgs
    examples = ["查看报警历史记录", "show the alarm history for the last day", "调取报警历史"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: GetAlarmHistoryArgs, world: MockWorld) -> ToolResult:
        return ok(data={"events": [], "count": 0})


class SuppressAlarmArgs(BaseModel):
    action: Literal["suppress_alarm"] = "suppress_alarm"
    alarm_id: str
    reason: str | None = None


class SuppressAlarm(MockTool):
    name = "suppress_alarm"
    domain = DOMAIN; action = "suppress_alarm"
    description = "Suppress an alarm from annunciation (design-time maintenance mode)."
    args_model = SuppressAlarmArgs
    examples = ["抑制这条报警", "suppress this nuisance alarm", "把这个报警屏蔽掉"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}.suppressed"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: SuppressAlarmArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        a = world.alarms[args.alarm_id]; a.suppressed = True
        return ok(data={"alarm_id": args.alarm_id, "suppressed": True},
                  world_diff=_al_diff(args.alarm_id, a))


class SetAlarmEnableConditionArgs(BaseModel):
    action: Literal["set_alarm_enable_condition"] = "set_alarm_enable_condition"
    alarm_id: str
    condition_tag: str


class SetAlarmEnableCondition(MockTool):
    name = "set_alarm_enable_condition"
    domain = DOMAIN; action = "set_alarm_enable_condition"
    description = "Gate an alarm so it is only active when a condition tag is true (e.g. pump running)."
    args_model = SetAlarmEnableConditionArgs
    examples = ["设置报警的使能条件", "only enable this alarm when the pump runs", "报警按工况自动使能"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}.enable_condition"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"alarms.{args.alarm_id}"]

    def run(self, args: SetAlarmEnableConditionArgs, world: MockWorld) -> ToolResult:
        err = _need_alarm(world, args.alarm_id)
        if err: return err
        return ok(data={"alarm_id": args.alarm_id, "condition_tag": args.condition_tag})


ALARM_ACTIONS.update({
    cls.action: cls
    for cls in (
        SetAlarmHighLimit, SetAlarmLowLimit, SetAlarmDeadband, AcknowledgeAlarm,
        ShelveAlarm, UnshelveAlarm, SetAlarmPriority, SetAlarmMessage, SetAlarmOnDelay,
        CreateAlarmGroup, AddAlarmToGroup, TestAlarm, ListActiveAlarms, GetAlarmHistory,
        SuppressAlarm, SetAlarmEnableCondition,
    )
})
