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


# ============================================================ extension tools
class RemoveTrendPenArgs(BaseModel):
    action: Literal["remove_trend_pen"] = "remove_trend_pen"
    group_id: str
    tag: str


class RemoveTrendPen(MockTool):
    name = "remove_trend_pen"
    domain = DOMAIN; action = "remove_trend_pen"
    description = "Remove a pen (tag curve) from a trend group."
    args_model = RemoveTrendPenArgs
    examples = ["从趋势图里删掉一条曲线", "remove the pressure pen from the trend", "去掉这条趋势线"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}.pens.{args.tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}"]

    def run(self, args: RemoveTrendPenArgs, world: object) -> ToolResult:
        return ok(data={"group_id": args.group_id, "tag": args.tag, "removed": True})


class SetTrendTimeRangeArgs(BaseModel):
    action: Literal["set_trend_time_range"] = "set_trend_time_range"
    group_id: str
    minutes: int = Field(ge=1, le=525600)


class SetTrendTimeRange(MockTool):
    name = "set_trend_time_range"
    domain = DOMAIN; action = "set_trend_time_range"
    description = "Set the visible time window (minutes) of a trend group."
    args_model = SetTrendTimeRangeArgs
    examples = ["设置趋势图的时间范围", "show the last 60 minutes on this trend", "把趋势窗口设为一天"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}.time_range"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}"]

    def run(self, args: SetTrendTimeRangeArgs, world: object) -> ToolResult:
        return ok(data={"group_id": args.group_id, "minutes": args.minutes})


class SetTrendPenColorArgs(BaseModel):
    action: Literal["set_trend_pen_color"] = "set_trend_pen_color"
    group_id: str
    tag: str
    color: str = Field(description="Hex or named color, e.g. '#ff0000' or 'red'")


class SetTrendPenColor(MockTool):
    name = "set_trend_pen_color"
    domain = DOMAIN; action = "set_trend_pen_color"
    description = "Set the line color of a trend pen."
    args_model = SetTrendPenColorArgs
    examples = ["把这条趋势线改成红色", "set the temperature pen to red", "调整曲线颜色"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}.pens.{args.tag}.color"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}"]

    def run(self, args: SetTrendPenColorArgs, world: object) -> ToolResult:
        return ok(data={"tag": args.tag, "color": args.color})


class SetTrendPenScaleArgs(BaseModel):
    action: Literal["set_trend_pen_scale"] = "set_trend_pen_scale"
    group_id: str
    tag: str
    min_value: float
    max_value: float


class SetTrendPenScale(MockTool):
    name = "set_trend_pen_scale"
    domain = DOMAIN; action = "set_trend_pen_scale"
    description = "Set the vertical scale (min/max) of a trend pen."
    args_model = SetTrendPenScaleArgs
    examples = ["设置曲线的纵轴范围", "scale this pen from 0 to 100", "调整趋势线的量程"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}.pens.{args.tag}.scale"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}"]

    def run(self, args: SetTrendPenScaleArgs, world: object) -> ToolResult:
        return ok(data={"tag": args.tag, "min": args.min_value, "max": args.max_value})


class ExportTrendDataArgs(BaseModel):
    action: Literal["export_trend_data"] = "export_trend_data"
    group_id: str
    format: Literal["csv", "excel"] = "csv"


class ExportTrendData(MockTool):
    name = "export_trend_data"
    domain = DOMAIN; action = "export_trend_data"
    description = "Export the data currently shown in a trend group to a file."
    args_model = ExportTrendDataArgs
    examples = ["导出趋势图的数据", "export this trend to Excel", "把曲线数据存成 CSV"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}"]

    def run(self, args: ExportTrendDataArgs, world: object) -> ToolResult:
        return ok(data={"group_id": args.group_id, "format": args.format})


class AddTrendMarkerArgs(BaseModel):
    action: Literal["add_trend_marker"] = "add_trend_marker"
    group_id: str
    marker_id: str
    label: str


class AddTrendMarker(MockTool):
    name = "add_trend_marker"
    domain = DOMAIN; action = "add_trend_marker"
    description = "Add an annotation marker (e.g. batch start) to a trend group."
    args_model = AddTrendMarkerArgs
    examples = ["在趋势图上加一个标注", "mark the batch start on the trend", "给曲线加个事件标记"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}.markers.{args.marker_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}"]

    def run(self, args: AddTrendMarkerArgs, world: object) -> ToolResult:
        return ok(data={"marker_id": args.marker_id, "label": args.label})


