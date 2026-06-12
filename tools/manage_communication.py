"""manage_communication — PLC/RTU communication driver & channel management stubs.

Industrial SCADA systems communicate with field devices via drivers (Modbus,
OPC UA, Profibus, etc.). These stubs model driver configuration, polling control,
and connection health.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import MockTool, ToolResult, ok

DOMAIN = "manage_communication"


# ---------------------------------------------------------------- configure_driver
class ConfigureDriverArgs(BaseModel):
    action: Literal["configure_driver"] = "configure_driver"
    driver_id: str = Field(description="Unique driver identifier, e.g. 'modbus_plc_1'")
    protocol: Literal["modbus", "opc_ua", "profibus", "ethernet_ip", "bacnet", "siemens_s7"] = "modbus"
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=502, ge=1, le=65535)
    unit_id: int = Field(default=1, ge=0, le=255, description="Modbus unit / slave ID")
    byte_order: Literal["big_endian", "little_endian", "swap_words"] = "big_endian"
    timeout_ms: int = Field(default=3000, ge=100, le=30000)
    retry_count: int = Field(default=3, ge=0, le=10)


class ConfigureDriver(MockTool):
    name = "configure_driver"
    domain = DOMAIN; action = "configure_driver"
    description = "Configure a communication driver for a field device."
    args_model = ConfigureDriverArgs
    examples = ["配置Modbus驱动", "add OPC UA driver for PLC", "设置通讯驱动参数"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ConfigureDriverArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "configured": True})


# ---------------------------------------------------------------- start_polling
class StartPollingArgs(BaseModel):
    action: Literal["start_polling"] = "start_polling"
    driver_id: str
    tags: list[str] = Field(default_factory=list, description="Specific points to poll; empty = all configured")
    interval_ms: int = Field(default=1000, ge=100, le=60000)


class StartPolling(MockTool):
    name = "start_polling"
    domain = DOMAIN; action = "start_polling"
    description = "Start polling a field device through the configured driver."
    args_model = StartPollingArgs
    examples = ["开始采集数据", "start polling PLC data", "开启通讯采集"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}.polling"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"] + [f"points.{t}" for t in args.tags] if args.tags else [f"drivers.{args.driver_id}"]

    def run(self, args: StartPollingArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "polling_started": True})


# ---------------------------------------------------------------- stop_polling
class StopPollingArgs(BaseModel):
    action: Literal["stop_polling"] = "stop_polling"
    driver_id: str


class StopPolling(MockTool):
    name = "stop_polling"
    domain = DOMAIN; action = "stop_polling"
    description = "Stop polling on a communication driver."
    args_model = StopPollingArgs
    examples = ["暂停通讯采集", "stop polling modbus driver", "停止数据采集"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}.polling"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: StopPollingArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "polling_stopped": True})


# ---------------------------------------------------------------- test_connection
class TestConnectionArgs(BaseModel):
    action: Literal["test_connection"] = "test_connection"
    driver_id: str


class TestConnection(MockTool):
    name = "test_connection"
    domain = DOMAIN; action = "test_connection"
    description = "Send a test command to verify communication with the field device."
    args_model = TestConnectionArgs
    examples = ["测试设备通讯", "ping the PLC", "检查通讯是否正常"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: TestConnectionArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "connected": True, "latency_ms": 12})


# ---------------------------------------------------------------- get_comm_stats
class GetCommStatsArgs(BaseModel):
    action: Literal["get_comm_stats"] = "get_comm_stats"
    driver_id: str


class GetCommStats(MockTool):
    name = "get_comm_stats"
    domain = DOMAIN; action = "get_comm_stats"
    description = "Retrieve communication statistics for a driver (error rate, throughput, uptime)."
    args_model = GetCommStatsArgs
    examples = ["查看通讯统计", "show communication error rate", "通讯质量怎么样"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: GetCommStatsArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "uptime_s": 86400, "error_rate": 0.001, "throughput_bytes_s": 1024, "packets_sent": 100000, "packets_lost": 0})


# ---------------------------------------------------------------- reset_driver
class ResetDriverArgs(BaseModel):
    action: Literal["reset_driver"] = "reset_driver"
    driver_id: str
    clear_counters: bool = False


class ResetDriver(MockTool):
    name = "reset_driver"
    domain = DOMAIN; action = "reset_driver"
    description = "Reset a communication driver (reinitialize connection)."
    args_model = ResetDriverArgs
    examples = ["重置通讯驱动", "restart modbus driver", "重新初始化通讯"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: ResetDriverArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "reset": True})


# ---------------------------------------------------------------- list_drivers
class ListDriversArgs(BaseModel):
    action: Literal["list_drivers"] = "list_drivers"
    protocol: str | None = None
    active_only: bool = False


class ListDrivers(MockTool):
    name = "list_drivers"
    domain = DOMAIN; action = "list_drivers"
    description = "List configured communication drivers, optionally filtered."
    args_model = ListDriversArgs
    examples = ["列出所有通讯驱动", "show all modbus drivers", "查看当前通讯配置"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ListDriversArgs, world: object) -> ToolResult:
        return ok(data={"drivers": [], "count": 0})


# ---------------------------------------------------------------- registry hookup
COMM_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (ConfigureDriver, StartPolling, StopPolling, TestConnection, GetCommStats, ResetDriver, ListDrivers)
}

ManageCommunicationArgs = Annotated[
    Union[
        ConfigureDriverArgs, StartPollingArgs, StopPollingArgs,
        TestConnectionArgs, GetCommStatsArgs, ResetDriverArgs, ListDriversArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN", "COMM_ACTIONS", "ManageCommunicationArgs",
    "ConfigureDriver", "StartPolling", "StopPolling",
    "TestConnection", "GetCommStats", "ResetDriver", "ListDrivers",
]
