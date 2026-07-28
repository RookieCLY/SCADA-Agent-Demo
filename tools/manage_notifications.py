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


# ============================================================ extension tools
class DeleteNotificationRuleArgs(BaseModel):
    action: Literal["delete_notification_rule"] = "delete_notification_rule"
    rule_id: str


class DeleteNotificationRule(MockTool):
    name = "delete_notification_rule"
    domain = DOMAIN; action = "delete_notification_rule"
    description = "Delete an alarm-notification rule."
    args_model = DeleteNotificationRuleArgs
    examples = ["删除一条通知规则", "delete the SMS notification rule", "移除某个报警通知"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]

    def run(self, args: DeleteNotificationRuleArgs, world: object) -> ToolResult:
        return ok(data={"rule_id": args.rule_id, "deleted": True})


class EnableNotificationRuleArgs(BaseModel):
    action: Literal["enable_notification_rule"] = "enable_notification_rule"
    rule_id: str


class EnableNotificationRule(MockTool):
    name = "enable_notification_rule"
    domain = DOMAIN; action = "enable_notification_rule"
    description = "Enable an alarm-notification rule so it starts firing."
    args_model = EnableNotificationRuleArgs
    examples = ["启用这条通知规则", "turn on the email alert rule", "打开报警通知"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}.enabled"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]

    def run(self, args: EnableNotificationRuleArgs, world: object) -> ToolResult:
        return ok(data={"rule_id": args.rule_id, "enabled": True})


class DisableNotificationRuleArgs(BaseModel):
    action: Literal["disable_notification_rule"] = "disable_notification_rule"
    rule_id: str


class DisableNotificationRule(MockTool):
    name = "disable_notification_rule"
    domain = DOMAIN; action = "disable_notification_rule"
    description = "Disable an alarm-notification rule."
    args_model = DisableNotificationRuleArgs
    examples = ["停用这条通知规则", "silence the email alerts for now", "先关掉报警通知"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}.enabled"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]

    def run(self, args: DisableNotificationRuleArgs, world: object) -> ToolResult:
        return ok(data={"rule_id": args.rule_id, "enabled": False})


class SetNotificationThrottleArgs(BaseModel):
    action: Literal["set_notification_throttle"] = "set_notification_throttle"
    rule_id: str
    max_per_hour: int = Field(default=10, ge=1, le=1000)


class SetNotificationThrottle(MockTool):
    name = "set_notification_throttle"
    domain = DOMAIN; action = "set_notification_throttle"
    description = "Rate-limit a notification rule to avoid alert storms."
    args_model = SetNotificationThrottleArgs
    examples = ["限制通知的发送频率", "cap this alert to 5 per hour", "避免报警刷屏"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}.throttle"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]

    def run(self, args: SetNotificationThrottleArgs, world: object) -> ToolResult:
        return ok(data={"rule_id": args.rule_id, "max_per_hour": args.max_per_hour})


class SetQuietHoursArgs(BaseModel):
    action: Literal["set_quiet_hours"] = "set_quiet_hours"
    rule_id: str
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=0, le=23)


class SetQuietHours(MockTool):
    name = "set_quiet_hours"
    domain = DOMAIN; action = "set_quiet_hours"
    description = "Suppress non-critical notifications during a daily quiet-hours window."
    args_model = SetQuietHoursArgs
    examples = ["设置免打扰时段", "no notifications between 22:00 and 06:00", "夜间不要发通知"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}.quiet_hours"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]

    def run(self, args: SetQuietHoursArgs, world: object) -> ToolResult:
        return ok(data={"rule_id": args.rule_id, "start": args.start_hour, "end": args.end_hour})


class AddNotificationRecipientArgs(BaseModel):
    action: Literal["add_notification_recipient"] = "add_notification_recipient"
    rule_id: str
    recipient: str = Field(description="Email / phone / user id to notify")


class AddNotificationRecipient(MockTool):
    name = "add_notification_recipient"
    domain = DOMAIN; action = "add_notification_recipient"
    description = "Add a recipient to a notification rule's distribution list."
    args_model = AddNotificationRecipientArgs
    examples = ["给通知规则加一个收件人", "also notify the on-call engineer", "把主管加到通知名单"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}.recipients"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]

    def run(self, args: AddNotificationRecipientArgs, world: object) -> ToolResult:
        return ok(data={"rule_id": args.rule_id, "recipient": args.recipient})


class RemoveNotificationRecipientArgs(BaseModel):
    action: Literal["remove_notification_recipient"] = "remove_notification_recipient"
    rule_id: str
    recipient: str


class RemoveNotificationRecipient(MockTool):
    name = "remove_notification_recipient"
    domain = DOMAIN; action = "remove_notification_recipient"
    description = "Remove a recipient from a notification rule's distribution list."
    args_model = RemoveNotificationRecipientArgs
    examples = ["从通知名单里去掉一个人", "stop notifying the former operator", "移除通知收件人"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}.recipients"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]

    def run(self, args: RemoveNotificationRecipientArgs, world: object) -> ToolResult:
        return ok(data={"rule_id": args.rule_id, "recipient": args.recipient})


class SetNotificationTemplateArgs(BaseModel):
    action: Literal["set_notification_template"] = "set_notification_template"
    rule_id: str
    template: str = Field(description="Message template, may include {{tag}} / {{value}} placeholders")


class SetNotificationTemplate(MockTool):
    name = "set_notification_template"
    domain = DOMAIN; action = "set_notification_template"
    description = "Set the message template used when a notification rule fires."
    args_model = SetNotificationTemplateArgs
    examples = ["设置通知的消息模板", "customize the alert text with the tag name", "改一下通知内容格式"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}.template"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.{args.rule_id}"]

    def run(self, args: SetNotificationTemplateArgs, world: object) -> ToolResult:
        return ok(data={"rule_id": args.rule_id, "template_set": True})


class AcknowledgeNotificationArgs(BaseModel):
    action: Literal["acknowledge_notification"] = "acknowledge_notification"
    notification_id: str
    operator: str | None = None


class AcknowledgeNotification(MockTool):
    name = "acknowledge_notification"
    domain = DOMAIN; action = "acknowledge_notification"
    description = "Acknowledge a delivered notification so escalation stops."
    args_model = AcknowledgeNotificationArgs
    examples = ["确认收到这条通知", "acknowledge the alert to stop escalation", "签收这条报警通知"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"notifications.delivered.{args.notification_id}.ack"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: AcknowledgeNotificationArgs, world: object) -> ToolResult:
        return ok(data={"notification_id": args.notification_id, "acknowledged": True})


class GetNotificationHistoryArgs(BaseModel):
    action: Literal["get_notification_history"] = "get_notification_history"
    rule_id: str | None = None
    last_n_days: int = Field(default=7, ge=1, le=365)


class GetNotificationHistory(MockTool):
    name = "get_notification_history"
    domain = DOMAIN; action = "get_notification_history"
    description = "Retrieve the history of notifications that have been sent."
    args_model = GetNotificationHistoryArgs
    examples = ["查看通知发送历史", "show notifications sent this week", "看看都发了哪些报警通知"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: GetNotificationHistoryArgs, world: object) -> ToolResult:
        return ok(data={"events": [], "count": 0})


NOTIFICATION_ACTIONS.update({
    cls.action: cls
    for cls in (
        DeleteNotificationRule, EnableNotificationRule, DisableNotificationRule,
        SetNotificationThrottle, SetQuietHours, AddNotificationRecipient,
        RemoveNotificationRecipient, SetNotificationTemplate, AcknowledgeNotification,
        GetNotificationHistory,
    )
})