class SetTrendRefreshRateArgs(BaseModel):
    action: Literal["set_trend_refresh_rate"] = "set_trend_refresh_rate"
    group_id: str
    interval_ms: int = Field(ge=100, le=60000)


class SetTrendRefreshRate(MockTool):
    name = "set_trend_refresh_rate"
    domain = DOMAIN; action = "set_trend_refresh_rate"
    description = "Set how often a live trend redraws."
    args_model = SetTrendRefreshRateArgs
    examples = ["设置趋势图刷新频率", "refresh this trend every second", "调整曲线的刷新速度"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}.refresh_rate"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}"]

    def run(self, args: SetTrendRefreshRateArgs, world: object) -> ToolResult:
        return ok(data={"group_id": args.group_id, "interval_ms": args.interval_ms})


class FreezeTrendArgs(BaseModel):
    action: Literal["freeze_trend"] = "freeze_trend"
    group_id: str
    frozen: bool = True


class FreezeTrend(MockTool):
    name = "freeze_trend"
    domain = DOMAIN; action = "freeze_trend"
    description = "Freeze (pause) or unfreeze a live trend for inspection."
    args_model = FreezeTrendArgs
    examples = ["冻结趋势图", "pause the live trend to look closely", "暂停一下曲线刷新"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}.frozen"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}"]

    def run(self, args: FreezeTrendArgs, world: object) -> ToolResult:
        return ok(data={"group_id": args.group_id, "frozen": args.frozen})


class CompareTrendPeriodsArgs(BaseModel):
    action: Literal["compare_trend_periods"] = "compare_trend_periods"
    group_id: str
    period_a_start: str
    period_b_start: str
    duration_minutes: int = Field(default=60, ge=1, le=10080)


class CompareTrendPeriods(MockTool):
    name = "compare_trend_periods"
    domain = DOMAIN; action = "compare_trend_periods"
    description = "Overlay two time periods of the same trend for comparison."
    args_model = CompareTrendPeriodsArgs
    examples = ["对比两个时间段的趋势", "compare today's curve with yesterday's", "把两班的曲线叠起来看"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}"]

    def run(self, args: CompareTrendPeriodsArgs, world: object) -> ToolResult:
        return ok(data={"group_id": args.group_id})


class SetTrendLegendArgs(BaseModel):
    action: Literal["set_trend_legend"] = "set_trend_legend"
    group_id: str
    show: bool = True
    position: Literal["top", "bottom", "left", "right"] = "bottom"


class SetTrendLegend(MockTool):
    name = "set_trend_legend"
    domain = DOMAIN; action = "set_trend_legend"
    description = "Show/hide and position the legend of a trend group."
    args_model = SetTrendLegendArgs
    examples = ["显示趋势图的图例", "put the legend at the top", "隐藏曲线图例"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}.legend"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.group_id}"]

    def run(self, args: SetTrendLegendArgs, world: object) -> ToolResult:
        return ok(data={"group_id": args.group_id, "legend_shown": args.show})


class CloneTrendGroupArgs(BaseModel):
    action: Literal["clone_trend_group"] = "clone_trend_group"
    source_group_id: str
    new_group_id: str


class CloneTrendGroup(MockTool):
    name = "clone_trend_group"
    domain = DOMAIN; action = "clone_trend_group"
    description = "Duplicate a trend group (pens + axes) under a new id."
    args_model = CloneTrendGroupArgs
    examples = ["复制一个趋势组", "clone this trend for the other reactor", "基于现有趋势新建一个"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.new_group_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"trends.{args.source_group_id}"]

    def run(self, args: CloneTrendGroupArgs, world: object) -> ToolResult:
        return ok(data={"new_group_id": args.new_group_id})


TREND_ACTIONS.update({
    cls.action: cls
    for cls in (
        RemoveTrendPen, SetTrendTimeRange, SetTrendPenColor, SetTrendPenScale,
        ExportTrendData, AddTrendMarker, SetTrendRefreshRate, FreezeTrend,
        CompareTrendPeriods, SetTrendLegend, CloneTrendGroup,
    )
})
