"""manage_notifications — alarm notification & escalation rules stubs.

Configures who gets notified, through which channel (SMS, email, WeChat,
voice call), and under what escalation rules when alarms are not acknowledged.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import MockTool, ToolResult, ok

DOMAIN = "manage_notifications"


# ---------------------------------------------------------------- create_notification_rule
class CreateNotificationRuleArgs(BaseModel):
    action: Literal["create_notification_rule"] = "create_notification_rule"
    rule_id: str = Field(description="Unique rule identifier, e.g. 'critical_alarm_notify'")
    rule_name: str
    alarm_priority: list[Literal["high", "medium", "low"]] = Field(default_factory=lambda: ["high"])
    channels: list[Literal["email", "sms", "wechat", "voice", "app_push"]] = Field(default_factory=lambda: ["email"])
    recipients: list[str] = Field(default_factory=list, description="Usernames or external contacts")
    delay_s: float = Field(default=0.0, ge=0, description="Delay before sending notification")


class CreateNotificationRule(MockTool):
    name = "create_notification_rule"
    domain = DOMAIN; action = "create_notification_rule"
    description = "Create a notification rule that triggers when matching alarms fire."
    args_model = CreateNotificationRuleArgs
    examples = ["创建报警通知规则", "send SMS for critical alarms", "高优先级报警发邮件给工程师"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: CreateNotificationRuleArgs, world: object) -> ToolResult:
        return ok(data={"rule_id": args.rule_id, "created": True})


# ---------------------------------------------------------------- configure_notification_channel
class ConfigureNotificationChannelArgs(BaseModel):
    action: Literal["configure_notification_channel"] = "configure_notification_channel"
    channel: Literal["email", "sms", "wechat", "voice", "app_push"]
    enabled: bool = True
    config: dict[str, str] = Field(default_factory=dict, description="Channel-specific params, e.g. smtp_host, api_key")


class ConfigureNotificationChannel(MockTool):
    name = "configure_notification_channel"
    domain = DOMAIN; action = "configure_notification_channel"
    description = "Configure a notification delivery channel (SMTP for email, SMS gateway, etc.)."
    args_model = ConfigureNotificationChannelArgs
    examples = ["配置邮件服务器", "set up SMS gateway", "启用微信通知"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.channels.{args.channel}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ConfigureNotificationChannelArgs, world: object) -> ToolResult:
        return ok(data={"channel": args.channel, "configured": True})


# ---------------------------------------------------------------- add_escalation_level
class AddEscalationLevelArgs(BaseModel):
    action: Literal["add_escalation_level"] = "add_escalation_level"
    rule_id: str
    level: int = Field(default=1, ge=1, le=5)
    delay_min: float = Field(default=15.0, ge=0, description="Minutes to wait before escalating")
    recipients: list[str] = Field(default_factory=list)
    channels: list[Literal["email", "sms", "wechat", "voice", "app_push"]] = Field(default_factory=list)


class AddEscalationLevel(MockTool):
    name = "add_escalation_level"
    domain = DOMAIN; action = "add_escalation_level"
    description = "Add an escalation level to a notification rule (who to notify if unacknowledged)."
    args_model = AddEscalationLevelArgs
    examples = ["添加升级通知", "if not acknowledged in 15 min, call supervisor", "设置二级通知"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}.escalation.{args.level}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]

    def run(self, args: AddEscalationLevelArgs, world: object) -> ToolResult:
        return ok(data={"level": args.level, "added": True})


# ---------------------------------------------------------------- test_notification
class TestNotificationArgs(BaseModel):
    action: Literal["test_notification"] = "test_notification"
    rule_id: str
    recipient: str


class TestNotification(MockTool):
    name = "test_notification"
    domain = DOMAIN; action = "test_notification"
    description = "Send a test notification to verify the rule and channels are working."
    args_model = TestNotificationArgs
    examples = ["发送测试通知", "test alarm notification rule", "测试报警通知是否正常"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]

    def run(self, args: TestNotificationArgs, world: object) -> ToolResult:
        return ok(data={"sent": True, "recipient": args.recipient})


# ---------------------------------------------------------------- list_notification_rules
class ListNotificationRulesArgs(BaseModel):
    action: Literal["list_notification_rules"] = "list_notification_rules"
    enabled_only: bool = False


class ListNotificationRules(MockTool):
    name = "list_notification_rules"
    domain = DOMAIN; action = "list_notification_rules"
    description = "List all notification rules, optionally filtered to enabled only."
    args_model = ListNotificationRulesArgs
    examples = ["列出所有通知规则", "show me active notification rules", "查看报警通知配置"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ListNotificationRulesArgs, world: object) -> ToolResult:
        return ok(data={"rules": [], "count": 0})


# ---------------------------------------------------------------- registry hookup
NOTIFICATION_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (CreateNotificationRule, ConfigureNotificationChannel, AddEscalationLevel, TestNotification, ListNotificationRules)
}

ManageNotificationsArgs = Annotated[
    Union[
        CreateNotificationRuleArgs, ConfigureNotificationChannelArgs,
        AddEscalationLevelArgs, TestNotificationArgs, ListNotificationRulesArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN", "ManageNotificationsArgs", "NOTIFICATION_ACTIONS",
    "CreateNotificationRule", "ConfigureNotificationChannel",
    "AddEscalationLevel", "TestNotification", "ListNotificationRules",
]
