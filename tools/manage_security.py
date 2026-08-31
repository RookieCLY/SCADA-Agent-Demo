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


# ============================================================ extension tools
class RotateEncryptionKeysArgs(BaseModel):
    action: Literal["rotate_encryption_keys"] = "rotate_encryption_keys"
    scope: Literal["at_rest", "in_transit", "all"] = "all"


class RotateEncryptionKeys(MockTool):
    name = "rotate_encryption_keys"
    domain = DOMAIN; action = "rotate_encryption_keys"
    description = "Rotate the encryption keys used for data-at-rest and/or in-transit."
    args_model = RotateEncryptionKeysArgs
    examples = ["轮换加密密钥", "rotate the data encryption keys", "更新传输加密的密钥"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return ["project_meta.security.encryption_keys"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: RotateEncryptionKeysArgs, world: object) -> ToolResult:
        return ok(data={"scope": args.scope, "rotated": True})


class ConfigureFirewallRuleArgs(BaseModel):
    action: Literal["configure_firewall_rule"] = "configure_firewall_rule"
    rule_id: str
    direction: Literal["inbound", "outbound"] = "inbound"
    protocol: Literal["tcp", "udp", "any"] = "tcp"
    port: int = Field(ge=1, le=65535)
    verb: Literal["allow", "deny"] = "allow"


class ConfigureFirewallRule(MockTool):
    name = "configure_firewall_rule"
    domain = DOMAIN; action = "configure_firewall_rule"
    description = "Add or update an OT-network firewall rule for the SCADA host."
    args_model = ConfigureFirewallRuleArgs
    examples = ["配置一条防火墙规则", "block inbound traffic on port 502", "只允许特定端口访问"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"project_meta.security.firewall.{args.rule_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ConfigureFirewallRuleArgs, world: object) -> ToolResult:
        return ok(data={"rule_id": args.rule_id, "verb": args.verb, "port": args.port})


class SetIpWhitelistArgs(BaseModel):
    action: Literal["set_ip_whitelist"] = "set_ip_whitelist"
    addresses: list[str] = Field(min_length=1, description="CIDR blocks or IPs allowed to connect")


class SetIpWhitelist(MockTool):
    name = "set_ip_whitelist"
    domain = DOMAIN; action = "set_ip_whitelist"
    description = "Restrict client connections to an explicit IP/CIDR whitelist."
    args_model = SetIpWhitelistArgs
    examples = ["设置 IP 白名单", "only allow the control room subnet to connect", "限制可访问的 IP 段"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return ["project_meta.security.ip_whitelist"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: SetIpWhitelistArgs, world: object) -> ToolResult:
        return ok(data={"count": len(args.addresses)})


class EnableTlsArgs(BaseModel):
    action: Literal["enable_tls"] = "enable_tls"
    min_version: Literal["1.2", "1.3"] = "1.2"


class EnableTls(MockTool):
    name = "enable_tls"
    domain = DOMAIN; action = "enable_tls"
    description = "Enable TLS on the SCADA server endpoints with a minimum protocol version."
    args_model = EnableTlsArgs
    examples = ["启用 TLS 加密", "enforce TLS 1.3 for the web HMI", "打开传输层加密"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return ["project_meta.security.tls"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: EnableTlsArgs, world: object) -> ToolResult:
        return ok(data={"tls_enabled": True, "min_version": args.min_version})


class ScanVulnerabilitiesArgs(BaseModel):
    action: Literal["scan_vulnerabilities"] = "scan_vulnerabilities"
    depth: Literal["quick", "full"] = "quick"


class ScanVulnerabilities(MockTool):
    name = "scan_vulnerabilities"
    domain = DOMAIN; action = "scan_vulnerabilities"
    description = "Run a security vulnerability scan against the SCADA configuration."
    args_model = ScanVulnerabilitiesArgs
    examples = ["做一次安全漏洞扫描", "run a quick vulnerability scan", "检查有没有安全隐患"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ScanVulnerabilitiesArgs, world: object) -> ToolResult:
        return ok(data={"depth": args.depth, "findings": [], "count": 0})


class ReviewAccessLogArgs(BaseModel):
    action: Literal["review_access_log"] = "review_access_log"
    last_n_hours: int = Field(default=24, ge=1, le=8760)
    only_failures: bool = False


class ReviewAccessLog(MockTool):
    name = "review_access_log"
    domain = DOMAIN; action = "review_access_log"
    description = "Review recent authentication / access-control log entries."
    args_model = ReviewAccessLogArgs
    examples = ["查看最近的访问日志", "show failed login attempts today", "审计一下谁登录过系统"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ReviewAccessLogArgs, world: object) -> ToolResult:
        return ok(data={"entries": [], "count": 0})


class ConfigureCertificateArgs(BaseModel):
    action: Literal["configure_certificate"] = "configure_certificate"
    cert_id: str
    common_name: str
    valid_days: int = Field(default=365, ge=1, le=3650)


class ConfigureCertificate(MockTool):
    name = "configure_certificate"
    domain = DOMAIN; action = "configure_certificate"
    description = "Install or update an X.509 server certificate."
    args_model = ConfigureCertificateArgs
    examples = ["配置服务器证书", "install a new TLS certificate", "更新 HMI 的数字证书"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"project_meta.security.certificates.{args.cert_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ConfigureCertificateArgs, world: object) -> ToolResult:
        return ok(data={"cert_id": args.cert_id, "common_name": args.common_name})


class RevokeCertificateArgs(BaseModel):
    action: Literal["revoke_certificate"] = "revoke_certificate"
    cert_id: str
    reason: str | None = None


class RevokeCertificate(MockTool):
    name = "revoke_certificate"
    domain = DOMAIN; action = "revoke_certificate"
    description = "Revoke a previously issued certificate."
    args_model = RevokeCertificateArgs
    examples = ["吊销一个证书", "revoke the compromised certificate", "作废旧的数字证书"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"project_meta.security.certificates.{args.cert_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"project_meta.security.certificates.{args.cert_id}"]

    def run(self, args: RevokeCertificateArgs, world: object) -> ToolResult:
        return ok(data={"cert_id": args.cert_id, "revoked": True})


class SetLoginBannerArgs(BaseModel):
    action: Literal["set_login_banner"] = "set_login_banner"
    text: str = Field(min_length=1)


class SetLoginBanner(MockTool):
    name = "set_login_banner"
    domain = DOMAIN; action = "set_login_banner"
    description = "Set the legal / warning banner shown on the login screen."
    args_model = SetLoginBannerArgs
    examples = ["设置登录页的警示语", "add a legal notice to the login screen", "配置登录横幅文字"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return ["project_meta.security.login_banner"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: SetLoginBannerArgs, world: object) -> ToolResult:
        return ok(data={"banner_set": True})


class EnforceRbacPolicyArgs(BaseModel):
    action: Literal["enforce_rbac_policy"] = "enforce_rbac_policy"
    strict: bool = True


class EnforceRbacPolicy(MockTool):
    name = "enforce_rbac_policy"
    domain = DOMAIN; action = "enforce_rbac_policy"
    description = "Turn on strict role-based access control enforcement for all tools."
    args_model = EnforceRbacPolicyArgs
    examples = ["启用严格的 RBAC 权限控制", "enforce role-based access strictly", "打开基于角色的访问控制"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return ["project_meta.security.rbac"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: EnforceRbacPolicyArgs, world: object) -> ToolResult:
        return ok(data={"rbac_strict": args.strict})


class ConfigureBackupScheduleArgs(BaseModel):
    action: Literal["configure_backup_schedule"] = "configure_backup_schedule"
    frequency: Literal["hourly", "daily", "weekly"] = "daily"
    retain_copies: int = Field(default=7, ge=1, le=365)


class ConfigureBackupSchedule(MockTool):
    name = "configure_backup_schedule"
    domain = DOMAIN; action = "configure_backup_schedule"
    description = "Schedule automatic project backups with a retention count."
    args_model = ConfigureBackupScheduleArgs
    examples = ["设置自动备份计划", "back up the project every day and keep 7 copies", "配置定时备份策略"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return ["project_meta.security.backup_schedule"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ConfigureBackupScheduleArgs, world: object) -> ToolResult:
        return ok(data={"frequency": args.frequency, "retain": args.retain_copies})


SECURITY_ACTIONS.update({
    cls.action: cls
    for cls in (
        RotateEncryptionKeys, ConfigureFirewallRule, SetIpWhitelist, EnableTls,
        ScanVulnerabilities, ReviewAccessLog, ConfigureCertificate, RevokeCertificate,
        SetLoginBanner, EnforceRbacPolicy, ConfigureBackupSchedule,
    )
})
