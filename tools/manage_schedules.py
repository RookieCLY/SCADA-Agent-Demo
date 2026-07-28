"""manage_schedules — timed / event-driven automation job stubs.

SCADA systems use schedulers for periodic tasks: report generation, data
archival, maintenance routines, and batch start/stop sequences.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import MockTool, ToolResult, ok

DOMAIN = "manage_schedules"


# ---------------------------------------------------------------- create_schedule
class CreateScheduleArgs(BaseModel):
    action: Literal["create_schedule"] = "create_schedule"
    schedule_id: str = Field(description="Unique schedule identifier, e.g. 'nightly_backup'")
    schedule_name: str
    description: str | None = None


class CreateSchedule(MockTool):
    name = "create_schedule"
    domain = DOMAIN; action = "create_schedule"
    description = "Create a new scheduled automation job."
    args_model = CreateScheduleArgs
    examples = ["创建一个定时任务", "schedule nightly data backup", "添加一个自动维护任务"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: CreateScheduleArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "created": True})


# ---------------------------------------------------------------- set_schedule_trigger
class SetScheduleTriggerArgs(BaseModel):
    action: Literal["set_schedule_trigger"] = "set_schedule_trigger"
    schedule_id: str
    trigger_type: Literal["cron", "interval", "at_time", "on_event", "on_startup"] = "cron"
    cron_expression: str | None = Field(default="0 8 * * *", description="Standard 5-field cron")
    interval_s: float | None = Field(default=None, ge=1.0)
    event_tag: str | None = Field(default=None, description="For 'on_event': SCADA tag to watch")
    event_condition: Literal["equals", "greater_than", "less_than", "changes"] | None = None
    event_value: float | None = None


class SetScheduleTrigger(MockTool):
    name = "set_schedule_trigger"
    domain = DOMAIN; action = "set_schedule_trigger"
    description = "Configure the trigger condition for a scheduled job."
    args_model = SetScheduleTriggerArgs
    examples = ["设置每天早上8点执行", "trigger on TEMP_101 > 80", "每5分钟运行一次", "set cron trigger for report"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}.trigger"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        refs = [f"schedules.{args.schedule_id}"]
        if args.event_tag:
            refs.append(f"points.{args.event_tag}")
        return refs

    def run(self, args: SetScheduleTriggerArgs, world: object) -> ToolResult:
        return ok(data={"trigger_set": True})


# ---------------------------------------------------------------- add_schedule_action
class AddScheduleActionArgs(BaseModel):
    action: Literal["add_schedule_action"] = "add_schedule_action"
    schedule_id: str
    action_id: str = Field(description="Unique action identifier, e.g. 'export_logs'")
    action_type: Literal["run_script", "generate_report", "export_data", "send_notification", "backup_database", "call_api"] = "run_script"
    target: str = Field(description="Target identifier: script name, report template, or URL")
    parameters: dict[str, str] = Field(default_factory=dict)


class AddScheduleAction(MockTool):
    name = "add_schedule_action"
    domain = DOMAIN; action = "add_schedule_action"
    description = "Add an action (what to do) to a scheduled job."
    args_model = AddScheduleActionArgs
    examples = ["定时任务中添加导出数据操作", "add report generation to nightly schedule", "添加备份数据库动作"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}.actions.{args.action_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        refs = [f"schedules.{args.schedule_id}"]
        if args.action_type == "run_script":
            refs.append(f"scripts.{args.target}")
        elif args.action_type == "generate_report":
            refs.append(f"reports.{args.target}")
        return refs

    def run(self, args: AddScheduleActionArgs, world: object) -> ToolResult:
        return ok(data={"action_id": args.action_id, "added": True})


# ---------------------------------------------------------------- enable_schedule
class EnableScheduleArgs(BaseModel):
    action: Literal["enable_schedule"] = "enable_schedule"
    schedule_id: str
    enabled: bool = True


class EnableSchedule(MockTool):
    name = "enable_schedule"
    domain = DOMAIN; action = "enable_schedule"
    description = "Enable or disable a scheduled job."
    args_model = EnableScheduleArgs
    examples = ["启用定时任务", "enable nightly backup schedule", "暂停定时报表"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}.status"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]

    def run(self, args: EnableScheduleArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "enabled": args.enabled})


# ---------------------------------------------------------------- get_schedule_status
class GetScheduleStatusArgs(BaseModel):
    action: Literal["get_schedule_status"] = "get_schedule_status"
    schedule_id: str


class GetScheduleStatus(MockTool):
    name = "get_schedule_status"
    domain = DOMAIN; action = "get_schedule_status"
    description = "Query the execution history and current state of a scheduled job."
    args_model = GetScheduleStatusArgs
    examples = ["查看定时任务状态", "check schedule execution history", "上次定时任务什么时候跑的"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]

    def run(self, args: GetScheduleStatusArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "status": "idle", "last_run": "2026-06-12T08:00:00Z", "next_run": "2026-06-13T08:00:00Z", "execution_count": 42, "last_result": "success"})


# ---------------------------------------------------------------- list_schedules
class ListSchedulesArgs(BaseModel):
    action: Literal["list_schedules"] = "list_schedules"
    enabled_only: bool = False


class ListSchedules(MockTool):
    name = "list_schedules"
    domain = DOMAIN; action = "list_schedules"
    description = "List all scheduled jobs, optionally filtered to enabled only."
    args_model = ListSchedulesArgs
    examples = ["列出所有定时任务", "show me active schedules", "查看当前的自动化任务"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ListSchedulesArgs, world: object) -> ToolResult:
        return ok(data={"schedules": [], "count": 0})


# ---------------------------------------------------------------- registry hookup
SCHEDULE_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (CreateSchedule, SetScheduleTrigger, AddScheduleAction, EnableSchedule, GetScheduleStatus, ListSchedules)
}

ManageSchedulesArgs = Annotated[
    Union[
        CreateScheduleArgs, SetScheduleTriggerArgs, AddScheduleActionArgs,
        EnableScheduleArgs, GetScheduleStatusArgs, ListSchedulesArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN", "ManageSchedulesArgs", "SCHEDULE_ACTIONS",
    "CreateSchedule", "SetScheduleTrigger", "AddScheduleAction",
    "EnableSchedule", "GetScheduleStatus", "ListSchedules",
]


# ============================================================ extension tools
class DisableScheduleArgs(BaseModel):
    action: Literal["disable_schedule"] = "disable_schedule"
    schedule_id: str


class DisableSchedule(MockTool):
    name = "disable_schedule"
    domain = DOMAIN; action = "disable_schedule"
    description = "Disable a scheduled job so it stops firing."
    args_model = DisableScheduleArgs
    examples = ["停用这个定时任务", "disable the nightly backup schedule", "先关掉那个计划任务"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}.enabled"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]

    def run(self, args: DisableScheduleArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "enabled": False})


class DeleteScheduleArgs(BaseModel):
    action: Literal["delete_schedule"] = "delete_schedule"
    schedule_id: str


class DeleteSchedule(MockTool):
    name = "delete_schedule"
    domain = DOMAIN; action = "delete_schedule"
    description = "Delete a scheduled job."
    args_model = DeleteScheduleArgs
    examples = ["删除定时任务", "delete the obsolete report schedule", "把这个计划任务移除"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]

    def run(self, args: DeleteScheduleArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "deleted": True})


class RunScheduleNowArgs(BaseModel):
    action: Literal["run_schedule_now"] = "run_schedule_now"
    schedule_id: str


class RunScheduleNow(MockTool):
    name = "run_schedule_now"
    domain = DOMAIN; action = "run_schedule_now"
    description = "Trigger a scheduled job immediately, out of band."
    args_model = RunScheduleNowArgs
    examples = ["立刻运行一次这个任务", "run the backup job now", "手动触发定时任务"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]

    def run(self, args: RunScheduleNowArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "triggered": True})


class PauseScheduleArgs(BaseModel):
    action: Literal["pause_schedule"] = "pause_schedule"
    schedule_id: str
    until: str | None = Field(default=None, description="Optional ISO timestamp to auto-resume")


class PauseSchedule(MockTool):
    name = "pause_schedule"
    domain = DOMAIN; action = "pause_schedule"
    description = "Temporarily pause a schedule without deleting it."
    args_model = PauseScheduleArgs
    examples = ["暂停这个定时任务", "pause the schedule during maintenance", "临时挂起计划任务"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}.state"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]

    def run(self, args: PauseScheduleArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "paused": True})


class ResumeScheduleArgs(BaseModel):
    action: Literal["resume_schedule"] = "resume_schedule"
    schedule_id: str


class ResumeSchedule(MockTool):
    name = "resume_schedule"
    domain = DOMAIN; action = "resume_schedule"
    description = "Resume a previously paused schedule."
    args_model = ResumeScheduleArgs
    examples = ["恢复这个定时任务", "resume the paused schedule", "让计划任务继续跑"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}.state"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]

    def run(self, args: ResumeScheduleArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "paused": False})


class SetScheduleTimezoneArgs(BaseModel):
    action: Literal["set_schedule_timezone"] = "set_schedule_timezone"
    schedule_id: str
    timezone: str = Field(description="IANA timezone, e.g. 'Asia/Shanghai'")


class SetScheduleTimezone(MockTool):
    name = "set_schedule_timezone"
    domain = DOMAIN; action = "set_schedule_timezone"
    description = "Set the timezone a schedule's cron/interval trigger is evaluated in."
    args_model = SetScheduleTimezoneArgs
    examples = ["设置定时任务的时区", "run this schedule in Asia/Shanghai time", "把计划任务改成本地时区"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}.timezone"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]

    def run(self, args: SetScheduleTimezoneArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "timezone": args.timezone})


class GetScheduleHistoryArgs(BaseModel):
    action: Literal["get_schedule_history"] = "get_schedule_history"
    schedule_id: str
    last_n_runs: int = Field(default=20, ge=1, le=1000)


class GetScheduleHistory(MockTool):
    name = "get_schedule_history"
    domain = DOMAIN; action = "get_schedule_history"
    description = "Retrieve the recent execution history (success/failure) of a schedule."
    args_model = GetScheduleHistoryArgs
    examples = ["查看定时任务的执行历史", "show the last 10 runs of this schedule", "看看这个任务跑得成不成功"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]

    def run(self, args: GetScheduleHistoryArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "runs": [], "count": 0})


class CloneScheduleArgs(BaseModel):
    action: Literal["clone_schedule"] = "clone_schedule"
    source_schedule_id: str
    new_schedule_id: str


class CloneSchedule(MockTool):
    name = "clone_schedule"
    domain = DOMAIN; action = "clone_schedule"
    description = "Duplicate a schedule (triggers + actions) under a new id."
    args_model = CloneScheduleArgs
    examples = ["复制一个定时任务", "clone this schedule for the other line", "基于现有计划新建一个"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.new_schedule_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.source_schedule_id}"]

    def run(self, args: CloneScheduleArgs, world: object) -> ToolResult:
        return ok(data={"new_schedule_id": args.new_schedule_id})


class SetScheduleRetryPolicyArgs(BaseModel):
    action: Literal["set_schedule_retry_policy"] = "set_schedule_retry_policy"
    schedule_id: str
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff: Literal["fixed", "exponential"] = "exponential"


class SetScheduleRetryPolicy(MockTool):
    name = "set_schedule_retry_policy"
    domain = DOMAIN; action = "set_schedule_retry_policy"
    description = "Configure how a failed scheduled job is retried."
    args_model = SetScheduleRetryPolicyArgs
    examples = ["设置定时任务失败重试策略", "retry this job 3 times with backoff", "任务失败了自动重试"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}.retry_policy"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}"]

    def run(self, args: SetScheduleRetryPolicyArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "max_retries": args.max_retries})


class SetScheduleDependencyArgs(BaseModel):
    action: Literal["set_schedule_dependency"] = "set_schedule_dependency"
    schedule_id: str
    depends_on_schedule_id: str


class SetScheduleDependency(MockTool):
    name = "set_schedule_dependency"
    domain = DOMAIN; action = "set_schedule_dependency"
    description = "Make a schedule run only after another schedule completes successfully."
    args_model = SetScheduleDependencyArgs
    examples = ["设置任务之间的依赖", "run this job only after the ETL job finishes", "让报表任务等备份任务先跑完"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}.depends_on"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"schedules.{args.schedule_id}", f"schedules.{args.depends_on_schedule_id}"]

    def run(self, args: SetScheduleDependencyArgs, world: object) -> ToolResult:
        return ok(data={"schedule_id": args.schedule_id, "depends_on": args.depends_on_schedule_id})


SCHEDULE_ACTIONS.update({
    cls.action: cls
    for cls in (
        DisableSchedule, DeleteSchedule, RunScheduleNow, PauseSchedule, ResumeSchedule,
        SetScheduleTimezone, GetScheduleHistory, CloneSchedule, SetScheduleRetryPolicy,
        SetScheduleDependency,
    )
})
