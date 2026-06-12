"""manage_devices — device registration and management stubs.

The demo uses MockWorld.devices for the device catalog; these stubs provide
realistic device-CRUD schemas for LLM evaluation without modifying world state.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import ErrorCode, MockTool, ToolResult, ok

DOMAIN = "manage_devices"


# ---------------------------------------------------------------- create_device
class CreateDeviceArgs(BaseModel):
    action: Literal["create_device"] = "create_device"
    device_id: str = Field(description="Unique device identifier, e.g. 'pump_3'")
    device_name: str = Field(description="Human-readable name, e.g. 'Feed Pump 3'")
    device_type: Literal["reactor", "pump", "tank", "heat_exchanger", "valve", "motor", "compressor", "sensor"] = "pump"
    tags: list[str] = Field(default_factory=list, description="Associated SCADA point tags")
    location: str | None = Field(default=None, description="Physical location or area")
    manufacturer: str | None = Field(default=None, description="Device manufacturer")
    model: str | None = Field(default=None, description="Device model number")


class CreateDevice(MockTool):
    name = "create_device"
    domain = DOMAIN
    action = "create_device"
    description = "Register a new device in the SCADA project device catalog."
    args_model = CreateDeviceArgs
    examples = [
        "添加一台新泵到设备列表",
        "register a new reactor device",
        "把进料泵加到工程里",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"points.{t}" for t in args.tags] if args.tags else []

    def run(self, args: CreateDeviceArgs, world: object) -> ToolResult:
        return ok(data={"device_id": args.device_id, "created": True})


# ---------------------------------------------------------------- update_device
class UpdateDeviceArgs(BaseModel):
    action: Literal["update_device"] = "update_device"
    device_id: str
    device_name: str | None = Field(default=None, description="New display name")
    location: str | None = Field(default=None, description="New location")
    tags: list[str] | None = Field(default=None, description="Replacement tag list")


class UpdateDevice(MockTool):
    name = "update_device"
    domain = DOMAIN
    action = "update_device"
    description = "Update an existing device's properties."
    args_model = UpdateDeviceArgs
    examples = [
        "修改泵的安装位置",
        "update device tags for pump_1",
        "更新设备信息",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: UpdateDeviceArgs, world: object) -> ToolResult:
        return ok(data={"device_id": args.device_id, "updated": True})


# ---------------------------------------------------------------- delete_device
class DeleteDeviceArgs(BaseModel):
    action: Literal["delete_device"] = "delete_device"
    device_id: str


class DeleteDevice(MockTool):
    name = "delete_device"
    domain = DOMAIN
    action = "delete_device"
    description = "Remove a device from the SCADA project catalog."
    args_model = DeleteDeviceArgs
    examples = [
        "删除闲置的设备",
        "remove device pump_3 from project",
        "卸载这台设备",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: DeleteDeviceArgs, world: object) -> ToolResult:
        return ok(data={"device_id": args.device_id, "deleted": True})


# ---------------------------------------------------------------- configure_device_params
class ConfigureDeviceParamsArgs(BaseModel):
    action: Literal["configure_device_params"] = "configure_device_params"
    device_id: str
    protocol: Literal["modbus", "opc_ua", "profibus", "ethernet_ip", "bacnet"] = "modbus"
    address: str = Field(default="127.0.0.1", description="Device network address")
    port: int = Field(default=502, ge=1, le=65535)
    polling_interval_ms: int = Field(default=1000, ge=100, description="Polling interval in milliseconds")
    timeout_ms: int = Field(default=3000, ge=100, le=30000)
    retry_count: int = Field(default=3, ge=0, le=10)


class ConfigureDeviceParams(MockTool):
    name = "configure_device_params"
    domain = DOMAIN
    action = "configure_device_params"
    description = "Configure communication and operational parameters for a device."
    args_model = ConfigureDeviceParamsArgs
    examples = [
        "配置设备的通讯参数",
        "set modbus address for pump_1",
        "把采集间隔改成500毫秒",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: ConfigureDeviceParamsArgs, world: object) -> ToolResult:
        return ok(data={"device_id": args.device_id, "configured": True})


# ---------------------------------------------------------------- get_device_status
class GetDeviceStatusArgs(BaseModel):
    action: Literal["get_device_status"] = "get_device_status"
    device_id: str


class GetDeviceStatus(MockTool):
    name = "get_device_status"
    domain = DOMAIN
    action = "get_device_status"
    description = "Query the current status and health of a device."
    args_model = GetDeviceStatusArgs
    examples = [
        "查看设备运行状态",
        "check if pump_1 is healthy",
        "设备在线吗",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: GetDeviceStatusArgs, world: object) -> ToolResult:
        return ok(data={"device_id": args.device_id, "status": "online", "health": "ok"})


# ---------------------------------------------------------------- list_devices
class ListDevicesArgs(BaseModel):
    action: Literal["list_devices"] = "list_devices"
    device_type: str | None = Field(default=None, description="Filter by device type")
    page_size: int = Field(default=50, ge=1, le=500)


class ListDevices(MockTool):
    name = "list_devices"
    domain = DOMAIN
    action = "list_devices"
    description = "List all registered devices, optionally filtered by type."
    args_model = ListDevicesArgs
    examples = [
        "列出所有设备",
        "show me all pumps",
        "工程里有哪些设备",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ListDevicesArgs, world: object) -> ToolResult:
        return ok(data={"devices": [], "count": 0})


# ---------------------------------------------------------------- registry hookup
DEVICE_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (CreateDevice, UpdateDevice, DeleteDevice, ConfigureDeviceParams, GetDeviceStatus, ListDevices)
}

ManageDevicesArgs = Annotated[
    Union[
        CreateDeviceArgs,
        UpdateDeviceArgs,
        DeleteDeviceArgs,
        ConfigureDeviceParamsArgs,
        GetDeviceStatusArgs,
        ListDevicesArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN",
    "DEVICE_ACTIONS",
    "ManageDevicesArgs",
    "CreateDevice", "CreateDeviceArgs",
    "UpdateDevice", "UpdateDeviceArgs",
    "DeleteDevice", "DeleteDeviceArgs",
    "ConfigureDeviceParams", "ConfigureDeviceParamsArgs",
    "GetDeviceStatus", "GetDeviceStatusArgs",
    "ListDevices", "ListDevicesArgs",
]
