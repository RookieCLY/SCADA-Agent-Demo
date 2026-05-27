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
