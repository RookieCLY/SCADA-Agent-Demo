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


# ============================================================ extension tools
class SetDriverPollingRateArgs(BaseModel):
    action: Literal["set_driver_polling_rate"] = "set_driver_polling_rate"
    driver_id: str
    interval_ms: int = Field(ge=10, le=600000)


class SetDriverPollingRate(MockTool):
    name = "set_driver_polling_rate"
    domain = DOMAIN; action = "set_driver_polling_rate"
    description = "Set how often a communication driver polls its devices."
    args_model = SetDriverPollingRateArgs
    examples = ["设置驱动的轮询周期", "poll this Modbus driver every 500ms", "调整采集频率"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}.polling_rate"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: SetDriverPollingRateArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "interval_ms": args.interval_ms})


class SetDriverTimeoutArgs(BaseModel):
    action: Literal["set_driver_timeout"] = "set_driver_timeout"
    driver_id: str
    timeout_ms: int = Field(ge=10, le=60000)
    retries: int = Field(default=3, ge=0, le=10)


class SetDriverTimeout(MockTool):
    name = "set_driver_timeout"
    domain = DOMAIN; action = "set_driver_timeout"
    description = "Configure a driver's request timeout and retry count."
    args_model = SetDriverTimeoutArgs
    examples = ["设置驱动的超时时间", "give the PLC 1s timeout with 3 retries", "配置通信超时重试"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}.timeout"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: SetDriverTimeoutArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "timeout_ms": args.timeout_ms})


class GetDriverDiagnosticsArgs(BaseModel):
    action: Literal["get_driver_diagnostics"] = "get_driver_diagnostics"
    driver_id: str


class GetDriverDiagnostics(MockTool):
    name = "get_driver_diagnostics"
    domain = DOMAIN; action = "get_driver_diagnostics"
    description = "Retrieve error counters and last-error diagnostics for a driver."
    args_model = GetDriverDiagnosticsArgs
    examples = ["查看驱动的诊断信息", "why is this driver dropping frames", "看看通信有没有报错"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: GetDriverDiagnosticsArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "errors": 0})


class EnableDriverRedundancyArgs(BaseModel):
    action: Literal["enable_driver_redundancy"] = "enable_driver_redundancy"
    driver_id: str
    backup_endpoint: str


class EnableDriverRedundancy(MockTool):
    name = "enable_driver_redundancy"
    domain = DOMAIN; action = "enable_driver_redundancy"
    description = "Enable a hot-standby backup endpoint for a communication driver."
    args_model = EnableDriverRedundancyArgs
    examples = ["为驱动启用冗余通道", "add a redundant link to the PLC", "配置通信主备切换"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}.redundancy"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: EnableDriverRedundancyArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "redundant": True})


class MapDriverAddressArgs(BaseModel):
    action: Literal["map_driver_address"] = "map_driver_address"
    driver_id: str
    tag: str
    address: str = Field(description="Protocol address, e.g. '40001' or 'ns=2;i=15'")


class MapDriverAddress(MockTool):
    name = "map_driver_address"
    domain = DOMAIN; action = "map_driver_address"
    description = "Map a SCADA tag to a protocol register/node address on a driver."
    args_model = MapDriverAddressArgs
    examples = ["把点位映射到寄存器地址", "map TEMP_101 to Modbus 40001", "配置标签的通信地址"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}.map.{args.tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: MapDriverAddressArgs, world: object) -> ToolResult:
        return ok(data={"tag": args.tag, "address": args.address})


class UnmapDriverAddressArgs(BaseModel):
    action: Literal["unmap_driver_address"] = "unmap_driver_address"
    driver_id: str
    tag: str


class UnmapDriverAddress(MockTool):
    name = "unmap_driver_address"
    domain = DOMAIN; action = "unmap_driver_address"
    description = "Remove a tag-to-address mapping from a driver."
    args_model = UnmapDriverAddressArgs
    examples = ["取消点位的地址映射", "unmap this tag from the driver", "删掉标签的通信地址"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}.map.{args.tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: UnmapDriverAddressArgs, world: object) -> ToolResult:
        return ok(data={"tag": args.tag, "unmapped": True})


class RestartDriverArgs(BaseModel):
    action: Literal["restart_driver"] = "restart_driver"
    driver_id: str


class RestartDriver(MockTool):
    name = "restart_driver"
    domain = DOMAIN; action = "restart_driver"
    description = "Restart a communication driver process to recover from a hung state."
    args_model = RestartDriverArgs
    examples = ["重启这个通信驱动", "restart the OPC UA driver", "驱动卡住了重启一下"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}.state"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: RestartDriverArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "restarted": True})


class SetByteOrderArgs(BaseModel):
    action: Literal["set_byte_order"] = "set_byte_order"
    driver_id: str
    byte_order: Literal["big_endian", "little_endian", "word_swap"] = "big_endian"


class SetByteOrder(MockTool):
    name = "set_byte_order"
    domain = DOMAIN; action = "set_byte_order"
    description = "Set the multi-register byte/word order for a driver's data decoding."
    args_model = SetByteOrderArgs
    examples = ["设置寄存器的字节序", "use word-swap for these 32-bit floats", "配置大小端"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}.byte_order"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: SetByteOrderArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "byte_order": args.byte_order})


class ScanDriverDevicesArgs(BaseModel):
    action: Literal["scan_driver_devices"] = "scan_driver_devices"
    driver_id: str


class ScanDriverDevices(MockTool):
    name = "scan_driver_devices"
    domain = DOMAIN; action = "scan_driver_devices"
    description = "Probe a driver's bus/network to discover connected devices."
    args_model = ScanDriverDevicesArgs
    examples = ["扫描总线上的设备", "discover devices on this Modbus line", "看看这个驱动能发现哪些设备"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: ScanDriverDevicesArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "devices": [], "count": 0})


class SetProtocolOptionsArgs(BaseModel):
    action: Literal["set_protocol_options"] = "set_protocol_options"
    driver_id: str
    options: dict[str, str] = Field(default_factory=dict)


class SetProtocolOptions(MockTool):
    name = "set_protocol_options"
    domain = DOMAIN; action = "set_protocol_options"
    description = "Set advanced protocol-specific options (function codes, security mode, etc.)."
    args_model = SetProtocolOptionsArgs
    examples = ["配置协议高级参数", "set the OPC UA security policy", "调整协议选项"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}.protocol_options"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: SetProtocolOptionsArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "options": len(args.options)})


class ExportDriverConfigArgs(BaseModel):
    action: Literal["export_driver_config"] = "export_driver_config"
    driver_id: str


class ExportDriverConfig(MockTool):
    name = "export_driver_config"
    domain = DOMAIN; action = "export_driver_config"
    description = "Export a driver's full configuration (mappings + options) to a file."
    args_model = ExportDriverConfigArgs
    examples = ["导出驱动配置", "export the Modbus driver config for backup", "把通信配置导出来"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"drivers.{args.driver_id}"]

    def run(self, args: ExportDriverConfigArgs, world: object) -> ToolResult:
        return ok(data={"driver_id": args.driver_id, "exported": True})


COMM_ACTIONS.update({
    cls.action: cls
    for cls in (
        SetDriverPollingRate, SetDriverTimeout, GetDriverDiagnostics, EnableDriverRedundancy,
        MapDriverAddress, UnmapDriverAddress, RestartDriver, SetByteOrder, ScanDriverDevices,
        SetProtocolOptions, ExportDriverConfig,
    )
})
