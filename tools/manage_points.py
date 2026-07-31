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


# ============================================================ extension tools
def _pt_diff(tag, point):
    return {"added_or_modified": {f"points.{tag}": point.model_dump()}, "removed": []}


class SetPointUnitArgs(BaseModel):
    action: Literal["set_point_unit"] = "set_point_unit"
    tag: str
    unit: str


class SetPointUnit(MockTool):
    name = "set_point_unit"
    domain = DOMAIN; action = "set_point_unit"
    description = "Set the engineering unit of a point (e.g. °C, MPa, m³/h)."
    args_model = SetPointUnitArgs
    examples = ["把这个点位的单位设成摄氏度", "set the unit of TEMP_101 to degC", "改一下点位的工程单位"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: SetPointUnitArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        p = world.points[args.tag]; p.unit = args.unit
        return ok(data={"tag": args.tag, "unit": args.unit}, world_diff=_pt_diff(args.tag, p))


class SetPointRangeArgs(BaseModel):
    action: Literal["set_point_range"] = "set_point_range"
    tag: str
    min: float
    max: float


class SetPointRange(MockTool):
    name = "set_point_range"
    domain = DOMAIN; action = "set_point_range"
    description = "Set the engineering min/max range (span) of a point."
    args_model = SetPointRangeArgs
    examples = ["把量程设为 0 到 200", "set the range of PRESS_101 to 0-1.6 MPa", "调整点位的上下量程"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: SetPointRangeArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        if args.min >= args.max:
            return fail(ErrorCode.BUSINESS_RULE, "min must be < max")
        p = world.points[args.tag]; p.min = args.min; p.max = args.max
        return ok(data={"tag": args.tag}, world_diff=_pt_diff(args.tag, p))


class SetPointDescriptionArgs(BaseModel):
    action: Literal["set_point_description"] = "set_point_description"
    tag: str
    description: str


class SetPointDescription(MockTool):
    name = "set_point_description"
    domain = DOMAIN; action = "set_point_description"
    description = "Set the human-readable description of a point."
    args_model = SetPointDescriptionArgs
    examples = ["给这个点位加个描述", "set the description of TEMP_101", "补充点位的说明文字"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: SetPointDescriptionArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        p = world.points[args.tag]; p.description = args.description
        return ok(data={"tag": args.tag}, world_diff=_pt_diff(args.tag, p))


class RenamePointArgs(BaseModel):
    action: Literal["rename_point"] = "rename_point"
    tag: str
    new_tag: str


class RenamePoint(MockTool):
    name = "rename_point"
    domain = DOMAIN; action = "rename_point"
    description = "Rename a point's tag. Fails if an alarm still references the old tag."
    args_model = RenamePointArgs
    examples = ["把点位重命名", "rename TEMP_101 to REACTOR1_TEMP", "改一下点位的标签名"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.new_tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: RenamePointArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        if args.new_tag in world.points:
            return fail(ErrorCode.ALREADY_EXISTS, f"point {args.new_tag} already exists")
        bound = [a.id for a in world.alarms.values() if a.tag == args.tag]
        if bound:
            return fail(ErrorCode.BUSINESS_RULE, f"point {args.tag} is referenced by alarms {bound}")
        p = world.points.pop(args.tag); p.tag = args.new_tag; world.points[args.new_tag] = p
        return ok(
            data={"tag": args.new_tag},
            world_diff={"added_or_modified": {f"points.{args.new_tag}": p.model_dump()}, "removed": [f"points.{args.tag}"]},
        )


class SetPointTypeArgs(BaseModel):
    action: Literal["set_point_type"] = "set_point_type"
    tag: str
    type: Literal["analog", "digital", "string"]


class SetPointType(MockTool):
    name = "set_point_type"
    domain = DOMAIN; action = "set_point_type"
    description = "Change the data type of a point (analog / digital / string)."
    args_model = SetPointTypeArgs
    examples = ["把点位类型改成数字量", "make this point analog", "调整点位的数据类型"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: SetPointTypeArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        p = world.points[args.tag]; p.type = args.type
        return ok(data={"tag": args.tag, "type": args.type}, world_diff=_pt_diff(args.tag, p))


class CopyPointArgs(BaseModel):
    action: Literal["copy_point"] = "copy_point"
    tag: str
    new_tag: str


class CopyPoint(MockTool):
    name = "copy_point"
    domain = DOMAIN; action = "copy_point"
    description = "Create a new point by copying an existing point's metadata."
    args_model = CopyPointArgs
    examples = ["照着这个点位复制一个", "duplicate TEMP_101 as TEMP_102", "基于现有点位新建一个"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.new_tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: CopyPointArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        if args.new_tag in world.points:
            return fail(ErrorCode.ALREADY_EXISTS, f"point {args.new_tag} already exists")
        src = world.points[args.tag]
        new = src.model_copy(update={"tag": args.new_tag})
        world.points[args.new_tag] = new
        return ok(data={"tag": args.new_tag}, world_diff=_pt_diff(args.new_tag, new))


class BatchCreatePointsArgs(BaseModel):
    action: Literal["batch_create_points"] = "batch_create_points"
    prefix: str
    count: int = Field(ge=1, le=1000)
    type: Literal["analog", "digital", "string"] = "analog"


class BatchCreatePoints(MockTool):
    name = "batch_create_points"
    domain = DOMAIN; action = "batch_create_points"
    description = "Create many points at once with a numbered prefix (e.g. TEMP_1..TEMP_50)."
    args_model = BatchCreatePointsArgs
    examples = ["批量创建 50 个温度点位", "bulk create TEMP_1 through TEMP_20", "一次性生成一批点位"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.prefix}_{i + 1}" for i in range(args.count)]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: BatchCreatePointsArgs, world: MockWorld) -> ToolResult:
        from world.models import Point
        added = {}
        for i in range(args.count):
            tag = f"{args.prefix}_{i + 1}"
            if tag in world.points:
                continue
            world.points[tag] = Point(tag=tag, type=args.type)
            added[f"points.{tag}"] = world.points[tag].model_dump()
        return ok(data={"created": len(added)}, world_diff={"added_or_modified": added, "removed": []})


class BatchDeletePointsArgs(BaseModel):
    action: Literal["batch_delete_points"] = "batch_delete_points"
    tags: list[str] = Field(min_length=1)


class BatchDeletePoints(MockTool):
    name = "batch_delete_points"
    domain = DOMAIN; action = "batch_delete_points"
    description = "Delete multiple points at once (skips points referenced by alarms)."
    args_model = BatchDeletePointsArgs
    examples = ["批量删除这些点位", "delete all the test tags", "一次性删掉一组点位"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{t}" for t in args.tags]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{t}" for t in args.tags]

    def run(self, args: BatchDeletePointsArgs, world: MockWorld) -> ToolResult:
        removed = []
        for t in args.tags:
            if t in world.points and not any(a.tag == t for a in world.alarms.values()):
                del world.points[t]; removed.append(f"points.{t}")
        return ok(data={"deleted": len(removed)}, world_diff={"added_or_modified": {}, "removed": removed})


class SetPointSimulationArgs(BaseModel):
    action: Literal["set_point_simulation"] = "set_point_simulation"
    tag: str
    mode: Literal["off", "ramp", "sine", "random"] = "off"


class SetPointSimulation(MockTool):
    name = "set_point_simulation"
    domain = DOMAIN; action = "set_point_simulation"
    description = "Configure a simulated value generator for a point (offline testing)."
    args_model = SetPointSimulationArgs
    examples = ["给点位设置一个模拟信号", "make this tag ramp for testing", "让点位仿真一个正弦波"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}.simulation"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: SetPointSimulationArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        return ok(data={"tag": args.tag, "mode": args.mode})


class SetPointInitialValueArgs(BaseModel):
    action: Literal["set_point_initial_value"] = "set_point_initial_value"
    tag: str
    value: float


class SetPointInitialValue(MockTool):
    name = "set_point_initial_value"
    domain = DOMAIN; action = "set_point_initial_value"
    description = "Set the power-up initial value of a point."
    args_model = SetPointInitialValueArgs
    examples = ["设置点位的初始值", "default this tag to 0 on startup", "配置点位上电初值"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}.initial_value"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: SetPointInitialValueArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        return ok(data={"tag": args.tag, "initial_value": args.value})


class SetPointScalingArgs(BaseModel):
    action: Literal["set_point_scaling"] = "set_point_scaling"
    tag: str
    raw_low: float
    raw_high: float
    eng_low: float
    eng_high: float


class SetPointScaling(MockTool):
    name = "set_point_scaling"
    domain = DOMAIN; action = "set_point_scaling"
    description = "Configure raw-to-engineering linear scaling for a point."
    args_model = SetPointScalingArgs
    examples = ["设置点位的量程转换", "scale 4-20mA to 0-100%", "配置原始值到工程值的换算"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}.scaling"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: SetPointScalingArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        return ok(data={"tag": args.tag, "scaled": True})


class MovePointToGroupArgs(BaseModel):
    action: Literal["move_point_to_group"] = "move_point_to_group"
    tag: str
    group: str


class MovePointToGroup(MockTool):
    name = "move_point_to_group"
    domain = DOMAIN; action = "move_point_to_group"
    description = "Assign a point to a logical tag group / folder."
    args_model = MovePointToGroupArgs
    examples = ["把点位归到某个分组", "move this tag into the Reactor1 group", "整理点位分组"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}.group"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: MovePointToGroupArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        return ok(data={"tag": args.tag, "group": args.group})


class GetPointValueArgs(BaseModel):
    action: Literal["get_point_value"] = "get_point_value"
    tag: str


class GetPointValue(MockTool):
    name = "get_point_value"
    domain = DOMAIN; action = "get_point_value"
    description = "Read the current value and quality of a point."
    args_model = GetPointValueArgs
    examples = ["读一下这个点位的当前值", "what's the current value of TEMP_101", "查询点位实时值"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: GetPointValueArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        return ok(data={"tag": args.tag, "value": None, "quality": "good"})


class SetPointArchiveArgs(BaseModel):
    action: Literal["set_point_archive"] = "set_point_archive"
    tag: str
    archive: bool = True


class SetPointArchive(MockTool):
    name = "set_point_archive"
    domain = DOMAIN; action = "set_point_archive"
    # Says what it is *not*, because it was measurably mistaken for the tool that
    # is. This flips a per-point flag; it neither creates nor configures a
    # historian entry, and a point with no history config still has none
    # afterwards. Its old description ("archived to history") and its old
    # examples ("让这个点位存历史" / "开启点位的历史归档") restated
    # ``enable_history``'s job almost word for word, so Tool RAG ranked this
    # above the real tool on history queries: golden-093 asked to keep values for
    # history query and got ``set_point_archive``, which succeeded, wrote nothing,
    # and left ``histories.ENERGY_KWH`` absent with no error in the trace.
    description = (
        "Toggle a point's archive flag. Does NOT create or configure historian "
        "sampling — use enable_history to start recording a tag's history."
    )
    args_model = SetPointArchiveArgs
    examples = ["切换点位的归档标记", "stop archiving this tag", "取消这个点的归档标志"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        # ``Point`` has no ``archive`` field and ``run`` writes nothing, so this
        # named an entity that can never exist. Claiming an intent the tool does
        # not fulfil is exactly what the cascade detector reads, so it must not
        # over-declare.
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{args.tag}"]

    def run(self, args: SetPointArchiveArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        return ok(data={"tag": args.tag, "archive": args.archive})


class ExportPointsArgs(BaseModel):
    action: Literal["export_points"] = "export_points"
    format: Literal["csv", "excel"] = "csv"


class ExportPoints(MockTool):
    name = "export_points"
    domain = DOMAIN; action = "export_points"
    description = "Export the full point/tag list to a file."
    args_model = ExportPointsArgs
    examples = ["导出点位表", "export all tags to CSV", "把点位清单导出来"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ExportPointsArgs, world: MockWorld) -> ToolResult:
        return ok(data={"format": args.format, "count": len(world.points)})


POINT_ACTIONS.update({
    cls.action: cls
    for cls in (
        SetPointUnit, SetPointRange, SetPointDescription, RenamePoint, SetPointType,
        CopyPoint, BatchCreatePoints, BatchDeletePoints, SetPointSimulation,
        SetPointInitialValue, SetPointScaling, MovePointToGroup, GetPointValue,
        SetPointArchive, ExportPoints,
    )
})
