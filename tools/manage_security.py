"""manage_security — audit log, backup, and security policy stubs.

Covers security-related operations common in regulated industries:
audit trail configuration, backup/restore, password policies, and
compliance checks.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import MockTool, ToolResult, ok

DOMAIN = "manage_security"


# ---------------------------------------------------------------- configure_audit_log
class ConfigureAuditLogArgs(BaseModel):
    action: Literal["configure_audit_log"] = "configure_audit_log"
    log_level: Literal["none", "errors_only", "changes", "all"] = "changes"
    log_operator_actions: bool = True
    log_alarm_acknowledgements: bool = True
    log_configuration_changes: bool = True
    log_login_attempts: bool = True


class ConfigureAuditLog(MockTool):
    name = "configure_audit_log"
    domain = DOMAIN; action = "configure_audit_log"
    description = "Configure the system audit log verbosity and scope."
    args_model = ConfigureAuditLogArgs
    examples = ["开启操作审计", "enable full audit logging", "记录所有配置变更"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return ["audit.config"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ConfigureAuditLogArgs, world: object) -> ToolResult:
        return ok(data={"audit_configured": True})


# ---------------------------------------------------------------- export_audit_trail
class ExportAuditTrailArgs(BaseModel):
    action: Literal["export_audit_trail"] = "export_audit_trail"
    start_time: str = Field(default="2026-06-01T00:00:00Z", description="ISO datetime")
    end_time: str = Field(default="2026-06-12T23:59:59Z", description="ISO datetime")
    format: Literal["csv", "pdf", "json"] = "csv"
    filter_user: str | None = None
    filter_action: str | None = None


class ExportAuditTrail(MockTool):
    name = "export_audit_trail"
    domain = DOMAIN; action = "export_audit_trail"
    description = "Export the audit trail for a given time range."
    args_model = ExportAuditTrailArgs
    examples = ["导出审计日志", "export audit trail for last month", "导出操作记录"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ExportAuditTrailArgs, world: object) -> ToolResult:
        return ok(data={"exported": True, "file": "/audit/audit_trail_export.csv", "entries": 0})


# ---------------------------------------------------------------- set_password_policy
class SetPasswordPolicyArgs(BaseModel):
    action: Literal["set_password_policy"] = "set_password_policy"
    min_length: int = Field(default=8, ge=4, le=64)
    require_uppercase: bool = True
    require_lowercase: bool = True
    require_digits: bool = True
    require_special_chars: bool = False
    max_age_days: int = Field(default=90, ge=1, le=365)
    prevent_reuse_count: int = Field(default=5, ge=0, le=20)


class SetPasswordPolicy(MockTool):
    name = "set_password_policy"
    domain = DOMAIN; action = "set_password_policy"
    description = "Configure the system-wide password strength and expiry policy."
    args_model = SetPasswordPolicyArgs
    examples = ["设置密码策略", "require strong passwords", "每90天强制改密"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return ["security.password_policy"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: SetPasswordPolicyArgs, world: object) -> ToolResult:
        return ok(data={"password_policy_updated": True})


# ---------------------------------------------------------------- check_compliance
class CheckComplianceArgs(BaseModel):
    action: Literal["check_compliance"] = "check_compliance"
    standard: Literal["isa95", "iec62443", "isa88", "cfr21_part11", "custom"] = "iec62443"
    generate_report: bool = True


class CheckCompliance(MockTool):
    name = "check_compliance"
    domain = DOMAIN; action = "check_compliance"
    description = "Check the current project against a compliance standard (ISA-95, IEC 62443, etc.)."
    args_model = CheckComplianceArgs
    examples = ["检查是否符合IEC62443", "run compliance check", "合规性审查"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: CheckComplianceArgs, world: object) -> ToolResult:
        return ok(data={"compliant": True, "issues": [], "standard": args.standard})


# ---------------------------------------------------------------- backup_project
class BackupProjectArgs(BaseModel):
    action: Literal["backup_project"] = "backup_project"
    backup_name: str = Field(default="auto_backup", description="Label for this backup")
    include_history: bool = True
    include_audit_log: bool = False
    destination: str = Field(default="/backups/", description="Backup directory path")


class BackupProject(MockTool):
    name = "backup_project"
    domain = DOMAIN; action = "backup_project"
    description = "Create a full project backup including configuration and optionally history data."
    args_model = BackupProjectArgs
    examples = ["备份整个工程", "create project backup", "备份到网络存储"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"backups.{args.backup_name}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: BackupProjectArgs, world: object) -> ToolResult:
        return ok(data={"backup_name": args.backup_name, "backed_up": True, "size_bytes": 1048576})


# ---------------------------------------------------------------- restore_project
class RestoreProjectArgs(BaseModel):
    action: Literal["restore_project"] = "restore_project"
    backup_name: str
    confirm: bool = Field(default=False, description="Must be set to true to proceed — safety gate")


class RestoreProject(MockTool):
    name = "restore_project"
    domain = DOMAIN; action = "restore_project"
    description = "Restore a project from a previous backup (requires confirmation)."
    args_model = RestoreProjectArgs
    examples = ["从备份恢复工程", "restore project from last backup", "回滚到昨天的备份"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"backups.{args.backup_name}"]

    def run(self, args: RestoreProjectArgs, world: object) -> ToolResult:
        if not args.confirm:
            return ok(data={"restored": False, "reason": "confirmation required — set confirm=true"})
        return ok(data={"backup_name": args.backup_name, "restored": True})


# ---------------------------------------------------------------- registry hookup
SECURITY_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (ConfigureAuditLog, ExportAuditTrail, SetPasswordPolicy, CheckCompliance, BackupProject, RestoreProject)
}

ManageSecurityArgs = Annotated[
    Union[
        ConfigureAuditLogArgs, ExportAuditTrailArgs, SetPasswordPolicyArgs,
        CheckComplianceArgs, BackupProjectArgs, RestoreProjectArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN", "ManageSecurityArgs", "SECURITY_ACTIONS",
    "ConfigureAuditLog", "ExportAuditTrail", "SetPasswordPolicy",
    "CheckCompliance", "BackupProject", "RestoreProject",
]
