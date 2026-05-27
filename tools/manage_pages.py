"""manage_pages — page/widget CRUD."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from tools._base import ErrorCode, MockTool, ToolResult, fail, ok
from world import MockWorld
from world.models import Page, Widget

DOMAIN = "manage_pages"


# ---------------------------------------------------------------- create_page
class CreatePageArgs(BaseModel):
    action: Literal["create_page"] = "create_page"
    id: str
    name: str
    resolution: tuple[int, int] = (1920, 1080)
    background: str = "#FFFFFF"


class CreatePage(MockTool):
    name = "create_page"
    domain = DOMAIN
    action = "create_page"
    description = "Create a new HMI page (screen) with a given resolution and background."
    args_model = CreatePageArgs
    examples = ["新建一个监控画面", "create a 1080p page"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return []

    def run(self, args: CreatePageArgs, world: MockWorld) -> ToolResult:
        if args.id in world.pages:
            return fail(ErrorCode.ALREADY_EXISTS, f"page {args.id} already exists")
        page = Page(
            id=args.id,
            name=args.name,
            resolution=args.resolution,
            background=args.background,
        )
        world.pages[args.id] = page
        return ok(
            data={"page_id": args.id},
            world_diff={"added_or_modified": {f"pages.{args.id}": page.model_dump()}, "removed": []},
        )


# ---------------------------------------------------------------- rename_page
class RenamePageArgs(BaseModel):
    action: Literal["rename_page"] = "rename_page"
    id: str
    new_name: str


class RenamePage(MockTool):
    name = "rename_page"
    domain = DOMAIN
    action = "rename_page"
    description = "Change the display name of an existing page."
    args_model = RenamePageArgs
    examples = ["改个名字"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.id}"]

    def run(self, args: RenamePageArgs, world: MockWorld) -> ToolResult:
        if args.id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.id} not found")
        world.pages[args.id].name = args.new_name
        return ok(
            data={"page_id": args.id},
            world_diff={"added_or_modified": {f"pages.{args.id}.name": args.new_name}, "removed": []},
        )


# ---------------------------------------------------------------- delete_page
class DeletePageArgs(BaseModel):
    action: Literal["delete_page"] = "delete_page"
    id: str


class DeletePage(MockTool):
    name = "delete_page"
    domain = DOMAIN
    action = "delete_page"
    description = "Delete a page and all its widgets."
    args_model = DeletePageArgs
    examples = ["删除画面"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.id}"]

    def run(self, args: DeletePageArgs, world: MockWorld) -> ToolResult:
        if args.id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.id} not found")
        del world.pages[args.id]
        return ok(
            data={"page_id": args.id},
            world_diff={"added_or_modified": {}, "removed": [f"pages.{args.id}"]},
        )


# ---------------------------------------------------------------- create_widget
class CreateWidgetArgs(BaseModel):
    action: Literal["create_widget"] = "create_widget"
    page_id: str
    widget_id: str
    type: str = Field(description="widget type, e.g. 'thermometer' / 'tank' / 'pump' / 'rect'")
    position: tuple[int, int]
    size: tuple[int, int]
    style: dict[str, Any] = Field(default_factory=dict)
    expected_binding_types: dict[str, list[str]] = Field(default_factory=dict)


class CreateWidget(MockTool):
    name = "create_widget"
    domain = DOMAIN
    action = "create_widget"
    description = "Place a graphical widget (thermometer, tank, pump, …) on a page."
    args_model = CreateWidgetArgs
    examples = ["在画面上画一个温度计", "add a tank widget"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}"]

    def run(self, args: CreateWidgetArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.page_id} not found")
        page = world.pages[args.page_id]
        if args.widget_id in page.widgets:
            return fail(ErrorCode.ALREADY_EXISTS, f"widget {args.widget_id} already on page")
        widget = Widget(
            id=args.widget_id,
            page_id=args.page_id,
            type=args.type,
            position=args.position,
            size=args.size,
            style=args.style,
            expected_binding_types=args.expected_binding_types,
        )
        page.widgets[args.widget_id] = widget
        key = f"pages.{args.page_id}.widgets.{args.widget_id}"
        return ok(
            data={"widget_id": args.widget_id},
            world_diff={"added_or_modified": {key: widget.model_dump()}, "removed": []},
        )


# ---------------------------------------------------------------- bind_point
class BindPointArgs(BaseModel):
    action: Literal["bind_point"] = "bind_point"
    page_id: str
    widget_id: str
    property: str = Field(description="widget property to bind, e.g. 'value' / 'color' / 'visible'")
    tag: str


class BindPoint(MockTool):
    name = "bind_point"
    domain = DOMAIN
    action = "bind_point"
    description = "Bind a SCADA point tag to a widget property, e.g. thermometer.value ← TEMP_101."
    args_model = BindPointArgs
    examples = [
        "给反应釜温度计绑定 TEMP_101",
        "bind pressure point to the gauge",
        "把温度点绑到温度计的 value 属性",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"pages.{args.page_id}.widgets.{args.widget_id}.bindings.{args.property}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [
            f"pages.{args.page_id}",
            f"pages.{args.page_id}.widgets.{args.widget_id}",
            f"points.{args.tag}",
        ]

    def run(self, args: BindPointArgs, world: MockWorld) -> ToolResult:
        if args.page_id not in world.pages:
            return fail(ErrorCode.PAGE_NOT_FOUND, f"page {args.page_id} not found")
        page = world.pages[args.page_id]
        if args.widget_id not in page.widgets:
            return fail(
                ErrorCode.WIDGET_NOT_FOUND,
                f"widget {args.widget_id} not on page {args.page_id}",
            )
        widget = page.widgets[args.widget_id]
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        point = world.points[args.tag]
        expected = widget.expected_binding_types.get(args.property)
        if expected and point.type not in expected:
            return fail(
                ErrorCode.TYPE_MISMATCH,
                f"property {args.property} expects {expected}, got {point.type}",
            )
        if args.property in widget.bindings:
            return fail(
                ErrorCode.ALREADY_BOUND,
                f"{args.property} already bound to {widget.bindings[args.property]}",
            )
        widget.bindings[args.property] = args.tag
        key = f"pages.{args.page_id}.widgets.{args.widget_id}.bindings.{args.property}"
        return ok(
            data={"binding": f"{args.widget_id}.{args.property}={args.tag}"},
            world_diff={"added_or_modified": {key: args.tag}, "removed": []},
        )


# ---------------------------------------------------------------- list (read-only)
class ListPagesArgs(BaseModel):
    action: Literal["list_pages"] = "list_pages"


class ListPages(MockTool):
    name = "list_pages"
    domain = DOMAIN
    action = "list_pages"
    description = "Return the list of pages with their IDs and names."
    args_model = ListPagesArgs
    examples = ["列出所有画面"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return []

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return []

    def run(self, args: ListPagesArgs, world: MockWorld) -> ToolResult:
        return ok(
            data={
                "count": len(world.pages),
                "pages": [{"id": p.id, "name": p.name} for p in world.pages.values()],
            }
        )


# ---------------------------------------------------------------- registry hookup
PAGE_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (CreatePage, RenamePage, DeletePage, CreateWidget, BindPoint, ListPages)
}

ManagePagesArgs = Annotated[
    Union[
        CreatePageArgs,
        RenamePageArgs,
        DeletePageArgs,
        CreateWidgetArgs,
        BindPointArgs,
        ListPagesArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN",
    "PAGE_ACTIONS",
    "ManagePagesArgs",
    "BindPoint",
    "BindPointArgs",
    "CreatePage",
    "CreatePageArgs",
    "CreateWidget",
    "CreateWidgetArgs",
    "DeletePage",
    "DeletePageArgs",
    "ListPages",
    "ListPagesArgs",
    "RenamePage",
    "RenamePageArgs",
]
