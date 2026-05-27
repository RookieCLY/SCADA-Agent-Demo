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
