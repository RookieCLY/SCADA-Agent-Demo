"""manage_users — user account and role management stubs.

Covers user CRUD, role assignment, and permission configuration for
multi-operator SCADA environments.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import MockTool, ToolResult, ok

DOMAIN = "manage_users"


# ---------------------------------------------------------------- create_user
class CreateUserArgs(BaseModel):
    action: Literal["create_user"] = "create_user"
    username: str = Field(description="Login name, e.g. 'operator_zhang'")
    full_name: str = Field(description="Display name")
    role: Literal["operator", "engineer", "supervisor", "administrator", "viewer"] = "operator"
    department: str | None = None
    password: str = Field(default="changeme", description="Initial password (should be changed on first login)")


class CreateUser(MockTool):
    name = "create_user"
    domain = DOMAIN; action = "create_user"
    description = "Create a new user account in the SCADA system."
    args_model = CreateUserArgs
    examples = ["创建一个操作工账号", "add new operator user", "注册一个只读用户"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: CreateUserArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "created": True})


# ---------------------------------------------------------------- assign_user_role
class AssignUserRoleArgs(BaseModel):
    action: Literal["assign_user_role"] = "assign_user_role"
    username: str
    role: Literal["operator", "engineer", "supervisor", "administrator", "viewer"]


class AssignUserRole(MockTool):
    name = "assign_user_role"
    domain = DOMAIN; action = "assign_user_role"
    description = "Change a user's role (permission group)."
    args_model = AssignUserRoleArgs
    examples = ["把用户提升为工程师", "assign supervisor role to user", "修改用户权限组"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}.role"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: AssignUserRoleArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "role": args.role})


# ---------------------------------------------------------------- set_user_permissions
class SetUserPermissionsArgs(BaseModel):
    action: Literal["set_user_permissions"] = "set_user_permissions"
    username: str
    can_configure: bool = True
    can_deploy: bool = False
    can_acknowledge_alarms: bool = True
    can_edit_recipes: bool = False
    can_manage_users: bool = False
    can_export_data: bool = True


class SetUserPermissions(MockTool):
    name = "set_user_permissions"
    domain = DOMAIN; action = "set_user_permissions"
    description = "Configure fine-grained permissions for a specific user."
    args_model = SetUserPermissionsArgs
    examples = ["设置用户的操作权限", "allow operator to acknowledge alarms", "禁止用户修改配方"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}.permissions"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: SetUserPermissionsArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "permissions_updated": True})


# ---------------------------------------------------------------- lock_user
class LockUserArgs(BaseModel):
    action: Literal["lock_user"] = "lock_user"
    username: str
    reason: str | None = None


class LockUser(MockTool):
    name = "lock_user"
    domain = DOMAIN; action = "lock_user"
    description = "Lock a user account, preventing login."
    args_model = LockUserArgs
    examples = ["锁定用户账号", "disable operator account", "临时冻结用户"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}.status"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: LockUserArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "locked": True})


# ---------------------------------------------------------------- reset_user_password
class ResetUserPasswordArgs(BaseModel):
    action: Literal["reset_user_password"] = "reset_user_password"
    username: str
    new_password: str = Field(default="changeme", min_length=6)
    force_change_on_login: bool = True


class ResetUserPassword(MockTool):
    name = "reset_user_password"
    domain = DOMAIN; action = "reset_user_password"
    description = "Reset a user's password (admin action)."
    args_model = ResetUserPasswordArgs
    examples = ["重置用户密码", "reset password for operator", "强制用户下次登录改密码"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}.password"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: ResetUserPasswordArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "password_reset": True})


# ---------------------------------------------------------------- configure_session
class ConfigureSessionArgs(BaseModel):
    action: Literal["configure_session"] = "configure_session"
    username: str
    session_timeout_min: int = Field(default=30, ge=1, le=1440)
    max_concurrent_sessions: int = Field(default=1, ge=1, le=5)
    auto_lock_after_failures: int = Field(default=5, ge=1, le=20)


class ConfigureSession(MockTool):
    name = "configure_session"
    domain = DOMAIN; action = "configure_session"
    description = "Configure session timeout and login policies for a user."
    args_model = ConfigureSessionArgs
    examples = ["设置会话超时时间", "configure session timeout to 15 minutes", "限制用户最多一个会话"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}.session"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: ConfigureSessionArgs, world: object) -> ToolResult:
        return ok(data={"session_configured": True})


# ---------------------------------------------------------------- list_users
class ListUsersArgs(BaseModel):
    action: Literal["list_users"] = "list_users"
    role: str | None = None
    status: Literal["active", "locked", "all"] = "all"


class ListUsers(MockTool):
    name = "list_users"
    domain = DOMAIN; action = "list_users"
    description = "List user accounts, optionally filtered by role or status."
    args_model = ListUsersArgs
    examples = ["列出所有操作工", "show me active users", "查询当前有哪些用户登录"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ListUsersArgs, world: object) -> ToolResult:
        return ok(data={"users": [], "count": 0})


# ---------------------------------------------------------------- unlock_user
class UnlockUserArgs(BaseModel):
    action: Literal["unlock_user"] = "unlock_user"
    username: str


class UnlockUser(MockTool):
    name = "unlock_user"
    domain = DOMAIN; action = "unlock_user"
    description = "Unlock a previously locked user account so the operator can log in again."
    args_model = UnlockUserArgs
    examples = ["解锁用户账号", "unlock the operator account", "把冻结的用户恢复"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}.status"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: UnlockUserArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "locked": False})


# ---------------------------------------------------------------- delete_user
class DeleteUserArgs(BaseModel):
    action: Literal["delete_user"] = "delete_user"
    username: str


class DeleteUser(MockTool):
    name = "delete_user"
    domain = DOMAIN; action = "delete_user"
    description = "Permanently remove a user account from the SCADA system."
    args_model = DeleteUserArgs
    examples = ["删除这个用户", "delete the departed operator's account", "移除离职员工账号"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: DeleteUserArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "deleted": True})


# ---------------------------------------------------------------- update_user_profile
class UpdateUserProfileArgs(BaseModel):
    action: Literal["update_user_profile"] = "update_user_profile"
    username: str
    full_name: str | None = None
    department: str | None = None
    email: str | None = None
    phone: str | None = None


class UpdateUserProfile(MockTool):
    name = "update_user_profile"
    domain = DOMAIN; action = "update_user_profile"
    description = "Update a user's profile fields (display name, department, contact info)."
    args_model = UpdateUserProfileArgs
    examples = ["修改用户的部门", "update operator's contact email", "改一下用户的显示名"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}.profile"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: UpdateUserProfileArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "profile_updated": True})


# ---------------------------------------------------------------- create_role
class CreateRoleArgs(BaseModel):
    action: Literal["create_role"] = "create_role"
    role_name: str
    description: str | None = None
    inherits_from: str | None = Field(default=None, description="Optional base role to inherit permissions from")


class CreateRole(MockTool):
    name = "create_role"
    domain = DOMAIN; action = "create_role"
    description = "Define a new permission role (group) for RBAC access control."
    args_model = CreateRoleArgs
    examples = ["新建一个角色", "create a maintenance-technician role", "定义一个新的权限组"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"roles.{args.role_name}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"roles.{args.inherits_from}"] if args.inherits_from else []

    def run(self, args: CreateRoleArgs, world: object) -> ToolResult:
        return ok(data={"role_name": args.role_name, "created": True})


# ---------------------------------------------------------------- delete_role
class DeleteRoleArgs(BaseModel):
    action: Literal["delete_role"] = "delete_role"
    role_name: str


class DeleteRole(MockTool):
    name = "delete_role"
    domain = DOMAIN; action = "delete_role"
    description = "Remove a permission role; users holding it fall back to the default role."
    args_model = DeleteRoleArgs
    examples = ["删除一个角色", "delete the obsolete contractor role", "移除某个权限组"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"roles.{args.role_name}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"roles.{args.role_name}"]

    def run(self, args: DeleteRoleArgs, world: object) -> ToolResult:
        return ok(data={"role_name": args.role_name, "deleted": True})


# ---------------------------------------------------------------- list_user_permissions
class ListUserPermissionsArgs(BaseModel):
    action: Literal["list_user_permissions"] = "list_user_permissions"
    username: str


class ListUserPermissions(MockTool):
    name = "list_user_permissions"
    domain = DOMAIN; action = "list_user_permissions"
    description = "Show the effective permission set of a user (role + per-user overrides)."
    args_model = ListUserPermissionsArgs
    examples = ["查看用户的权限", "what can this operator do", "列出用户的有效权限"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: ListUserPermissionsArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "permissions": {}})


# ---------------------------------------------------------------- force_user_logout
class ForceUserLogoutArgs(BaseModel):
    action: Literal["force_user_logout"] = "force_user_logout"
    username: str
    reason: str | None = None


class ForceUserLogout(MockTool):
    name = "force_user_logout"
    domain = DOMAIN; action = "force_user_logout"
    description = "Terminate a user's active sessions immediately (e.g. shift handover)."
    args_model = ForceUserLogoutArgs
    examples = ["强制用户下线", "kick the operator out of all sessions", "结束某用户的登录会话"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}.session"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: ForceUserLogoutArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "sessions_terminated": True})


# ---------------------------------------------------------------- enable_two_factor_auth
class EnableTwoFactorAuthArgs(BaseModel):
    action: Literal["enable_two_factor_auth"] = "enable_two_factor_auth"
    username: str
    method: Literal["totp", "sms", "email"] = "totp"


class EnableTwoFactorAuth(MockTool):
    name = "enable_two_factor_auth"
    domain = DOMAIN; action = "enable_two_factor_auth"
    description = "Enable two-factor authentication for a specific user account."
    args_model = EnableTwoFactorAuthArgs
    examples = ["给管理员开启双因素认证", "enable 2FA for the supervisor", "让这个账号用手机验证码登录"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}.mfa"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: EnableTwoFactorAuthArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "mfa_method": args.method, "enabled": True})


# ---------------------------------------------------------------- get_user_activity
class GetUserActivityArgs(BaseModel):
    action: Literal["get_user_activity"] = "get_user_activity"
    username: str
    last_n_days: int = Field(default=7, ge=1, le=365)


class GetUserActivity(MockTool):
    name = "get_user_activity"
    domain = DOMAIN; action = "get_user_activity"
    description = "Retrieve a user's recent login / operation activity for audit."
    args_model = GetUserActivityArgs
    examples = ["查看用户最近的操作记录", "show this operator's activity last week", "调取用户登录历史"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"users.{args.username}"]

    def run(self, args: GetUserActivityArgs, world: object) -> ToolResult:
        return ok(data={"username": args.username, "events": [], "count": 0})


# ---------------------------------------------------------------- import_users_from_directory
class ImportUsersFromDirectoryArgs(BaseModel):
    action: Literal["import_users_from_directory"] = "import_users_from_directory"
    source: Literal["ldap", "active_directory", "csv"] = "ldap"
    default_role: Literal["operator", "engineer", "supervisor", "administrator", "viewer"] = "viewer"
    dry_run: bool = True


class ImportUsersFromDirectory(MockTool):
    name = "import_users_from_directory"
    domain = DOMAIN; action = "import_users_from_directory"
    description = "Bulk-import user accounts from an external directory (LDAP / AD / CSV)."
    args_model = ImportUsersFromDirectoryArgs
    examples = ["从 LDAP 批量导入用户", "sync users from active directory", "用 CSV 批量建账号"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return ["users"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ImportUsersFromDirectoryArgs, world: object) -> ToolResult:
        return ok(data={"source": args.source, "dry_run": args.dry_run, "imported": 0})


# ---------------------------------------------------------------- registry hookup
USER_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (
        CreateUser, AssignUserRole, SetUserPermissions, LockUser, ResetUserPassword,
        ConfigureSession, ListUsers, UnlockUser, DeleteUser, UpdateUserProfile,
        CreateRole, DeleteRole, ListUserPermissions, ForceUserLogout,
        EnableTwoFactorAuth, GetUserActivity, ImportUsersFromDirectory,
    )
}

ManageUsersArgs = Annotated[
    Union[
        CreateUserArgs, AssignUserRoleArgs, SetUserPermissionsArgs,
        LockUserArgs, ResetUserPasswordArgs, ConfigureSessionArgs, ListUsersArgs,
        UnlockUserArgs, DeleteUserArgs, UpdateUserProfileArgs, CreateRoleArgs,
        DeleteRoleArgs, ListUserPermissionsArgs, ForceUserLogoutArgs,
        EnableTwoFactorAuthArgs, GetUserActivityArgs, ImportUsersFromDirectoryArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN", "ManageUsersArgs", "USER_ACTIONS",
    "CreateUser", "AssignUserRole", "SetUserPermissions",
    "LockUser", "ResetUserPassword", "ConfigureSession", "ListUsers",
    "UnlockUser", "DeleteUser", "UpdateUserProfile", "CreateRole", "DeleteRole",
    "ListUserPermissions", "ForceUserLogout", "EnableTwoFactorAuth",
    "GetUserActivity", "ImportUsersFromDirectory",
]
