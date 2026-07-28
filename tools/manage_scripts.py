"""manage_scripts — user script CRUD.

The demo never executes the script body. The world only records that a script
exists with a given trigger, source, and binding; downstream Resources and the
deployment validator can then assert e.g. "every on_change script must be bound
to an existing tag".
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

from tools._base import ErrorCode, MockTool, ToolResult, fail, ok
from world import MockWorld, ScriptTrigger
from world.models import Script

DOMAIN = "manage_scripts"


# ---------------------------------------------------------------- create_script
class CreateScriptArgs(BaseModel):
    action: Literal["create_script"] = "create_script"
    id: str
    name: str
    trigger: ScriptTrigger
    bound_tag: str | None = None
    period_s: float | None = Field(default=None, gt=0)
    body: str = ""

    @model_validator(mode="after")
    def _trigger_consistency(self):
        if self.trigger in ("on_change", "on_alarm") and self.bound_tag is None:
            raise ValueError(f"trigger {self.trigger!r} requires `bound_tag`")
        if self.trigger == "periodic" and self.period_s is None:
            raise ValueError("trigger 'periodic' requires `period_s`")
        return self


class CreateScript(MockTool):
    name = "create_script"
    domain = DOMAIN
    action = "create_script"
    description = "Define a user script (on_change / on_alarm / periodic / on_event)."
    args_model = CreateScriptArgs
    examples = [
        "添加一个温度超限的脚本",
        "新建一个周期 1 秒的脚本",
        "create an on_change script bound to TEMP_101",
        "alarm 触发时跑一段脚本",
        "增加一段事件脚本",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"scripts.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"points.{args.bound_tag}"] if args.bound_tag else []

    def run(self, args: CreateScriptArgs, world: MockWorld) -> ToolResult:
        if args.id in world.scripts:
            return fail(ErrorCode.ALREADY_EXISTS, f"script {args.id} already exists")
        if args.bound_tag is not None and args.bound_tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"bound_tag {args.bound_tag} not found")
        if args.trigger == "on_alarm" and args.bound_tag and args.bound_tag not in {
            a.tag for a in world.alarms.values()
        }:
            return fail(
                ErrorCode.BUSINESS_RULE,
                f"on_alarm script requires an existing alarm on tag {args.bound_tag}",
            )
        script = Script(
            id=args.id,
            name=args.name,
            trigger=args.trigger,
            bound_tag=args.bound_tag,
            period_s=args.period_s,
            body=args.body,
        )
        world.scripts[args.id] = script
        return ok(
            data={"script_id": args.id},
            world_diff={"added_or_modified": {f"scripts.{args.id}": script.model_dump()}, "removed": []},
        )


# ---------------------------------------------------------------- update_script_body
class UpdateScriptBodyArgs(BaseModel):
    action: Literal["update_script_body"] = "update_script_body"
    id: str
    body: str


class UpdateScriptBody(MockTool):
    name = "update_script_body"
    domain = DOMAIN
    action = "update_script_body"
    description = "Replace the source body of an existing script."
    args_model = UpdateScriptBodyArgs
    examples = [
        "改一下脚本内容",
        "edit script body",
        "把那段脚本改写一下",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"scripts.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"scripts.{args.id}"]

    def run(self, args: UpdateScriptBodyArgs, world: MockWorld) -> ToolResult:
        if args.id not in world.scripts:
            return fail(ErrorCode.BUSINESS_RULE, f"script {args.id} not found")
        world.scripts[args.id].body = args.body
        return ok(
            data={"script_id": args.id, "body_len": len(args.body)},
            world_diff={"added_or_modified": {f"scripts.{args.id}.body": args.body}, "removed": []},
        )


# ---------------------------------------------------------------- enable / disable
class EnableScriptArgs(BaseModel):
    action: Literal["enable_script"] = "enable_script"
    id: str


class EnableScript(MockTool):
    name = "enable_script"
    domain = DOMAIN
    action = "enable_script"
    description = "Enable a disabled script."
    args_model = EnableScriptArgs
    examples = ["启用脚本", "enable this script"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"scripts.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"scripts.{args.id}"]

    def run(self, args: EnableScriptArgs, world: MockWorld) -> ToolResult:
        if args.id not in world.scripts:
            return fail(ErrorCode.BUSINESS_RULE, f"script {args.id} not found")
        world.scripts[args.id].enabled = True
        return ok(
            data={"script_id": args.id, "enabled": True},
            world_diff={"added_or_modified": {f"scripts.{args.id}.enabled": True}, "removed": []},
        )


class DisableScriptArgs(BaseModel):
    action: Literal["disable_script"] = "disable_script"
    id: str


class DisableScript(MockTool):
    name = "disable_script"
    domain = DOMAIN
    action = "disable_script"
    description = "Disable a script without deleting it."
    args_model = DisableScriptArgs
    examples = ["停用脚本", "disable this script", "暂时关掉脚本"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"scripts.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"scripts.{args.id}"]

    def run(self, args: DisableScriptArgs, world: MockWorld) -> ToolResult:
        if args.id not in world.scripts:
            return fail(ErrorCode.BUSINESS_RULE, f"script {args.id} not found")
        world.scripts[args.id].enabled = False
        return ok(
            data={"script_id": args.id, "enabled": False},
            world_diff={"added_or_modified": {f"scripts.{args.id}.enabled": False}, "removed": []},
        )


# ---------------------------------------------------------------- delete_script
class DeleteScriptArgs(BaseModel):
    action: Literal["delete_script"] = "delete_script"
    id: str


class DeleteScript(MockTool):
    name = "delete_script"
    domain = DOMAIN
    action = "delete_script"
    description = "Remove a script definition entirely."
    args_model = DeleteScriptArgs
    examples = ["删除脚本", "remove this script", "把这段脚本去掉"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"scripts.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"scripts.{args.id}"]

    def run(self, args: DeleteScriptArgs, world: MockWorld) -> ToolResult:
        if args.id not in world.scripts:
            return fail(ErrorCode.BUSINESS_RULE, f"script {args.id} not found")
        del world.scripts[args.id]
        return ok(
            data={"script_id": args.id},
            world_diff={"added_or_modified": {}, "removed": [f"scripts.{args.id}"]},
        )


# ---------------------------------------------------------------- list_scripts
class ListScriptsArgs(BaseModel):
    action: Literal["list_scripts"] = "list_scripts"
    trigger: ScriptTrigger | None = None


class ListScripts(MockTool):
    name = "list_scripts"
    domain = DOMAIN
    action = "list_scripts"
    description = "List defined scripts, optionally filtered by trigger."
    args_model = ListScriptsArgs
    examples = [
        "列出所有脚本",
        "show all on_change scripts",
        "查询脚本配置",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return []

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return []

    def run(self, args: ListScriptsArgs, world: MockWorld) -> ToolResult:
        items = [
            s.model_dump()
            for s in world.scripts.values()
            if args.trigger is None or s.trigger == args.trigger
        ]
        return ok(data={"count": len(items), "scripts": items})


# ---------------------------------------------------------------- registry hookup
SCRIPT_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (CreateScript, UpdateScriptBody, EnableScript, DisableScript, DeleteScript, ListScripts)
}

ManageScriptsArgs = Annotated[
    Union[
        CreateScriptArgs,
        UpdateScriptBodyArgs,
        EnableScriptArgs,
        DisableScriptArgs,
        DeleteScriptArgs,
        ListScriptsArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN",
    "SCRIPT_ACTIONS",
    "ManageScriptsArgs",
    "CreateScript",
    "CreateScriptArgs",
    "DeleteScript",
    "DeleteScriptArgs",
    "DisableScript",
    "DisableScriptArgs",
    "EnableScript",
    "EnableScriptArgs",
    "ListScripts",
    "ListScriptsArgs",
    "UpdateScriptBody",
    "UpdateScriptBodyArgs",
]


# ============================================================ extension tools
def _need_script(world, script_id):
    if script_id not in world.scripts:
        return fail(ErrorCode.BUSINESS_RULE, f"script {script_id} not found")
    return None


def _script_diff(script_id, s):
    return {"added_or_modified": {f"scripts.{script_id}": s.model_dump()}, "removed": []}


class RenameScriptArgs(BaseModel):
    action: Literal["rename_script"] = "rename_script"
    script_id: str
    new_name: str


class RenameScript(MockTool):
    name = "rename_script"
    domain = DOMAIN; action = "rename_script"
    description = "Rename a user script's display name."
    args_model = RenameScriptArgs
    examples = ["给脚本改个名字", "rename this script to 'pump_interlock'", "修改脚本名称"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]

    def run(self, args: RenameScriptArgs, world: MockWorld) -> ToolResult:
        err = _need_script(world, args.script_id)
        if err: return err
        s = world.scripts[args.script_id]; s.name = args.new_name
        return ok(data={"script_id": args.script_id}, world_diff=_script_diff(args.script_id, s))


class SetScriptTriggerArgs(BaseModel):
    action: Literal["set_script_trigger"] = "set_script_trigger"
    script_id: str
    trigger: Literal["on_change", "on_alarm", "periodic", "on_event"]


class SetScriptTrigger(MockTool):
    name = "set_script_trigger"
    domain = DOMAIN; action = "set_script_trigger"
    description = "Set what triggers a script to run (on_change / on_alarm / periodic / on_event)."
    args_model = SetScriptTriggerArgs
    examples = ["设置脚本的触发方式", "run this script on every value change", "让脚本定周期执行"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}.trigger"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]

    def run(self, args: SetScriptTriggerArgs, world: MockWorld) -> ToolResult:
        err = _need_script(world, args.script_id)
        if err: return err
        return ok(data={"script_id": args.script_id, "trigger": args.trigger})


class SetScriptPeriodArgs(BaseModel):
    action: Literal["set_script_period"] = "set_script_period"
    script_id: str
    period_s: float = Field(gt=0, le=86400)


class SetScriptPeriod(MockTool):
    name = "set_script_period"
    domain = DOMAIN; action = "set_script_period"
    description = "Set the execution period (seconds) of a periodic script."
    args_model = SetScriptPeriodArgs
    examples = ["设置脚本的执行周期", "run this script every 10 seconds", "调整脚本的运行间隔"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]

    def run(self, args: SetScriptPeriodArgs, world: MockWorld) -> ToolResult:
        err = _need_script(world, args.script_id)
        if err: return err
        s = world.scripts[args.script_id]; s.period_s = args.period_s
        return ok(data={"script_id": args.script_id}, world_diff=_script_diff(args.script_id, s))


class BindScriptToTagArgs(BaseModel):
    action: Literal["bind_script_to_tag"] = "bind_script_to_tag"
    script_id: str
    tag: str


class BindScriptToTag(MockTool):
    name = "bind_script_to_tag"
    domain = DOMAIN; action = "bind_script_to_tag"
    description = "Bind a script to a point so it triggers on that tag."
    args_model = BindScriptToTagArgs
    examples = ["把脚本绑定到某个点位", "trigger this script when TEMP_101 changes", "让脚本挂到这个标签上"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}", f"points.{args.tag}"]

    def run(self, args: BindScriptToTagArgs, world: MockWorld) -> ToolResult:
        err = _need_script(world, args.script_id)
        if err: return err
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        s = world.scripts[args.script_id]; s.bound_tag = args.tag
        return ok(data={"script_id": args.script_id, "tag": args.tag}, world_diff=_script_diff(args.script_id, s))


class CloneScriptArgs(BaseModel):
    action: Literal["clone_script"] = "clone_script"
    script_id: str
    new_script_id: str


class CloneScript(MockTool):
    name = "clone_script"
    domain = DOMAIN; action = "clone_script"
    description = "Duplicate a script under a new id."
    args_model = CloneScriptArgs
    examples = ["复制一个脚本", "clone this interlock script", "照着现有脚本新建一个"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.new_script_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]

    def run(self, args: CloneScriptArgs, world: MockWorld) -> ToolResult:
        err = _need_script(world, args.script_id)
        if err: return err
        if args.new_script_id in world.scripts:
            return fail(ErrorCode.ALREADY_EXISTS, f"script {args.new_script_id} already exists")
        new = world.scripts[args.script_id].model_copy(update={"id": args.new_script_id})
        world.scripts[args.new_script_id] = new
        return ok(data={"script_id": args.new_script_id}, world_diff=_script_diff(args.new_script_id, new))


class TestScriptArgs(BaseModel):
    action: Literal["test_script"] = "test_script"
    script_id: str


class TestScript(MockTool):
    name = "test_script"
    domain = DOMAIN; action = "test_script"
    description = "Run a script once in a sandbox to verify it executes without error."
    args_model = TestScriptArgs
    examples = ["测试运行一下这个脚本", "test-run this script", "验证脚本能不能跑通"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]

    def run(self, args: TestScriptArgs, world: MockWorld) -> ToolResult:
        err = _need_script(world, args.script_id)
        if err: return err
        return ok(data={"script_id": args.script_id, "passed": True})


class DebugScriptArgs(BaseModel):
    action: Literal["debug_script"] = "debug_script"
    script_id: str
    breakpoint_line: int | None = None


class DebugScript(MockTool):
    name = "debug_script"
    domain = DOMAIN; action = "debug_script"
    description = "Start a debug session for a script with an optional breakpoint."
    args_model = DebugScriptArgs
    examples = ["调试这个脚本", "debug this script at line 12", "给脚本打个断点排查"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]

    def run(self, args: DebugScriptArgs, world: MockWorld) -> ToolResult:
        err = _need_script(world, args.script_id)
        if err: return err
        return ok(data={"script_id": args.script_id, "debugging": True})


class GetScriptLogsArgs(BaseModel):
    action: Literal["get_script_logs"] = "get_script_logs"
    script_id: str
    last_n_lines: int = Field(default=100, ge=1, le=10000)


class GetScriptLogs(MockTool):
    name = "get_script_logs"
    domain = DOMAIN; action = "get_script_logs"
    description = "Retrieve recent execution log output of a script."
    args_model = GetScriptLogsArgs
    examples = ["查看脚本的运行日志", "show this script's recent logs", "看看脚本报了什么错"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]

    def run(self, args: GetScriptLogsArgs, world: MockWorld) -> ToolResult:
        return ok(data={"script_id": args.script_id, "lines": []})


class SetScriptPriorityArgs(BaseModel):
    action: Literal["set_script_priority"] = "set_script_priority"
    script_id: str
    priority: Literal["low", "normal", "high"] = "normal"


class SetScriptPriority(MockTool):
    name = "set_script_priority"
    domain = DOMAIN; action = "set_script_priority"
    description = "Set the scheduler priority of a script."
    args_model = SetScriptPriorityArgs
    examples = ["设置脚本的优先级", "run this interlock script at high priority", "调整脚本执行优先级"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}.priority"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]

    def run(self, args: SetScriptPriorityArgs, world: MockWorld) -> ToolResult:
        err = _need_script(world, args.script_id)
        if err: return err
        return ok(data={"script_id": args.script_id, "priority": args.priority})


class ValidateScriptSyntaxArgs(BaseModel):
    action: Literal["validate_script_syntax"] = "validate_script_syntax"
    script_id: str


class ValidateScriptSyntax(MockTool):
    name = "validate_script_syntax"
    domain = DOMAIN; action = "validate_script_syntax"
    description = "Statically check a script's syntax without executing it."
    args_model = ValidateScriptSyntaxArgs
    examples = ["检查脚本语法有没有错", "validate this script's syntax", "静态检查脚本"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]

    def run(self, args: ValidateScriptSyntaxArgs, world: MockWorld) -> ToolResult:
        err = _need_script(world, args.script_id)
        if err: return err
        return ok(data={"script_id": args.script_id, "valid": True})


class ImportScriptArgs(BaseModel):
    action: Literal["import_script"] = "import_script"
    source_file: str
    new_script_id: str


class ImportScript(MockTool):
    name = "import_script"
    domain = DOMAIN; action = "import_script"
    description = "Import a script from a file into the script library."
    args_model = ImportScriptArgs
    examples = ["从文件导入脚本", "import this script from a file", "把脚本文件导进来"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.new_script_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ImportScriptArgs, world: MockWorld) -> ToolResult:
        return ok(data={"new_script_id": args.new_script_id, "imported": True})


class ExportScriptArgs(BaseModel):
    action: Literal["export_script"] = "export_script"
    script_id: str


class ExportScript(MockTool):
    name = "export_script"
    domain = DOMAIN; action = "export_script"
    description = "Export a script's source to a file."
    args_model = ExportScriptArgs
    examples = ["导出这个脚本", "export the script source", "把脚本导出成文件"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"scripts.{args.script_id}"]

    def run(self, args: ExportScriptArgs, world: MockWorld) -> ToolResult:
        err = _need_script(world, args.script_id)
        if err: return err
        return ok(data={"script_id": args.script_id, "exported": True})


SCRIPT_ACTIONS.update({
    cls.action: cls
    for cls in (
        RenameScript, SetScriptTrigger, SetScriptPeriod, BindScriptToTag, CloneScript,
        TestScript, DebugScript, GetScriptLogs, SetScriptPriority, ValidateScriptSyntax,
        ImportScript, ExportScript,
    )
})
