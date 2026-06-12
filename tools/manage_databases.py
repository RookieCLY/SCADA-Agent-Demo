"""manage_databases — historian/relational database connection and management stubs.

Larger SCADA installations use SQL databases for long-term archiving, batch
records, and custom reporting. These stubs model database configuration and
data operations.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import MockTool, ToolResult, ok

DOMAIN = "manage_databases"


# ---------------------------------------------------------------- create_database_table
class CreateDatabaseTableArgs(BaseModel):
    action: Literal["create_database_table"] = "create_database_table"
    table_name: str = Field(description="Table name, e.g. 'batch_records'")
    database_name: str = Field(default="scada_historian", description="Target database")
    columns: list[dict[str, str]] = Field(default_factory=list, description="List of {name, type} dicts")
    primary_key: str | None = None


class CreateDatabaseTable(MockTool):
    name = "create_database_table"
    domain = DOMAIN; action = "create_database_table"
    description = "Create a new table in a connected database."
    args_model = CreateDatabaseTableArgs
    examples = ["创建一个数据库表", "create batch_records table", "在数据库里建表"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.database_name}.tables.{args.table_name}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.database_name}"]

    def run(self, args: CreateDatabaseTableArgs, world: object) -> ToolResult:
        return ok(data={"table_name": args.table_name, "created": True})


# ---------------------------------------------------------------- configure_database_connection
class ConfigureDatabaseConnectionArgs(BaseModel):
    action: Literal["configure_database_connection"] = "configure_database_connection"
    database_name: str = Field(default="scada_historian", description="Logical name for this connection")
    db_type: Literal["sqlite", "postgresql", "mssql", "mysql", "oracle"] = "postgresql"
    host: str = "127.0.0.1"
    port: int = 5432
    username: str = "scada"
    password: str = Field(default="", description="DB password")
    max_connections: int = Field(default=10, ge=1, le=200)


class ConfigureDatabaseConnection(MockTool):
    name = "configure_database_connection"
    domain = DOMAIN; action = "configure_database_connection"
    description = "Configure a connection to an external database."
    args_model = ConfigureDatabaseConnectionArgs
    examples = ["配置数据库连接", "connect to PostgreSQL historian", "设置历史数据库"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.database_name}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ConfigureDatabaseConnectionArgs, world: object) -> ToolResult:
        return ok(data={"database_name": args.database_name, "connected": True})


# ---------------------------------------------------------------- execute_sql_query
class ExecuteSqlQueryArgs(BaseModel):
    action: Literal["execute_sql_query"] = "execute_sql_query"
    database_name: str = "scada_historian"
    query: str = Field(description="SQL query string")
    max_rows: int = Field(default=1000, ge=1, le=100000)
    timeout_s: float = Field(default=30.0, ge=1.0, le=300.0)


class ExecuteSqlQuery(MockTool):
    name = "execute_sql_query"
    domain = DOMAIN; action = "execute_sql_query"
    description = "Execute a SQL query against a configured database (read-only for non-admin)."
    args_model = ExecuteSqlQueryArgs
    examples = ["查询生产数据", "SELECT * FROM batch_records", "执行SQL查询"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.database_name}"]

    def run(self, args: ExecuteSqlQueryArgs, world: object) -> ToolResult:
        return ok(data={"database_name": args.database_name, "rows": [], "row_count": 0, "execution_time_ms": 5})


# ---------------------------------------------------------------- set_retention_policy
class SetRetentionPolicyArgs(BaseModel):
    action: Literal["set_retention_policy"] = "set_retention_policy"
    database_name: str = "scada_historian"
    table_name: str
    retention_days: int = Field(default=365, ge=1, le=3650)
    archive_before_delete: bool = True


class SetRetentionPolicy(MockTool):
    name = "set_retention_policy"
    domain = DOMAIN; action = "set_retention_policy"
    description = "Set data retention policy (auto-purge old records) for a database table."
    args_model = SetRetentionPolicyArgs
    examples = ["设置数据保留一年", "set 90-day retention on events", "配置数据保留策略"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.database_name}.tables.{args.table_name}.retention"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.database_name}.tables.{args.table_name}"]

    def run(self, args: SetRetentionPolicyArgs, world: object) -> ToolResult:
        return ok(data={"retention_set": True, "retention_days": args.retention_days})


# ---------------------------------------------------------------- list_databases
class ListDatabasesArgs(BaseModel):
    action: Literal["list_databases"] = "list_databases"
    db_type: str | None = None


class ListDatabases(MockTool):
    name = "list_databases"
    domain = DOMAIN; action = "list_databases"
    description = "List all configured database connections."
    args_model = ListDatabasesArgs
    examples = ["列出所有数据库连接", "show configured databases", "查看数据库配置"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ListDatabasesArgs, world: object) -> ToolResult:
        return ok(data={"databases": [], "count": 0})


# ---------------------------------------------------------------- registry hookup
DATABASE_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (CreateDatabaseTable, ConfigureDatabaseConnection, ExecuteSqlQuery, SetRetentionPolicy, ListDatabases)
}

ManageDatabasesArgs = Annotated[
    Union[
        CreateDatabaseTableArgs, ConfigureDatabaseConnectionArgs,
        ExecuteSqlQueryArgs, SetRetentionPolicyArgs, ListDatabasesArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN", "DATABASE_ACTIONS", "ManageDatabasesArgs",
    "CreateDatabaseTable", "ConfigureDatabaseConnection",
    "ExecuteSqlQuery", "SetRetentionPolicy", "ListDatabases",
]
