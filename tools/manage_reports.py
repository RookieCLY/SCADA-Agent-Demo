"""manage_reports — production & alarm report generation stubs.

Industrial SCADA systems produce shift reports, alarm summaries, and
compliance logs. These stubs model template-based report generation.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import MockTool, ToolResult, ok

DOMAIN = "manage_reports"


# ---------------------------------------------------------------- create_report_template
class CreateReportTemplateArgs(BaseModel):
    action: Literal["create_report_template"] = "create_report_template"
    template_id: str = Field(description="Unique template identifier, e.g. 'shift_report'")
    template_name: str
    report_type: Literal["shift", "alarm_summary", "production_log", "compliance", "custom"] = "shift"
    description: str | None = None


class CreateReportTemplate(MockTool):
    name = "create_report_template"
    domain = DOMAIN; action = "create_report_template"
    description = "Create a report template definition."
    args_model = CreateReportTemplateArgs
    examples = ["创建一个班报模板", "define shift report template", "新建报警汇总报表"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: CreateReportTemplateArgs, world: object) -> ToolResult:
        return ok(data={"template_id": args.template_id, "created": True})


# ---------------------------------------------------------------- add_report_section
class AddReportSectionArgs(BaseModel):
    action: Literal["add_report_section"] = "add_report_section"
    template_id: str
    section_id: str = Field(description="Unique section identifier, e.g. 'prod_summary'")
    section_title: str
    data_source: Literal["alarm_log", "history", "event_log", "points_snapshot", "production_counters"] = "history"
    tags: list[str] = Field(default_factory=list, description="SCADA tags to include in this section")
    aggregation: Literal["raw", "avg", "min", "max", "sum", "count", "last"] = "avg"


class AddReportSection(MockTool):
    name = "add_report_section"
    domain = DOMAIN; action = "add_report_section"
    description = "Add a data section to an existing report template."
    args_model = AddReportSectionArgs
    examples = ["在报表中添加产量统计", "add temperature summary section", "加入报警统计区块"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}.sections.{args.section_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"] + [f"points.{t}" for t in args.tags] if args.tags else [f"reports.{args.template_id}"]

    def run(self, args: AddReportSectionArgs, world: object) -> ToolResult:
        return ok(data={"section_id": args.section_id, "added": True})


# ---------------------------------------------------------------- configure_report_schedule
class ConfigureReportScheduleArgs(BaseModel):
    action: Literal["configure_report_schedule"] = "configure_report_schedule"
    template_id: str
    frequency: Literal["hourly", "daily", "weekly", "monthly", "on_shift_change", "manual"] = "daily"
    time_of_day: str | None = Field(default="08:00", description="HH:MM, e.g. '08:00'")
    day_of_week: str | None = Field(default=None, description="For weekly: 'mon'..'sun'")
    recipients: list[str] = Field(default_factory=list, description="Email or user list")


class ConfigureReportSchedule(MockTool):
    name = "configure_report_schedule"
    domain = DOMAIN; action = "configure_report_schedule"
    description = "Configure the generation schedule for a report template."
    args_model = ConfigureReportScheduleArgs
    examples = ["设置每天八点自动生成报表", "schedule weekly report on Monday", "配置报表定时任务"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}.schedule"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: ConfigureReportScheduleArgs, world: object) -> ToolResult:
        return ok(data={"schedule_configured": True})


# ---------------------------------------------------------------- generate_report
class GenerateReportArgs(BaseModel):
    action: Literal["generate_report"] = "generate_report"
    template_id: str
    time_range_start: str | None = Field(default=None, description="ISO datetime, e.g. '2026-06-12T00:00:00Z'")
    time_range_end: str | None = Field(default=None, description="ISO datetime")


class GenerateReport(MockTool):
    name = "generate_report"
    domain = DOMAIN; action = "generate_report"
    description = "Generate a report from a template for a specific time range."
    args_model = GenerateReportArgs
    examples = ["生成昨天的班报", "generate shift report for last 8 hours", "导出生产日报"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}.generated"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: GenerateReportArgs, world: object) -> ToolResult:
        return ok(data={"template_id": args.template_id, "generated": True, "report_url": f"/reports/{args.template_id}_latest.pdf"})


# ---------------------------------------------------------------- set_report_format
class SetReportFormatArgs(BaseModel):
    action: Literal["set_report_format"] = "set_report_format"
    template_id: str
    output_format: Literal["pdf", "csv", "xlsx", "html"] = "pdf"
    include_header: bool = True
    page_size: Literal["a4", "letter", "a3"] = "a4"
    orientation: Literal["portrait", "landscape"] = "portrait"


class SetReportFormat(MockTool):
    name = "set_report_format"
    domain = DOMAIN; action = "set_report_format"
    description = "Configure the output format and layout for a report template."
    args_model = SetReportFormatArgs
    examples = ["设置报表输出为PDF", "change report format to Excel", "报表用A4横版"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}.format"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: SetReportFormatArgs, world: object) -> ToolResult:
        return ok(data={"format_set": True})


# ---------------------------------------------------------------- export_report
class ExportReportArgs(BaseModel):
    action: Literal["export_report"] = "export_report"
    template_id: str
    destination: str = Field(default="/exports/", description="Export directory path")


class ExportReport(MockTool):
    name = "export_report"
    domain = DOMAIN; action = "export_report"
    description = "Export the latest generated report to a file location."
    args_model = ExportReportArgs
    examples = ["导出报表到文件", "export report to network share", "保存报表"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: ExportReportArgs, world: object) -> ToolResult:
        return ok(data={"template_id": args.template_id, "exported": True, "path": args.destination})


# ---------------------------------------------------------------- list_report_templates
class ListReportTemplatesArgs(BaseModel):
    action: Literal["list_report_templates"] = "list_report_templates"
    report_type: str | None = None


class ListReportTemplates(MockTool):
    name = "list_report_templates"
    domain = DOMAIN; action = "list_report_templates"
    description = "List report templates, optionally filtered by type."
    args_model = ListReportTemplatesArgs
    examples = ["列出所有报表模板", "show me shift report templates", "查看报表配置"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ListReportTemplatesArgs, world: object) -> ToolResult:
        return ok(data={"templates": [], "count": 0})


# ---------------------------------------------------------------- registry hookup
REPORT_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (CreateReportTemplate, AddReportSection, ConfigureReportSchedule, GenerateReport, SetReportFormat, ExportReport, ListReportTemplates)
}

ManageReportsArgs = Annotated[
    Union[
        CreateReportTemplateArgs, AddReportSectionArgs, ConfigureReportScheduleArgs,
        GenerateReportArgs, SetReportFormatArgs, ExportReportArgs, ListReportTemplatesArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN", "ManageReportsArgs", "REPORT_ACTIONS",
    "CreateReportTemplate", "AddReportSection", "ConfigureReportSchedule",
    "GenerateReport", "SetReportFormat", "ExportReport", "ListReportTemplates",
]


# ============================================================ extension tools
class DeleteReportTemplateArgs(BaseModel):
    action: Literal["delete_report_template"] = "delete_report_template"
    template_id: str


class DeleteReportTemplate(MockTool):
    name = "delete_report_template"
    domain = DOMAIN; action = "delete_report_template"
    description = "Delete a report template."
    args_model = DeleteReportTemplateArgs
    examples = ["删除一个报表模板", "delete the monthly report template", "移除旧的报表定义"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: DeleteReportTemplateArgs, world: object) -> ToolResult:
        return ok(data={"template_id": args.template_id, "deleted": True})


class CloneReportTemplateArgs(BaseModel):
    action: Literal["clone_report_template"] = "clone_report_template"
    source_template_id: str
    new_template_id: str


class CloneReportTemplate(MockTool):
    name = "clone_report_template"
    domain = DOMAIN; action = "clone_report_template"
    description = "Duplicate an existing report template under a new id."
    args_model = CloneReportTemplateArgs
    examples = ["复制一个报表模板", "clone the shift report as a daily report", "基于现有模板新建一个"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.new_template_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.source_template_id}"]

    def run(self, args: CloneReportTemplateArgs, world: object) -> ToolResult:
        return ok(data={"new_template_id": args.new_template_id})


class PreviewReportArgs(BaseModel):
    action: Literal["preview_report"] = "preview_report"
    template_id: str


class PreviewReport(MockTool):
    name = "preview_report"
    domain = DOMAIN; action = "preview_report"
    description = "Render a preview of a report without persisting or delivering it."
    args_model = PreviewReportArgs
    examples = ["预览一下这个报表", "preview the production report", "先看看报表长什么样"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: PreviewReportArgs, world: object) -> ToolResult:
        return ok(data={"template_id": args.template_id, "preview": "<rendered>"})


class EmailReportArgs(BaseModel):
    action: Literal["email_report"] = "email_report"
    template_id: str
    recipients: list[str] = Field(min_length=1)


class EmailReport(MockTool):
    name = "email_report"
    domain = DOMAIN; action = "email_report"
    description = "Generate a report and email it to a list of recipients."
    args_model = EmailReportArgs
    examples = ["把报表邮件发给主管", "email the daily report to the shift leads", "生成报表并发邮件"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: EmailReportArgs, world: object) -> ToolResult:
        return ok(data={"template_id": args.template_id, "sent_to": len(args.recipients)})


class ArchiveReportArgs(BaseModel):
    action: Literal["archive_report"] = "archive_report"
    report_id: str
    retention_days: int = Field(default=365, ge=1, le=3650)


class ArchiveReport(MockTool):
    name = "archive_report"
    domain = DOMAIN; action = "archive_report"
    description = "Archive a generated report instance with a retention window."
    args_model = ArchiveReportArgs
    examples = ["归档这份报表", "archive the generated report for a year", "把报表存档"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.archive.{args.report_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ArchiveReportArgs, world: object) -> ToolResult:
        return ok(data={"report_id": args.report_id, "archived": True})


class SetReportHeaderFooterArgs(BaseModel):
    action: Literal["set_report_header_footer"] = "set_report_header_footer"
    template_id: str
    header: str | None = None
    footer: str | None = None


class SetReportHeaderFooter(MockTool):
    name = "set_report_header_footer"
    domain = DOMAIN; action = "set_report_header_footer"
    description = "Set the header and footer text of a report template (e.g. company name, page numbers)."
    args_model = SetReportHeaderFooterArgs
    examples = ["设置报表的页眉页脚", "add the company logo text to the report header", "报表底部加页码"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}.header_footer"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: SetReportHeaderFooterArgs, world: object) -> ToolResult:
        return ok(data={"template_id": args.template_id, "updated": True})


class AddReportChartArgs(BaseModel):
    action: Literal["add_report_chart"] = "add_report_chart"
    template_id: str
    chart_id: str
    chart_type: Literal["line", "bar", "pie"] = "line"
    tags: list[str] = Field(default_factory=list)


class AddReportChart(MockTool):
    name = "add_report_chart"
    domain = DOMAIN; action = "add_report_chart"
    description = "Add a chart element (line/bar/pie over tag data) to a report template."
    args_model = AddReportChartArgs
    examples = ["在报表里加一张趋势图", "add a bar chart of daily output to the report", "报表插入一个饼图"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}.charts.{args.chart_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: AddReportChartArgs, world: object) -> ToolResult:
        return ok(data={"chart_id": args.chart_id, "chart_type": args.chart_type})


class AddReportTableArgs(BaseModel):
    action: Literal["add_report_table"] = "add_report_table"
    template_id: str
    table_id: str
    columns: list[str] = Field(min_length=1)


class AddReportTable(MockTool):
    name = "add_report_table"
    domain = DOMAIN; action = "add_report_table"
    description = "Add a data table element with the given columns to a report template."
    args_model = AddReportTableArgs
    examples = ["在报表里加一张数据表", "add a table of alarm counts to the report", "报表插入一个表格"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}.tables.{args.table_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: AddReportTableArgs, world: object) -> ToolResult:
        return ok(data={"table_id": args.table_id, "columns": len(args.columns)})


class SetReportDataSourceArgs(BaseModel):
    action: Literal["set_report_data_source"] = "set_report_data_source"
    template_id: str
    source: Literal["historian", "realtime", "database"] = "historian"


class SetReportDataSource(MockTool):
    name = "set_report_data_source"
    domain = DOMAIN; action = "set_report_data_source"
    description = "Choose where a report pulls its data from (historian / realtime / external DB)."
    args_model = SetReportDataSourceArgs
    examples = ["设置报表的数据来源", "make the report read from the historian", "报表数据取自实时库"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}.data_source"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: SetReportDataSourceArgs, world: object) -> ToolResult:
        return ok(data={"template_id": args.template_id, "source": args.source})


class ScheduleReportDeliveryArgs(BaseModel):
    action: Literal["schedule_report_delivery"] = "schedule_report_delivery"
    template_id: str
    cron: str = Field(description="Cron expression for automatic delivery")
    channel: Literal["email", "ftp", "shared_folder"] = "email"


class ScheduleReportDelivery(MockTool):
    name = "schedule_report_delivery"
    domain = DOMAIN; action = "schedule_report_delivery"
    description = "Automatically generate and deliver a report on a cron schedule."
    args_model = ScheduleReportDeliveryArgs
    examples = ["设置报表自动定时下发", "auto-send the report every Monday 8am", "每天定时把报表发到共享目录"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}.delivery"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"reports.{args.template_id}"]

    def run(self, args: ScheduleReportDeliveryArgs, world: object) -> ToolResult:
        return ok(data={"template_id": args.template_id, "cron": args.cron, "channel": args.channel})


REPORT_ACTIONS.update({
    cls.action: cls
    for cls in (
        DeleteReportTemplate, CloneReportTemplate, PreviewReport, EmailReport,
        ArchiveReport, SetReportHeaderFooter, AddReportChart, AddReportTable,
        SetReportDataSource, ScheduleReportDelivery,
    )
})
