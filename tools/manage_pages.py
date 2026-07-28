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


# ============================================================ extension tools
def _page_diff(page_id, page):
    return {"added_or_modified": {f"pages.{page_id}": page.model_dump()}, "removed": []}


def _need_page(world, page_id):
    if page_id not in world.pages:
        return fail(ErrorCode.PAGE_NOT_FOUND, f"page {page_id} not found")
    return None


def _need_widget(world, page_id, widget_id):
    err = _need_page(world, page_id)
    if err: return err
    if widget_id not in world.pages[page_id].widgets:
        return fail(ErrorCode.WIDGET_NOT_FOUND, f"widget {widget_id} not on page {page_id}")
    return None


class ClonePageArgs(BaseModel):
    action: Literal["clone_page"] = "clone_page"
    page_id: str
    new_page_id: str
    new_name: str | None = None


class ClonePage(MockTool):
    name = "clone_page"
    domain = DOMAIN; action = "clone_page"
    description = "Duplicate a page (with its widgets) under a new id."
    args_model = ClonePageArgs
    examples = ["复制一个画面", "clone the reactor overview page", "照着这个页面新建一个"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.new_page_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: ClonePageArgs, world: MockWorld) -> ToolResult:
        err = _need_page(world, args.page_id)
        if err: return err
        if args.new_page_id in world.pages:
            return fail(ErrorCode.ALREADY_EXISTS, f"page {args.new_page_id} already exists")
        new = world.pages[args.page_id].model_copy(deep=True, update={"id": args.new_page_id})
        if args.new_name is not None:
            new.name = args.new_name
        world.pages[args.new_page_id] = new
        return ok(data={"page_id": args.new_page_id}, world_diff=_page_diff(args.new_page_id, new))


class SetPageResolutionArgs(BaseModel):
    action: Literal["set_page_resolution"] = "set_page_resolution"
    page_id: str
    width: int = Field(ge=320, le=7680)
    height: int = Field(ge=240, le=4320)


class SetPageResolution(MockTool):
    name = "set_page_resolution"
    domain = DOMAIN; action = "set_page_resolution"
    description = "Set the pixel resolution of a page."
    args_model = SetPageResolutionArgs
    examples = ["把画面分辨率设成 1920x1080", "set the page resolution to 4K", "调整页面尺寸"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: SetPageResolutionArgs, world: MockWorld) -> ToolResult:
        err = _need_page(world, args.page_id)
        if err: return err
        p = world.pages[args.page_id]; p.resolution = (args.width, args.height)
        return ok(data={"page_id": args.page_id}, world_diff=_page_diff(args.page_id, p))


class SetPageBackgroundArgs(BaseModel):
    action: Literal["set_page_background"] = "set_page_background"
    page_id: str
    background: str = Field(description="Hex color or image reference")


class SetPageBackground(MockTool):
    name = "set_page_background"
    domain = DOMAIN; action = "set_page_background"
    description = "Set the background color/image of a page."
    args_model = SetPageBackgroundArgs
    examples = ["把画面背景设成深色", "set the page background to #202020", "换一下页面背景"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: SetPageBackgroundArgs, world: MockWorld) -> ToolResult:
        err = _need_page(world, args.page_id)
        if err: return err
        p = world.pages[args.page_id]; p.background = args.background
        return ok(data={"page_id": args.page_id}, world_diff=_page_diff(args.page_id, p))


class SetHomePageArgs(BaseModel):
    action: Literal["set_home_page"] = "set_home_page"
    page_id: str


