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


# ============================================================ extension tools
class TestDbConnectionArgs(BaseModel):
    action: Literal["test_db_connection"] = "test_db_connection"
    connection_id: str


class TestDbConnection(MockTool):
    name = "test_db_connection"
    domain = DOMAIN; action = "test_db_connection"
    description = "Test an external database connection and report reachability/latency."
    args_model = TestDbConnectionArgs
    examples = ["测试一下数据库连接", "check if the historian DB is reachable", "数据库连得上吗"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}"]

    def run(self, args: TestDbConnectionArgs, world: object) -> ToolResult:
        return ok(data={"connection_id": args.connection_id, "reachable": True})


class CloseDbConnectionArgs(BaseModel):
    action: Literal["close_db_connection"] = "close_db_connection"
    connection_id: str


class CloseDbConnection(MockTool):
    name = "close_db_connection"
    domain = DOMAIN; action = "close_db_connection"
    description = "Close and release an external database connection."
    args_model = CloseDbConnectionArgs
    examples = ["关闭数据库连接", "close the reporting DB connection", "断开外部数据库"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}.state"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}"]

    def run(self, args: CloseDbConnectionArgs, world: object) -> ToolResult:
        return ok(data={"connection_id": args.connection_id, "closed": True})


class ListDbTablesArgs(BaseModel):
    action: Literal["list_db_tables"] = "list_db_tables"
    connection_id: str
    schema_name: str | None = None


class ListDbTables(MockTool):
    name = "list_db_tables"
    domain = DOMAIN; action = "list_db_tables"
    description = "List the tables available on an external database connection."
    args_model = ListDbTablesArgs
    examples = ["列出数据库里的表", "show tables in the historian database", "看看这个库有哪些表"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}"]

    def run(self, args: ListDbTablesArgs, world: object) -> ToolResult:
        return ok(data={"connection_id": args.connection_id, "tables": [], "count": 0})


class DropDbTableArgs(BaseModel):
    action: Literal["drop_db_table"] = "drop_db_table"
    connection_id: str
    table_name: str


class DropDbTable(MockTool):
    name = "drop_db_table"
    domain = DOMAIN; action = "drop_db_table"
    description = "Drop a table from an external database (destructive)."
    args_model = DropDbTableArgs
    examples = ["删除数据库中的一张表", "drop the temporary staging table", "把这张表删掉"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}.tables.{args.table_name}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}"]

    def run(self, args: DropDbTableArgs, world: object) -> ToolResult:
        return ok(data={"table_name": args.table_name, "dropped": True})


class CreateDbIndexArgs(BaseModel):
    action: Literal["create_db_index"] = "create_db_index"
    connection_id: str
    table_name: str
    columns: list[str] = Field(min_length=1)
    unique: bool = False


class CreateDbIndex(MockTool):
    name = "create_db_index"
    domain = DOMAIN; action = "create_db_index"
    description = "Create an index on a database table to speed up queries."
    args_model = CreateDbIndexArgs
    examples = ["给这张表建个索引", "create an index on the timestamp column", "加个索引加速查询"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}.tables.{args.table_name}.index"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}"]

    def run(self, args: CreateDbIndexArgs, world: object) -> ToolResult:
        return ok(data={"table_name": args.table_name, "columns": len(args.columns)})


class SetDbRetentionDaysArgs(BaseModel):
    action: Literal["set_db_retention_days"] = "set_db_retention_days"
    connection_id: str
    table_name: str
    days: int = Field(ge=1, le=3650)


class SetDbRetentionDays(MockTool):
    name = "set_db_retention_days"
    domain = DOMAIN; action = "set_db_retention_days"
    description = "Set the row-retention window (days) for a database table's data purge job."
    args_model = SetDbRetentionDaysArgs
    examples = ["设置这张表的数据保留天数", "keep only 90 days of rows in this table", "超过保留期的数据自动清理"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}.tables.{args.table_name}.retention"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}"]

    def run(self, args: SetDbRetentionDaysArgs, world: object) -> ToolResult:
        return ok(data={"table_name": args.table_name, "days": args.days})


class ExportDbTableArgs(BaseModel):
    action: Literal["export_db_table"] = "export_db_table"
    connection_id: str
    table_name: str
    format: Literal["csv", "parquet", "json"] = "csv"


class ExportDbTable(MockTool):
    name = "export_db_table"
    domain = DOMAIN; action = "export_db_table"
    description = "Export a database table's rows to a file in the given format."
    args_model = ExportDbTableArgs
    examples = ["把这张表导出成 CSV", "export the alarm history table to parquet", "导出数据库表数据"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}"]

    def run(self, args: ExportDbTableArgs, world: object) -> ToolResult:
        return ok(data={"table_name": args.table_name, "format": args.format})


class ImportDbDataArgs(BaseModel):
    action: Literal["import_db_data"] = "import_db_data"
    connection_id: str
    table_name: str
    source_file: str


class ImportDbData(MockTool):
    name = "import_db_data"
    domain = DOMAIN; action = "import_db_data"
    description = "Bulk-import rows from a file into a database table."
    args_model = ImportDbDataArgs
    examples = ["把 CSV 导入到数据库表", "load this file into the staging table", "批量导入数据到库里"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}.tables.{args.table_name}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}"]

    def run(self, args: ImportDbDataArgs, world: object) -> ToolResult:
        return ok(data={"table_name": args.table_name, "imported": True})


class RunDbMaintenanceArgs(BaseModel):
    action: Literal["run_db_maintenance"] = "run_db_maintenance"
    connection_id: str
    task: Literal["vacuum", "reindex", "analyze"] = "vacuum"


class RunDbMaintenance(MockTool):
    name = "run_db_maintenance"
    domain = DOMAIN; action = "run_db_maintenance"
    description = "Run a maintenance task (vacuum / reindex / analyze) on a database."
    args_model = RunDbMaintenanceArgs
    examples = ["对数据库做一次维护", "vacuum the historian database", "重建索引优化数据库"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}"]

    def run(self, args: RunDbMaintenanceArgs, world: object) -> ToolResult:
        return ok(data={"connection_id": args.connection_id, "task": args.task})


class GetDbStatsArgs(BaseModel):
    action: Literal["get_db_stats"] = "get_db_stats"
    connection_id: str


class GetDbStats(MockTool):
    name = "get_db_stats"
    domain = DOMAIN; action = "get_db_stats"
    description = "Retrieve size / row-count / connection statistics for a database."
    args_model = GetDbStatsArgs
    examples = ["查看数据库的统计信息", "how big is the historian database", "数据库有多少行数据"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"databases.{args.connection_id}"]

    def run(self, args: GetDbStatsArgs, world: object) -> ToolResult:
        return ok(data={"connection_id": args.connection_id, "stats": {}})


DATABASE_ACTIONS.update({
    cls.action: cls
    for cls in (
        TestDbConnection, CloseDbConnection, ListDbTables, DropDbTable, CreateDbIndex,
        SetDbRetentionDays, ExportDbTable, ImportDbData, RunDbMaintenance, GetDbStats,
    )
})
