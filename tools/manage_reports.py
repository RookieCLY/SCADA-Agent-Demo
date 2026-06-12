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