class SetHomePage(MockTool):
    name = "set_home_page"
    domain = DOMAIN; action = "set_home_page"
    description = "Set which page is shown as the runtime home/start screen."
    args_model = SetHomePageArgs
    examples = ["把这个画面设为主页", "make this the startup screen", "设置默认打开的画面"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return ["project_meta.home_page"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: SetHomePageArgs, world: MockWorld) -> ToolResult:
        err = _need_page(world, args.page_id)
        if err: return err
        return ok(data={"home_page": args.page_id})


class LockPageArgs(BaseModel):
    action: Literal["lock_page"] = "lock_page"
    page_id: str


class LockPage(MockTool):
    name = "lock_page"
    domain = DOMAIN; action = "lock_page"
    description = "Lock a page against edits during configuration review."
    args_model = LockPageArgs
    examples = ["锁定这个画面防止误改", "lock the page from editing", "把画面锁上"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.locked"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: LockPageArgs, world: MockWorld) -> ToolResult:
        err = _need_page(world, args.page_id)
        if err: return err
        return ok(data={"page_id": args.page_id, "locked": True})


class UnlockPageArgs(BaseModel):
    action: Literal["unlock_page"] = "unlock_page"
    page_id: str


class UnlockPage(MockTool):
    name = "unlock_page"
    domain = DOMAIN; action = "unlock_page"
    description = "Unlock a previously locked page for editing."
    args_model = UnlockPageArgs
    examples = ["解锁这个画面", "unlock the page for editing", "把画面解锁"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.locked"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: UnlockPageArgs, world: MockWorld) -> ToolResult:
        err = _need_page(world, args.page_id)
        if err: return err
        return ok(data={"page_id": args.page_id, "locked": False})


class SetPagePermissionsArgs(BaseModel):
    action: Literal["set_page_permissions"] = "set_page_permissions"
    page_id: str
    min_role: Literal["operator", "engineer", "supervisor", "administrator", "viewer"] = "operator"


class SetPagePermissions(MockTool):
    name = "set_page_permissions"
    domain = DOMAIN; action = "set_page_permissions"
    description = "Restrict which role may view/operate a page."
    args_model = SetPagePermissionsArgs
    examples = ["设置画面的访问权限", "only supervisors can open this page", "限制谁能看这个画面"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.permissions"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: SetPagePermissionsArgs, world: MockWorld) -> ToolResult:
        err = _need_page(world, args.page_id)
        if err: return err
        return ok(data={"page_id": args.page_id, "min_role": args.min_role})


class MoveWidgetArgs(BaseModel):
    action: Literal["move_widget"] = "move_widget"
    page_id: str
    widget_id: str
    position: tuple[int, int]


class MoveWidget(MockTool):
    name = "move_widget"
    domain = DOMAIN; action = "move_widget"
    description = "Move a widget to a new position on its page."
    args_model = MoveWidgetArgs
    examples = ["把这个图元移到左上角", "move the pump widget to (100,200)", "调整图元位置"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    def run(self, args: MoveWidgetArgs, world: MockWorld) -> ToolResult:
        err = _need_widget(world, args.page_id, args.widget_id)
        if err: return err
        w = world.pages[args.page_id].widgets[args.widget_id]; w.position = args.position
        key = f"pages.{args.page_id}.widgets.{args.widget_id}.position"
        return ok(data={"widget_id": args.widget_id}, world_diff={"added_or_modified": {key: list(args.position)}, "removed": []})


class ResizeWidgetArgs(BaseModel):
    action: Literal["resize_widget"] = "resize_widget"
    page_id: str
    widget_id: str
    size: tuple[int, int]


class ResizeWidget(MockTool):
    name = "resize_widget"
    domain = DOMAIN; action = "resize_widget"
    description = "Resize a widget on its page."
    args_model = ResizeWidgetArgs
    examples = ["把这个图元放大", "resize the tank widget", "调整图元大小"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    def run(self, args: ResizeWidgetArgs, world: MockWorld) -> ToolResult:
        err = _need_widget(world, args.page_id, args.widget_id)
        if err: return err
        w = world.pages[args.page_id].widgets[args.widget_id]; w.size = args.size
        key = f"pages.{args.page_id}.widgets.{args.widget_id}.size"
        return ok(data={"widget_id": args.widget_id}, world_diff={"added_or_modified": {key: list(args.size)}, "removed": []})


class UpdateWidgetBindingArgs(BaseModel):
    action: Literal["update_widget_binding"] = "update_widget_binding"
    page_id: str
    widget_id: str
    property: str
    tag: str


class UpdateWidgetBinding(MockTool):
    name = "update_widget_binding"
    domain = DOMAIN; action = "update_widget_binding"
    description = "Change which point a widget property is bound to."
    args_model = UpdateWidgetBindingArgs
    examples = ["改一下图元绑定的点位", "rebind this gauge to PRESS_102", "更新图元的点位绑定"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}.bindings"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}", f"points.{args.tag}"]

    def run(self, args: UpdateWidgetBindingArgs, world: MockWorld) -> ToolResult:
        err = _need_widget(world, args.page_id, args.widget_id)
        if err: return err
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        w = world.pages[args.page_id].widgets[args.widget_id]; w.bindings[args.property] = args.tag
        key = f"pages.{args.page_id}.widgets.{args.widget_id}.bindings"
        return ok(data={"widget_id": args.widget_id}, world_diff={"added_or_modified": {key: dict(w.bindings)}, "removed": []})


class UnbindWidgetPointArgs(BaseModel):
    action: Literal["unbind_widget_point"] = "unbind_widget_point"
    page_id: str
    widget_id: str
    property: str


class UnbindWidgetPoint(MockTool):
    name = "unbind_widget_point"
    domain = DOMAIN; action = "unbind_widget_point"
    description = "Remove a point binding from a widget property."
    args_model = UnbindWidgetPointArgs
    examples = ["解除图元的点位绑定", "unbind the value property of this widget", "取消图元绑定"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}.bindings"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}.widgets.{args.widget_id}"]

    def run(self, args: UnbindWidgetPointArgs, world: MockWorld) -> ToolResult:
        err = _need_widget(world, args.page_id, args.widget_id)
        if err: return err
        w = world.pages[args.page_id].widgets[args.widget_id]
        w.bindings.pop(args.property, None)
        key = f"pages.{args.page_id}.widgets.{args.widget_id}.bindings"
        return ok(data={"widget_id": args.widget_id}, world_diff={"added_or_modified": {key: dict(w.bindings)}, "removed": []})


class ListWidgetsArgs(BaseModel):
    action: Literal["list_widgets"] = "list_widgets"
    page_id: str


class ListWidgets(MockTool):
    name = "list_widgets"
    domain = DOMAIN; action = "list_widgets"
    description = "List all widgets on a page with their type and binding summary."
    args_model = ListWidgetsArgs
    examples = ["列出这个画面上的所有图元", "show the widgets on the overview page", "看看画面上有哪些图元"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: ListWidgetsArgs, world: MockWorld) -> ToolResult:
        err = _need_page(world, args.page_id)
        if err: return err
        widgets = [w.model_dump() for w in world.pages[args.page_id].widgets.values()]
        return ok(data={"page_id": args.page_id, "count": len(widgets), "widgets": widgets})


class ExportPageArgs(BaseModel):
    action: Literal["export_page"] = "export_page"
    page_id: str


class ExportPage(MockTool):
    name = "export_page"
    domain = DOMAIN; action = "export_page"
    description = "Export a page definition (widgets + bindings) to a portable file."
    args_model = ExportPageArgs
    examples = ["导出这个画面", "export the page for reuse in another project", "把画面导出成文件"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"]

    def run(self, args: ExportPageArgs, world: MockWorld) -> ToolResult:
        err = _need_page(world, args.page_id)
        if err: return err
        return ok(data={"page_id": args.page_id, "exported": True})


PAGE_ACTIONS.update({
    cls.action: cls
    for cls in (
        ClonePage, SetPageResolution, SetPageBackground, SetHomePage, LockPage, UnlockPage,
        SetPagePermissions, MoveWidget, ResizeWidget, UpdateWidgetBinding, UnbindWidgetPoint,
        ListWidgets, ExportPage,
    )
})
