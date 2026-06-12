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


# ---------------------------------------------------------------- registry hookup
USER_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (CreateUser, AssignUserRole, SetUserPermissions, LockUser, ResetUserPassword, ConfigureSession, ListUsers)
}

ManageUsersArgs = Annotated[
    Union[
        CreateUserArgs, AssignUserRoleArgs, SetUserPermissionsArgs,
        LockUserArgs, ResetUserPasswordArgs, ConfigureSessionArgs, ListUsersArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN", "ManageUsersArgs", "USER_ACTIONS",
    "CreateUser", "AssignUserRole", "SetUserPermissions",
    "LockUser", "ResetUserPassword", "ConfigureSession", "ListUsers",
]
