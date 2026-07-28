"""manage_graphics — primitive/layout-level graphics operations.

This domain complements ``manage_pages`` (which already owns page CRUD,
single-widget placement and point→widget binding). ``manage_graphics`` is the
home of the *graphical primitive* operations: drawing rectangles/circles/lines
on a page, applying flow / grid layouts, grouping & ungrouping widgets, and
adjusting style.

Splitting these out keeps the per-domain action count balanced (≤ 8 actions
each) and gives §3.3.1's `(domain, action)` reverse-table cleaner buckets.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator

from tools._base import ErrorCode, MockTool, ToolResult, fail, ok
from world import MockWorld
from world.models import Widget

DOMAIN = "manage_graphics"


# ---------------------------------------------------------------- create_rect
class CreateRectArgs(BaseModel):
    action: Literal["create_rect"] = "create_rect"
    page_id: str
    widget_id: str
    position: tuple[int, int]
    size: tuple[int, int]
    style: dict[str, Any] = Field(default_factory=dict)


def _check_page_then_unique_widget(
    world: MockWorld, page_id: str, widget_id: str
) -> ToolResult | None:
    if page_id not in world.pages:
        return fail(ErrorCode.PAGE_NOT_FOUND, f"page {page_id} not found")
    if widget_id in world.pages[page_id].widgets:
        return fail(ErrorCode.ALREADY_EXISTS, f"widget {widget_id} already on page")
    return None


def _place_widget(world: MockWorld, page_id: str, widget: Widget) -> ToolResult:
    world.pages[page_id].widgets[widget.id] = widget
    key = f"pages.{page_id}.widgets.{widget.id}"
    return ok(
        data={"widget_id": widget.id, "page_id": page_id, "type": widget.type},
        world_diff={"added_or_modified": {key: widget.model_dump()}, "removed": []},
    )


class CreateRect(MockTool):
    name = "create_rect"
    domain = DOMAIN
    action = "create_rect"
    description = "Draw a rectangle primitive on a page."
    args_model = CreateRectArgs
    examples = [
        "在画面上画一个矩形",
        "draw a rectangle on page",
        "添加一个矩形框",
        "画一个红色边框框",
        "create a rect primitive",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}"]

    def run(self, args: CreateRectArgs, world: MockWorld) -> ToolResult:
        pre = _check_page_then_unique_widget(world, args.page_id, args.widget_id)
        if pre is not None:
            return pre
        widget = Widget(
            id=args.widget_id,
            page_id=args.page_id,
            type="rect",
            position=args.position,
            size=args.size,
            style=args.style,
        )
        return _place_widget(world, args.page_id, widget)


# ---------------------------------------------------------------- create_circle
class CreateCircleArgs(BaseModel):
    action: Literal["create_circle"] = "create_circle"
    page_id: str
    widget_id: str
    center: tuple[int, int]
    radius: int = Field(gt=0)
    style: dict[str, Any] = Field(default_factory=dict)


class CreateCircle(MockTool):
    name = "create_circle"
    domain = DOMAIN
    action = "create_circle"
    description = "Draw a circle primitive on a page."
    args_model = CreateCircleArgs
    examples = [
        "画一个圆",
        "在画面上加一个圆形",
        "draw a circle",
        "indicator light 用圆形",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}"]

    def run(self, args: CreateCircleArgs, world: MockWorld) -> ToolResult:
        pre = _check_page_then_unique_widget(world, args.page_id, args.widget_id)
        if pre is not None:
            return pre
        cx, cy = args.center
        widget = Widget(
            id=args.widget_id,
            page_id=args.page_id,
            type="circle",
            position=(cx - args.radius, cy - args.radius),
            size=(args.radius * 2, args.radius * 2),
            style=args.style,
        )
        return _place_widget(world, args.page_id, widget)


# ---------------------------------------------------------------- create_line
class CreateLineArgs(BaseModel):
    action: Literal["create_line"] = "create_line"
    page_id: str
    widget_id: str
    start: tuple[int, int]
    end: tuple[int, int]
    style: dict[str, Any] = Field(default_factory=dict)


class CreateLine(MockTool):
    name = "create_line"
    domain = DOMAIN
    action = "create_line"
    description = "Draw a line primitive (e.g. a pipe segment) on a page."
    args_model = CreateLineArgs
    examples = [
        "画一根管道",
        "在画面上画一根连线",
        "draw a pipe line",
        "用线段连接两个设备",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}"]

    def run(self, args: CreateLineArgs, world: MockWorld) -> ToolResult:
        pre = _check_page_then_unique_widget(world, args.page_id, args.widget_id)
        if pre is not None:
            return pre
        x0, y0 = args.start
        x1, y1 = args.end
        widget = Widget(
            id=args.widget_id,
            page_id=args.page_id,
            type="line",
            position=(min(x0, x1), min(y0, y1)),
            size=(abs(x1 - x0), abs(y1 - y0)),
            style={**args.style, "start": list(args.start), "end": list(args.end)},
        )
        return _place_widget(world, args.page_id, widget)


# ---------------------------------------------------------------- create_text
class CreateTextArgs(BaseModel):
    action: Literal["create_text"] = "create_text"
    page_id: str
    widget_id: str
    position: tuple[int, int]
    text: str
    font_size: int = 14
    style: dict[str, Any] = Field(default_factory=dict)


class CreateText(MockTool):
    name = "create_text"
    domain = DOMAIN
    action = "create_text"
    description = "Place a static text label on a page."
    args_model = CreateTextArgs
    examples = [
        "在画面上加一个文字标签",
        "add a title text",
        "贴一个说明文本",
        "在反应釜下面写一个标签",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}"]

    def run(self, args: CreateTextArgs, world: MockWorld) -> ToolResult:
        pre = _check_page_then_unique_widget(world, args.page_id, args.widget_id)
        if pre is not None:
            return pre
        widget = Widget(
            id=args.widget_id,
            page_id=args.page_id,
            type="text",
            position=args.position,
            size=(max(8 * len(args.text), 32), args.font_size + 4),
            style={
                **args.style,
                "text": args.text,
                "font_size": args.font_size,
            },
        )
        return _place_widget(world, args.page_id, widget)


# ---------------------------------------------------------------- apply_flow_layout
class ApplyFlowLayoutArgs(BaseModel):
    action: Literal["apply_flow_layout"] = "apply_flow_layout"
    page_id: str
    widget_ids: list[str] = Field(min_length=1)
    direction: Literal["row", "column"] = "row"
    gap: int = Field(default=20, ge=0)
    origin: tuple[int, int] = (50, 50)


class ApplyFlowLayout(MockTool):
    name = "apply_flow_layout"
    domain = DOMAIN
    action = "apply_flow_layout"
    description = "Re-position a group of widgets in a horizontal/vertical flow layout."
    args_model = ApplyFlowLayoutArgs
    examples = [
        "把这些图元水平排列",
        "纵向均匀分布几个图元",
        "arrange widgets in a row",
        "三个泵横向排开",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [
            f"pages.{args.page_id}.widgets.{wid}" for wid in args.widget_ids
        ]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}"] + [
            f"pages.{args.page_id}.widgets.{wid}" for wid in args.widget_ids
        ]

    def run(self, args: ApplyFlowLayoutArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.page_id} not found")
        page = world.pages[args.page_id]
        missing = [w for w in args.widget_ids if w not in page.widgets]
        if missing:
            return fail(
                ErrorCode.WIDGET_NOT_FOUND,
                f"widgets {missing} not on page {args.page_id}",
            )
        x, y = args.origin
        modified: dict[str, Any] = {}
        for wid in args.widget_ids:
            widget = page.widgets[wid]
            widget.position = (x, y)
            modified[f"pages.{args.page_id}.widgets.{wid}.position"] = list(widget.position)
            if args.direction == "row":
                x += widget.size[0] + args.gap
            else:
                y += widget.size[1] + args.gap
        return ok(
            data={"count": len(args.widget_ids)},
            world_diff={"added_or_modified": modified, "removed": []},
        )


# ---------------------------------------------------------------- group_widgets
class GroupWidgetsArgs(BaseModel):
    action: Literal["group_widgets"] = "group_widgets"
    page_id: str
    group_id: str
    widget_ids: list[str] = Field(min_length=2)


class GroupWidgets(MockTool):
    name = "group_widgets"
    domain = DOMAIN
    action = "group_widgets"
    description = "Group existing widgets into a named cluster (semantic, no layout change)."
    args_model = GroupWidgetsArgs
    examples = [
        "把这几个图元组合成一组",
        "group these widgets as 'pump_assembly'",
        "组合反应釜与温度计",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}.groups.{args.group_id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}"] + [
            f"pages.{args.page_id}.widgets.{wid}" for wid in args.widget_ids
        ]

    def run(self, args: GroupWidgetsArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.page_id} not found")
        page = world.pages[args.page_id]
        missing = [w for w in args.widget_ids if w not in page.widgets]
        if missing:
            return fail(
                ErrorCode.WIDGET_NOT_FOUND,
                f"widgets {missing} not on page {args.page_id}",
            )
        # store the group inside project_meta so we don't add new top-level schema
        groups = world.project_meta.setdefault("groups", {})
        groups_for_page = groups.setdefault(args.page_id, {})
        if args.group_id in groups_for_page:
            return fail(ErrorCode.ALREADY_EXISTS, f"group {args.group_id} already exists")
        groups_for_page[args.group_id] = list(args.widget_ids)
        return ok(
            data={"group_id": args.group_id, "members": list(args.widget_ids)},
            world_diff={
                "added_or_modified": {
                    f"project_meta.groups.{args.page_id}.{args.group_id}": list(args.widget_ids)
                },
                "removed": [],
            },
        )


# ---------------------------------------------------------------- set_widget_style
class SetWidgetStyleArgs(BaseModel):
    action: Literal["set_widget_style"] = "set_widget_style"
    page_id: str
    widget_id: str
    style: dict[str, Any]

    @model_validator(mode="after")
    def at_least_one(self):
        if not self.style:
            raise ValueError("style must contain at least one key")
        return self


class SetWidgetStyle(MockTool):
    name = "set_widget_style"
    domain = DOMAIN
    action = "set_widget_style"
    description = "Merge style keys (color, line_width, font_size, …) onto an existing widget."
    args_model = SetWidgetStyleArgs
    examples = [
        "把这个矩形改成红色",
        "调整图元字号",
        "set widget color to green",
        "把温度计描边加粗",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [
            f"pages.{args.page_id}",
            f"pages.{args.page_id}.widgets.{args.widget_id}",
        ]

    def run(self, args: SetWidgetStyleArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.page_id} not found")
        page = world.pages[args.page_id]
        if args.widget_id not in page.widgets:
            return fail(
                ErrorCode.WIDGET_NOT_FOUND,
                f"widget {args.widget_id} not on page {args.page_id}",
            )
        widget = page.widgets[args.widget_id]
        widget.style = {**widget.style, **args.style}
        key = f"pages.{args.page_id}.widgets.{args.widget_id}.style"
        return ok(
            data={"widget_id": args.widget_id, "style": widget.style},
            world_diff={"added_or_modified": {key: widget.style}, "removed": []},
        )


# ---------------------------------------------------------------- delete_widget
class DeleteWidgetArgs(BaseModel):
    action: Literal["delete_widget"] = "delete_widget"
    page_id: str
    widget_id: str


class DeleteWidget(MockTool):
    name = "delete_widget"
    domain = DOMAIN
    action = "delete_widget"
    description = "Remove a widget from a page."
    args_model = DeleteWidgetArgs
    examples = [
        "删掉那个矩形",
        "remove this widget",
        "把这个温度计从画面上去掉",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [
            f"pages.{args.page_id}",
            f"pages.{args.page_id}.widgets.{args.widget_id}",
        ]

    def run(self, args: DeleteWidgetArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.page_id} not found")
        page = world.pages[args.page_id]
        if args.widget_id not in page.widgets:
            return fail(
                ErrorCode.WIDGET_NOT_FOUND,
                f"widget {args.widget_id} not on page {args.page_id}",
            )
        del page.widgets[args.widget_id]
        return ok(
            data={"widget_id": args.widget_id},
            world_diff={
                "added_or_modified": {},
                "removed": [f"pages.{args.page_id}.widgets.{args.widget_id}"],
            },
        )


# ---------------------------------------------------------------- registry hookup
GRAPHICS_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (
        CreateRect,
        CreateCircle,
        CreateLine,
        CreateText,
        ApplyFlowLayout,
        GroupWidgets,
        SetWidgetStyle,
        DeleteWidget,
    )
}

ManageGraphicsArgs = Annotated[
    Union[
        CreateRectArgs,
        CreateCircleArgs,
        CreateLineArgs,
        CreateTextArgs,
        ApplyFlowLayoutArgs,
        GroupWidgetsArgs,
        SetWidgetStyleArgs,
        DeleteWidgetArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN",
    "GRAPHICS_ACTIONS",
    "ManageGraphicsArgs",
    "ApplyFlowLayout",
    "ApplyFlowLayoutArgs",
    "CreateCircle",
    "CreateCircleArgs",
    "CreateLine",
    "CreateLineArgs",
    "CreateRect",
    "CreateRectArgs",
    "CreateText",
    "CreateTextArgs",
    "DeleteWidget",
    "DeleteWidgetArgs",
    "GroupWidgets",
    "GroupWidgetsArgs",
    "SetWidgetStyle",
    "SetWidgetStyleArgs",
]


# ============================================================ extension tools
def _place_symbol(world, page_id, widget_id, position, size, sym_type):
    pre = _check_page_then_unique_widget(world, page_id, widget_id)
    if pre is not None:
        return pre
    widget = Widget(id=widget_id, page_id=page_id, type=sym_type,
                    position=position, size=size, style={"symbol": sym_type})
    return _place_widget(world, page_id, widget)


class CreatePipeArgs(BaseModel):
    action: Literal["create_pipe"] = "create_pipe"
    page_id: str
    widget_id: str
    start: tuple[int, int]
    end: tuple[int, int]
    diameter: int = Field(default=8, ge=1)


class CreatePipe(MockTool):
    name = "create_pipe"
    domain = DOMAIN; action = "create_pipe"
    description = "Draw a process pipe symbol connecting two points on a page."
    args_model = CreatePipeArgs
    examples = ["画一根工艺管道", "draw a pipe from the tank to the pump", "在画面上加一段管路"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: CreatePipeArgs, world: MockWorld) -> ToolResult:
        pre = _check_page_then_unique_widget(world, args.page_id, args.widget_id)
        if pre is not None:
            return pre
        x0, y0 = args.start; x1, y1 = args.end
        widget = Widget(
            id=args.widget_id, page_id=args.page_id, type="pipe",
            position=(min(x0, x1), min(y0, y1)),
            size=(abs(x1 - x0) or args.diameter, abs(y1 - y0) or args.diameter),
            style={"diameter": args.diameter, "start": list(args.start), "end": list(args.end)},
        )
        return _place_widget(world, args.page_id, widget)


class CreateTankArgs(BaseModel):
    action: Literal["create_tank"] = "create_tank"
    page_id: str
    widget_id: str
    position: tuple[int, int]
    size: tuple[int, int] = (80, 120)


class CreateTank(MockTool):
    name = "create_tank"
    domain = DOMAIN; action = "create_tank"
    description = "Draw a storage tank / vessel symbol on a page."
    args_model = CreateTankArgs
    examples = ["画一个储罐", "add a tank symbol", "在画面上放一个反应釜罐体"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: CreateTankArgs, world: MockWorld) -> ToolResult:
        return _place_symbol(world, args.page_id, args.widget_id, args.position, args.size, "tank")


class CreatePumpArgs(BaseModel):
    action: Literal["create_pump"] = "create_pump"
    page_id: str
    widget_id: str
    position: tuple[int, int]
    size: tuple[int, int] = (60, 60)


class CreatePump(MockTool):
    name = "create_pump"
    domain = DOMAIN; action = "create_pump"
    description = "Draw a pump symbol on a page."
    args_model = CreatePumpArgs
    examples = ["画一个泵", "add a centrifugal pump symbol", "在画面上加个水泵"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: CreatePumpArgs, world: MockWorld) -> ToolResult:
        return _place_symbol(world, args.page_id, args.widget_id, args.position, args.size, "pump")


class CreateValveArgs(BaseModel):
    action: Literal["create_valve"] = "create_valve"
    page_id: str
    widget_id: str
    position: tuple[int, int]
    size: tuple[int, int] = (40, 40)


class CreateValve(MockTool):
    name = "create_valve"
    domain = DOMAIN; action = "create_valve"
    description = "Draw a valve symbol on a page."
    args_model = CreateValveArgs
    examples = ["画一个阀门", "add a control valve symbol", "在管路上加个阀"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: CreateValveArgs, world: MockWorld) -> ToolResult:
        return _place_symbol(world, args.page_id, args.widget_id, args.position, args.size, "valve")


class CreateGaugeArgs(BaseModel):
    action: Literal["create_gauge"] = "create_gauge"
    page_id: str
    widget_id: str
    position: tuple[int, int]
    size: tuple[int, int] = (70, 70)


class CreateGauge(MockTool):
    name = "create_gauge"
    domain = DOMAIN; action = "create_gauge"
    description = "Draw a gauge / dial indicator symbol on a page."
    args_model = CreateGaugeArgs
    examples = ["画一个仪表指针", "add a pressure gauge", "放一个圆表盘"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: CreateGaugeArgs, world: MockWorld) -> ToolResult:
        return _place_symbol(world, args.page_id, args.widget_id, args.position, args.size, "gauge")


class CreateMotorArgs(BaseModel):
    action: Literal["create_motor"] = "create_motor"
    page_id: str
    widget_id: str
    position: tuple[int, int]
    size: tuple[int, int] = (60, 60)


class CreateMotor(MockTool):
    name = "create_motor"
    domain = DOMAIN; action = "create_motor"
    description = "Draw a motor symbol on a page."
    args_model = CreateMotorArgs
    examples = ["画一个电机", "add a motor symbol", "在画面上加个马达"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: CreateMotorArgs, world: MockWorld) -> ToolResult:
        return _place_symbol(world, args.page_id, args.widget_id, args.position, args.size, "motor")


class RotateWidgetArgs(BaseModel):
    action: Literal["rotate_widget"] = "rotate_widget"
    page_id: str
    widget_id: str
    degrees: int = Field(ge=-360, le=360)


class RotateWidget(MockTool):
    name = "rotate_widget"
    domain = DOMAIN; action = "rotate_widget"
    description = "Rotate a widget by the given number of degrees."
    args_model = RotateWidgetArgs
    examples = ["把这个图元旋转90度", "rotate the valve 45 degrees", "转一下图元的方向"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    def run(self, args: RotateWidgetArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.page_id} not found")
        page = world.pages[args.page_id]
        if args.widget_id not in page.widgets:
            return fail(ErrorCode.WIDGET_NOT_FOUND, f"widget {args.widget_id} not on page {args.page_id}")
        w = page.widgets[args.widget_id]; w.style = {**w.style, "rotation": args.degrees}
        key = f"pages.{args.page_id}.widgets.{args.widget_id}.style"
        return ok(data={"widget_id": args.widget_id}, world_diff={"added_or_modified": {key: w.style}, "removed": []})


class UngroupWidgetsArgs(BaseModel):
    action: Literal["ungroup_widgets"] = "ungroup_widgets"
    page_id: str
    group_id: str


class UngroupWidgets(MockTool):
    name = "ungroup_widgets"
    domain = DOMAIN; action = "ungroup_widgets"
    description = "Dissolve a widget group back into individual widgets."
    args_model = UngroupWidgetsArgs
    examples = ["把这个组合拆开", "ungroup the pump assembly", "解散图元分组"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.groups.{args.group_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: UngroupWidgetsArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.page_id} not found")
        groups = world.project_meta.get("groups", {}).get(args.page_id, {})
        if args.group_id not in groups:
            return fail(ErrorCode.BUSINESS_RULE, f"group {args.group_id} not found on page {args.page_id}")
        del groups[args.group_id]
        return ok(data={"group_id": args.group_id, "ungrouped": True},
                  world_diff={"added_or_modified": {}, "removed": [f"project_meta.groups.{args.page_id}.{args.group_id}"]})


class AlignWidgetsArgs(BaseModel):
    action: Literal["align_widgets"] = "align_widgets"
    page_id: str
    widget_ids: list[str] = Field(min_length=2)
    edge: Literal["left", "right", "top", "bottom", "center_h", "center_v"] = "left"


class AlignWidgets(MockTool):
    name = "align_widgets"
    domain = DOMAIN; action = "align_widgets"
    description = "Align a set of widgets to a common edge or center line."
    args_model = AlignWidgetsArgs
    examples = ["把这些图元左对齐", "align these widgets to the top", "让图元居中对齐"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{wid}" for wid in args.widget_ids]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: AlignWidgetsArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.page_id} not found")
        page = world.pages[args.page_id]
        missing = [w for w in args.widget_ids if w not in page.widgets]
        if missing:
            return fail(ErrorCode.WIDGET_NOT_FOUND, f"widgets {missing} not on page {args.page_id}")
        return ok(data={"aligned": len(args.widget_ids), "edge": args.edge})


class DistributeWidgetsArgs(BaseModel):
    action: Literal["distribute_widgets"] = "distribute_widgets"
    page_id: str
    widget_ids: list[str] = Field(min_length=3)
    axis: Literal["horizontal", "vertical"] = "horizontal"


class DistributeWidgets(MockTool):
    name = "distribute_widgets"
    domain = DOMAIN; action = "distribute_widgets"
    description = "Evenly distribute spacing between a set of widgets along an axis."
    args_model = DistributeWidgetsArgs
    examples = ["把这些图元等间距分布", "distribute these widgets horizontally", "让图元均匀排布"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{wid}" for wid in args.widget_ids]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: DistributeWidgetsArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.page_id} not found")
        return ok(data={"distributed": len(args.widget_ids), "axis": args.axis})


class SetWidgetAnimationArgs(BaseModel):
    action: Literal["set_widget_animation"] = "set_widget_animation"
    page_id: str
    widget_id: str
    animation: Literal["blink", "rotate", "fill_level", "color_by_value"]
    tag: str | None = None


class SetWidgetAnimation(MockTool):
    name = "set_widget_animation"
    domain = DOMAIN; action = "set_widget_animation"
    description = "Attach a dynamic animation (blink/rotate/fill/color) driven by a tag to a widget."
    args_model = SetWidgetAnimationArgs
    examples = ["给图元加一个闪烁动画", "make the tank fill level follow LEVEL_101", "让泵在运行时转动"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}.animation"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    def run(self, args: SetWidgetAnimationArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.page_id} not found")
        page = world.pages[args.page_id]
        if args.widget_id not in page.widgets:
            return fail(ErrorCode.WIDGET_NOT_FOUND, f"widget {args.widget_id} not on page {args.page_id}")
        return ok(data={"widget_id": args.widget_id, "animation": args.animation})


class BringWidgetToFrontArgs(BaseModel):
    action: Literal["bring_widget_to_front"] = "bring_widget_to_front"
    page_id: str
    widget_id: str


class BringWidgetToFront(MockTool):
    name = "bring_widget_to_front"
    domain = DOMAIN; action = "bring_widget_to_front"
    description = "Raise a widget to the top of the z-order."
    args_model = BringWidgetToFrontArgs
    examples = ["把这个图元置于最前", "bring the label to front", "让图元显示在最上层"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}.z"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    def run(self, args: BringWidgetToFrontArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages or args.widget_id not in world.pages[args.page_id].widgets:
            return fail(ErrorCode.WIDGET_NOT_FOUND, f"widget {args.widget_id} not on page {args.page_id}")
        return ok(data={"widget_id": args.widget_id, "z": "front"})


class SendWidgetToBackArgs(BaseModel):
    action: Literal["send_widget_to_back"] = "send_widget_to_back"
    page_id: str
    widget_id: str


class SendWidgetToBack(MockTool):
    name = "send_widget_to_back"
    domain = DOMAIN; action = "send_widget_to_back"
    description = "Send a widget to the bottom of the z-order."
    args_model = SendWidgetToBackArgs
    examples = ["把这个图元置于最后", "send the background rect to back", "让图元显示在最底层"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}.z"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    def run(self, args: SendWidgetToBackArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages or args.widget_id not in world.pages[args.page_id].widgets:
            return fail(ErrorCode.WIDGET_NOT_FOUND, f"widget {args.widget_id} not on page {args.page_id}")
        return ok(data={"widget_id": args.widget_id, "z": "back"})


GRAPHICS_ACTIONS.update({
    cls.action: cls
    for cls in (
        CreatePipe, CreateTank, CreatePump, CreateValve, CreateGauge, CreateMotor,
        RotateWidget, UngroupWidgets, AlignWidgets, DistributeWidgets,
        SetWidgetAnimation, BringWidgetToFront, SendWidgetToBack,
    )
})
