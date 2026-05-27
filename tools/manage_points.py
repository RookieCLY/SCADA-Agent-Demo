"""manage_points — point/tag CRUD."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from tools._base import ErrorCode, MockTool, ToolResult, fail, ok
from world import MockWorld, PointType
from world.models import Point

DOMAIN = "manage_points"


# ---------------------------------------------------------------- create_point
class CreatePointArgs(BaseModel):
    action: Literal["create_point"] = "create_point"
    tag: str
    type: PointType
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    description: str | None = None


class CreatePoint(MockTool):
    name = "create_point"
    domain = DOMAIN
    action = "create_point"
    description = "Define a new SCADA point (tag) with type and optional engineering units."
    args_model = CreatePointArgs
    examples = [
        "新建一个温度点位 TEMP_201",
        "create analog point for pressure",
        "增加一个数字量输入点位",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"points.{args.tag}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return []

    def run(self, args: CreatePointArgs, world: MockWorld) -> ToolResult:
        if args.tag in world.points:
            return fail(ErrorCode.ALREADY_EXISTS, f"point {args.tag} already exists")
        point = Point(
            tag=args.tag,
            type=args.type,
            unit=args.unit,
            min=args.min,
            max=args.max,
            description=args.description,
        )
        world.points[args.tag] = point
        return ok(
            data={"tag": args.tag},
            world_diff={"added_or_modified": {f"points.{args.tag}": point.model_dump()}, "removed": []},
        )


# ---------------------------------------------------------------- update_point
class UpdatePointArgs(BaseModel):
    action: Literal["update_point"] = "update_point"
    tag: str
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    description: str | None = None


class UpdatePoint(MockTool):
    name = "update_point"
    domain = DOMAIN
    action = "update_point"
    description = "Update engineering metadata (unit, min, max, description) on an existing point."
    args_model = UpdatePointArgs
    examples = ["把温度点的量程改成 0~200", "set unit to MPa"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"points.{args.tag}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"points.{args.tag}"]

    def run(self, args: UpdatePointArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        p = world.points[args.tag]
        if args.unit is not None:
            p.unit = args.unit
        if args.min is not None:
            p.min = args.min
        if args.max is not None:
            p.max = args.max
        if args.description is not None:
            p.description = args.description
        return ok(
            data={"tag": args.tag},
            world_diff={"added_or_modified": {f"points.{args.tag}": p.model_dump()}, "removed": []},
        )


# ---------------------------------------------------------------- delete_point
class DeletePointArgs(BaseModel):
    action: Literal["delete_point"] = "delete_point"
    tag: str


class DeletePoint(MockTool):
    name = "delete_point"
    domain = DOMAIN
    action = "delete_point"
    description = "Delete a SCADA point. Fails if any alarm references it."
    args_model = DeletePointArgs
    examples = ["删除点位"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"points.{args.tag}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"points.{args.tag}"]

    def run(self, args: DeletePointArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        # business rule: cannot delete a point that has an alarm bound to it
        users = [a.id for a in world.alarms.values() if a.tag == args.tag]
        if users:
            return fail(
                ErrorCode.BUSINESS_RULE,
                f"point {args.tag} is referenced by alarms {users}",
            )
        del world.points[args.tag]
        return ok(
            data={"tag": args.tag},
            world_diff={"added_or_modified": {}, "removed": [f"points.{args.tag}"]},
        )


# ---------------------------------------------------------------- list (read-only)
class ListPointsArgs(BaseModel):
    action: Literal["list_points"] = "list_points"
    type_filter: PointType | None = None


class ListPoints(MockTool):
    name = "list_points"
    domain = DOMAIN
    action = "list_points"
    description = "Return a summary of all points; optional filter by type."
    args_model = ListPointsArgs
    examples = ["列出所有点位", "show me the analog points"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return []

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return []

    def run(self, args: ListPointsArgs, world: MockWorld) -> ToolResult:
        items = [
            p.model_dump()
            for p in world.points.values()
            if args.type_filter is None or p.type == args.type_filter
        ]
        return ok(data={"count": len(items), "points": items})


# ---------------------------------------------------------------- registry hookup
POINT_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls for cls in (CreatePoint, UpdatePoint, DeletePoint, ListPoints)
}

ManagePointsArgs = Annotated[
    Union[CreatePointArgs, UpdatePointArgs, DeletePointArgs, ListPointsArgs],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN",
    "POINT_ACTIONS",
    "ManagePointsArgs",
    "CreatePoint",
    "CreatePointArgs",
    "UpdatePoint",
    "UpdatePointArgs",
    "DeletePoint",
    "DeletePointArgs",
    "ListPoints",
    "ListPointsArgs",
]
