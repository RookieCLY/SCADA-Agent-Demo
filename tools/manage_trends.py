"""manage_trends — trend-curve / historical trend configuration stubs.

SCADA trend curves display real-time and historical process data graphically.
These stubs provide realistic schemas without modifying world state.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import MockTool, ToolResult, ok

DOMAIN = "manage_trends"


# ---------------------------------------------------------------- create_trend_group
class CreateTrendGroupArgs(BaseModel):
    action: Literal["create_trend_group"] = "create_trend_group"
    group_name: str = Field(description="Unique trend group name, e.g. 'Reactor_Trends'")
    description: str | None = None
    page_id: str | None = Field(default=None, description="Parent HMI page to embed the trend into")
    width: int = Field(default=800, ge=100)
    height: int = Field(default=400, ge=100)


class CreateTrendGroup(MockTool):
    name = "create_trend_group"
    domain = DOMAIN; action = "create_trend_group"
    description = "Create a new trend curve group on an HMI page."
    args_model = CreateTrendGroupArgs
    examples = ["新建一个趋势曲线组", "create trend group for reactor monitoring", "添加趋势图"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_name}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"] if args.page_id else []

    def run(self, args: CreateTrendGroupArgs, world: object) -> ToolResult:
        return ok(data={"group_name": args.group_name, "created": True})


# ---------------------------------------------------------------- add_trend_pen
class AddTrendPenArgs(BaseModel):
    action: Literal["add_trend_pen"] = "add_trend_pen"
    group_name: str
    pen_id: str = Field(description="Unique pen ID within the group, e.g. 'pen_temp'")
    tag: str = Field(description="SCADA point tag to plot")
    color: str = Field(default="#FF0000", description="Line color in hex")
    line_width: int = Field(default=2, ge=1, le=10)
    line_style: Literal["solid", "dashed", "dotted", "dash_dot"] = "solid"
    y_axis: Literal["left", "right"] = "left"


class AddTrendPen(MockTool):
    name = "add_trend_pen"
    domain = DOMAIN; action = "add_trend_pen"
    description = "Add a data pen (curve) to an existing trend group."
    args_model = AddTrendPenArgs
    examples = ["在趋势图上添加温度曲线", "add TEMP_101 to trend", "加一条红色的压力曲线"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_name}.pens.{args.pen_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_name}", f"points.{args.tag}"]

    def run(self, args: AddTrendPenArgs, world: object) -> ToolResult:
        return ok(data={"pen_id": args.pen_id, "added": True})


# ---------------------------------------------------------------- configure_trend_axis
class ConfigureTrendAxisArgs(BaseModel):
    action: Literal["configure_trend_axis"] = "configure_trend_axis"
    group_name: str
    axis: Literal["left", "right", "bottom"] = "left"
    label: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    auto_scale: bool = True
    logarithmic: bool = False
    grid_visible: bool = True


class ConfigureTrendAxis(MockTool):
    name = "configure_trend_axis"
    domain = DOMAIN; action = "configure_trend_axis"
    description = "Configure the scale, label, and display options for a trend axis."
    args_model = ConfigureTrendAxisArgs
    examples = ["设置趋势图Y轴范围", "auto-scale the left axis", "把温度轴范围设成0-100"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_name}.axes.{args.axis}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_name}"]

    def run(self, args: ConfigureTrendAxisArgs, world: object) -> ToolResult:
        return ok(data={"axis": args.axis, "configured": True})


# ---------------------------------------------------------------- set_trend_sampling
class SetTrendSamplingArgs(BaseModel):
    action: Literal["set_trend_sampling"] = "set_trend_sampling"
    group_name: str
    sample_interval_s: float = Field(default=1.0, gt=0, description="Seconds between samples")
    buffer_size: int = Field(default=3600, ge=60, description="How many samples to keep in memory")


class SetTrendSampling(MockTool):
    name = "set_trend_sampling"
    domain = DOMAIN; action = "set_trend_sampling"
    description = "Set the data sampling rate and buffer size for a trend group."
    args_model = SetTrendSamplingArgs
    examples = ["设置趋势采样间隔为500毫秒", "change sampling to 5 seconds", "调整采样频率"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_name}.sampling"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_name}"]

    def run(self, args: SetTrendSamplingArgs, world: object) -> ToolResult:
        return ok(data={"sampling_configured": True})


# ---------------------------------------------------------------- enable_trend_scroll
class EnableTrendScrollArgs(BaseModel):
    action: Literal["enable_trend_scroll"] = "enable_trend_scroll"
    group_name: str
    history_window_s: float = Field(default=3600.0, ge=60.0, description="Seconds of history to buffer for scrollback")
    scroll_enabled: bool = True


class EnableTrendScroll(MockTool):
    name = "enable_trend_scroll"
    domain = DOMAIN; action = "enable_trend_scroll"
    description = "Enable or disable historical scroll-back on a trend group."
    args_model = EnableTrendScrollArgs
    examples = ["开启趋势历史回看", "enable scroll-back for reactor trend", "允许拖动查看历史数据"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_name}.history"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_name}"]

    def run(self, args: EnableTrendScrollArgs, world: object) -> ToolResult:
        return ok(data={"scroll_enabled": args.scroll_enabled})


# ---------------------------------------------------------------- delete_trend_group
class DeleteTrendGroupArgs(BaseModel):
    action: Literal["delete_trend_group"] = "delete_trend_group"
    group_name: str


class DeleteTrendGroup(MockTool):
    name = "delete_trend_group"
    domain = DOMAIN; action = "delete_trend_group"
    description = "Remove a trend group and all its pens from an HMI page."
    args_model = DeleteTrendGroupArgs
    examples = ["删除这个趋势图", "remove reactor trend group", "把趋势曲线删掉"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_name}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_name}"]

    def run(self, args: DeleteTrendGroupArgs, world: object) -> ToolResult:
        return ok(data={"group_name": args.group_name, "deleted": True})


# ---------------------------------------------------------------- list_trend_groups
class ListTrendGroupsArgs(BaseModel):
    action: Literal["list_trend_groups"] = "list_trend_groups"
    page_id: str | None = None


class ListTrendGroups(MockTool):
    name = "list_trend_groups"
    domain = DOMAIN; action = "list_trend_groups"
    description = "List all trend groups, optionally filtered by page."
    args_model = ListTrendGroupsArgs
    examples = ["列出所有趋势图", "show me all trend groups on page p1", "有哪些趋势"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"pages.{args.page_id}"] if args.page_id else []

    def run(self, args: ListTrendGroupsArgs, world: object) -> ToolResult:
        return ok(data={"trends": [], "count": 0})


# ---------------------------------------------------------------- registry hookup
TREND_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (CreateTrendGroup, AddTrendPen, ConfigureTrendAxis, SetTrendSampling, EnableTrendScroll, DeleteTrendGroup, ListTrendGroups)
}

ManageTrendsArgs = Annotated[
    Union[
        CreateTrendGroupArgs, AddTrendPenArgs, ConfigureTrendAxisArgs,
        SetTrendSamplingArgs, EnableTrendScrollArgs, DeleteTrendGroupArgs, ListTrendGroupsArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN", "ManageTrendsArgs", "TREND_ACTIONS",
    "CreateTrendGroup", "AddTrendPen", "ConfigureTrendAxis",
    "SetTrendSampling", "EnableTrendScroll", "DeleteTrendGroup", "ListTrendGroups",
]
