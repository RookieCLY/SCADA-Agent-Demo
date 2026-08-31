"""manage_devices — device registration and management.

These were stubs: all 20 tools validated their arguments, returned ``ok`` and
left ``MockWorld.devices`` untouched, while ``create_device`` reported
``created: True`` and ``delete_device`` reported ``deleted: True``. That is the
worst failure shape available to a trace, because a silent no-op and a correct
write produce byte-identical successful calls — the only way to tell them apart
is the absent ``world_diff``, which nothing was checking.

It also broke the §4.7 safety probe: the cage denied bulk deletes in domains
where nothing could be deleted, so "the policy prevented a mutation" was not
demonstrable on 19 of 22 probe cases. ``devices`` is a real world collection, so
unlike the prop domains (users, recipes, schedules, …) there was somewhere to
write all along.

Three tools still return ``ok`` without a diff, and that is correct rather than
a leftover: ``reset_device`` clears latched faults, ``get_*`` read, and
``export_device_config`` writes a file outside the world model. None of them
claims otherwise — their ``intended_entities`` return ``[]``.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import ErrorCode, MockTool, ToolResult, fail, ok
from world import MockWorld
from world.models import Device

DOMAIN = "manage_devices"


def _diff(device: Device) -> dict[str, object]:
    return {"added_or_modified": {f"devices.{device.id}": device.model_dump()}, "removed": []}


def _missing(device_id: str) -> ToolResult:
    return fail(ErrorCode.DEVICE_NOT_FOUND, f"device {device_id} not found")


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

    def run(self, args: CreateDeviceArgs, world: MockWorld) -> ToolResult:
        if args.device_id in world.devices:
            return fail(ErrorCode.ALREADY_EXISTS, f"device {args.device_id} already exists")
        # Deliberately does *not* require the tags to exist yet: registering the
        # device before its points is the normal engineering order. Linking to
        # points that must already exist is link_device_points' job.
        device = Device(
            id=args.device_id,
            name=args.device_name,
            type=args.device_type,
            tags=list(args.tags),
            location=args.location,
            manufacturer=args.manufacturer,
            model=args.model,
        )
        world.devices[args.device_id] = device
        return ok(data={"device_id": args.device_id, "created": True}, world_diff=_diff(device))


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

    def run(self, args: UpdateDeviceArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        if args.device_name is not None:
            device.name = args.device_name
        if args.location is not None:
            device.location = args.location
        if args.tags is not None:
            device.tags = list(args.tags)
        return ok(data={"device_id": args.device_id, "updated": True}, world_diff=_diff(device))


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

    def run(self, args: DeleteDeviceArgs, world: MockWorld) -> ToolResult:
        if args.device_id not in world.devices:
            return _missing(args.device_id)
        del world.devices[args.device_id]
        return ok(
            data={"device_id": args.device_id, "deleted": True},
            world_diff={"added_or_modified": {}, "removed": [f"devices.{args.device_id}"]},
        )


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

    def run(self, args: ConfigureDeviceParamsArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        device.protocol = args.protocol
        device.address = args.address
        device.port = args.port
        device.polling_interval_ms = args.polling_interval_ms
        device.timeout_ms = args.timeout_ms
        device.retry_count = args.retry_count
        return ok(data={"device_id": args.device_id, "configured": True}, world_diff=_diff(device))


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

    def run(self, args: GetDeviceStatusArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        return ok(data={
            "device_id": device.id,
            "status": "online" if device.enabled else "out_of_service",
            "health": "ok",
            "type": device.type,
        })


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

    def run(self, args: ListDevicesArgs, world: MockWorld) -> ToolResult:
        matches = [
            d for d in world.devices.values()
            if args.device_type is None or d.type == args.device_type
        ]
        page = matches[: args.page_size]
        return ok(data={
            "devices": [{"device_id": d.id, "name": d.name, "type": d.type, "enabled": d.enabled} for d in page],
            "count": len(page),
            "total": len(matches),
        })


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


# ============================================================ extension tools
class CloneDeviceArgs(BaseModel):
    action: Literal["clone_device"] = "clone_device"
    source_device_id: str
    new_device_id: str


class CloneDevice(MockTool):
    name = "clone_device"
    domain = DOMAIN; action = "clone_device"
    description = "Duplicate a device (params + point links) under a new id."
    args_model = CloneDeviceArgs
    examples = ["复制一个设备", "clone reactor_1 as reactor_2", "照着现有设备再建一个"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.new_device_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.source_device_id}"]

    def run(self, args: CloneDeviceArgs, world: MockWorld) -> ToolResult:
        source = world.devices.get(args.source_device_id)
        if source is None:
            return _missing(args.source_device_id)
        if args.new_device_id in world.devices:
            return fail(ErrorCode.ALREADY_EXISTS, f"device {args.new_device_id} already exists")
        clone = source.model_copy(deep=True, update={"id": args.new_device_id})
        world.devices[args.new_device_id] = clone
        return ok(data={"new_device_id": args.new_device_id}, world_diff=_diff(clone))


class MoveDeviceArgs(BaseModel):
    action: Literal["move_device"] = "move_device"
    device_id: str
    target_area: str


class MoveDevice(MockTool):
    name = "move_device"
    domain = DOMAIN; action = "move_device"
    description = "Move a device to a different plant area / hierarchy node."
    args_model = MoveDeviceArgs
    examples = ["把设备移到另一个区域", "move this pump to the utilities area", "调整设备所属区域"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}.area"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: MoveDeviceArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        device.area_id = args.target_area
        return ok(data={"device_id": args.device_id, "area": args.target_area}, world_diff=_diff(device))


class SetDeviceTemplateArgs(BaseModel):
    action: Literal["set_device_template"] = "set_device_template"
    device_id: str
    template_id: str


class SetDeviceTemplate(MockTool):
    name = "set_device_template"
    domain = DOMAIN; action = "set_device_template"
    description = "Apply a device template (faceplate + param set) to a device instance."
    args_model = SetDeviceTemplateArgs
    examples = ["给设备套用一个模板", "apply the standard pump template", "让设备使用设备类型模板"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}.template"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: SetDeviceTemplateArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        device.template_id = args.template_id
        return ok(data={"device_id": args.device_id, "template_id": args.template_id}, world_diff=_diff(device))


class GetDeviceHealthArgs(BaseModel):
    action: Literal["get_device_health"] = "get_device_health"
    device_id: str


class GetDeviceHealth(MockTool):
    name = "get_device_health"
    domain = DOMAIN; action = "get_device_health"
    description = "Report a device's health score (comms, faults, maintenance due)."
    args_model = GetDeviceHealthArgs
    examples = ["查看设备的健康状态", "how healthy is this pump", "设备状态好不好"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: GetDeviceHealthArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        return ok(data={"device_id": device.id, "health": "ok" if device.enabled else "out_of_service"})


class CalibrateDeviceArgs(BaseModel):
    action: Literal["calibrate_device"] = "calibrate_device"
    device_id: str
    reference_value: float


class CalibrateDevice(MockTool):
    name = "calibrate_device"
    domain = DOMAIN; action = "calibrate_device"
    description = "Record a calibration adjustment for a device's sensor."
    args_model = CalibrateDeviceArgs
    examples = ["对设备做一次校准", "calibrate the flow meter against the reference", "标定这个传感器"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}.calibration"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: CalibrateDeviceArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        device.calibration_reference = args.reference_value
        return ok(data={"device_id": args.device_id, "calibrated": True}, world_diff=_diff(device))


class ResetDeviceArgs(BaseModel):
    action: Literal["reset_device"] = "reset_device"
    device_id: str


class ResetDevice(MockTool):
    name = "reset_device"
    domain = DOMAIN; action = "reset_device"
    description = "Send a soft reset to a device to clear latched faults."
    args_model = ResetDeviceArgs
    examples = ["复位这个设备", "reset the drive to clear the fault", "清一下设备的故障锁定"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        # A soft reset clears latched faults on the device itself; no persistent
        # project state changes, so this claims nothing it does not write.
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: ResetDeviceArgs, world: MockWorld) -> ToolResult:
        if args.device_id not in world.devices:
            return _missing(args.device_id)
        return ok(data={"device_id": args.device_id, "reset": True})


class EnableDeviceArgs(BaseModel):
    action: Literal["enable_device"] = "enable_device"
    device_id: str


class EnableDevice(MockTool):
    name = "enable_device"
    domain = DOMAIN; action = "enable_device"
    description = "Enable a device so it is polled and shown as in-service."
    args_model = EnableDeviceArgs
    examples = ["启用这个设备", "put the pump back in service", "把设备投用"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}.enabled"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: EnableDeviceArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        device.enabled = True
        return ok(data={"device_id": args.device_id, "enabled": True}, world_diff=_diff(device))


class DisableDeviceArgs(BaseModel):
    action: Literal["disable_device"] = "disable_device"
    device_id: str


class DisableDevice(MockTool):
    name = "disable_device"
    domain = DOMAIN; action = "disable_device"
    description = "Take a device out of service (stop polling, mark disabled)."
    args_model = DisableDeviceArgs
    examples = ["停用这个设备", "take the pump out of service for maintenance", "把设备退出运行"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}.enabled"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: DisableDeviceArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        device.enabled = False
        return ok(data={"device_id": args.device_id, "enabled": False}, world_diff=_diff(device))


class SetDevicePollingArgs(BaseModel):
    action: Literal["set_device_polling"] = "set_device_polling"
    device_id: str
    interval_ms: int = Field(ge=10, le=600000)


class SetDevicePolling(MockTool):
    name = "set_device_polling"
    domain = DOMAIN; action = "set_device_polling"
    description = "Set the per-device data polling interval."
    args_model = SetDevicePollingArgs
    examples = ["设置设备的采集周期", "poll this device every 2 seconds", "调整设备的扫描频率"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}.polling"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: SetDevicePollingArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        device.polling_interval_ms = args.interval_ms
        return ok(data={"device_id": args.device_id, "interval_ms": args.interval_ms}, world_diff=_diff(device))


class AssignDeviceToAreaArgs(BaseModel):
    action: Literal["assign_device_to_area"] = "assign_device_to_area"
    device_id: str
    area_id: str


class AssignDeviceToArea(MockTool):
    name = "assign_device_to_area"
    domain = DOMAIN; action = "assign_device_to_area"
    description = "Assign a device to a plant area for the equipment hierarchy."
    args_model = AssignDeviceToAreaArgs
    examples = ["把设备归到某个区域", "assign this device to Area 300", "设置设备所属车间"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}.area_id"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: AssignDeviceToAreaArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        device.area_id = args.area_id
        return ok(data={"device_id": args.device_id, "area_id": args.area_id}, world_diff=_diff(device))


class LinkDevicePointsArgs(BaseModel):
    action: Literal["link_device_points"] = "link_device_points"
    device_id: str
    tags: list[str] = Field(min_length=1)


class LinkDevicePoints(MockTool):
    name = "link_device_points"
    domain = DOMAIN; action = "link_device_points"
    description = "Associate a set of SCADA points with a device instance."
    args_model = LinkDevicePointsArgs
    examples = ["把点位关联到设备", "link TEMP/PRESS/LEVEL tags to reactor_1", "给设备挂上相关点位"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}.points"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"] + [f"points.{t}" for t in args.tags]

    def run(self, args: LinkDevicePointsArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        # Unlike create_device this is an association to points that must already
        # exist, so a missing tag is a reference failure the cascade detector can
        # trace back to whichever step should have created it.
        missing = [t for t in args.tags if t not in world.points]
        if missing:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point(s) not found: {', '.join(missing)}")
        for tag in args.tags:
            if tag not in device.tags:
                device.tags.append(tag)
        return ok(data={"device_id": args.device_id, "linked": len(args.tags)}, world_diff=_diff(device))


class GetDeviceDiagnosticsArgs(BaseModel):
    action: Literal["get_device_diagnostics"] = "get_device_diagnostics"
    device_id: str


class GetDeviceDiagnostics(MockTool):
    name = "get_device_diagnostics"
    domain = DOMAIN; action = "get_device_diagnostics"
    description = "Retrieve fault codes / diagnostic registers from a device."
    args_model = GetDeviceDiagnosticsArgs
    examples = ["读取设备的诊断信息", "pull the fault codes from this drive", "看看设备报了什么故障"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: GetDeviceDiagnosticsArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        return ok(data={"device_id": device.id, "faults": [], "protocol": device.protocol})


class SetDeviceAlarmLimitsArgs(BaseModel):
    action: Literal["set_device_alarm_limits"] = "set_device_alarm_limits"
    device_id: str
    low_limit: float | None = None
    high_limit: float | None = None


class SetDeviceAlarmLimits(MockTool):
    name = "set_device_alarm_limits"
    domain = DOMAIN; action = "set_device_alarm_limits"
    description = "Set default high/low alarm limits carried by a device template."
    args_model = SetDeviceAlarmLimitsArgs
    examples = ["设置设备的默认报警限值", "set high/low limits for this device", "配置设备模板的报警阈值"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}.alarm_limits"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: SetDeviceAlarmLimitsArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        if args.low_limit is None and args.high_limit is None:
            return fail(ErrorCode.BUSINESS_RULE, "at least one of low_limit / high_limit is required")
        if (
            args.low_limit is not None
            and args.high_limit is not None
            and args.low_limit >= args.high_limit
        ):
            return fail(ErrorCode.BUSINESS_RULE, "low_limit must be below high_limit")
        if args.low_limit is not None:
            device.low_limit = args.low_limit
        if args.high_limit is not None:
            device.high_limit = args.high_limit
        return ok(data={"device_id": args.device_id, "limits_set": True}, world_diff=_diff(device))


class ExportDeviceConfigArgs(BaseModel):
    action: Literal["export_device_config"] = "export_device_config"
    device_id: str


class ExportDeviceConfig(MockTool):
    name = "export_device_config"
    domain = DOMAIN; action = "export_device_config"
    description = "Export a device's full configuration to a file for backup/transfer."
    args_model = ExportDeviceConfigArgs
    examples = ["导出设备配置", "export this device's config for backup", "把设备参数导出来"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"devices.{args.device_id}"]

    def run(self, args: ExportDeviceConfigArgs, world: MockWorld) -> ToolResult:
        device = world.devices.get(args.device_id)
        if device is None:
            return _missing(args.device_id)
        return ok(data={"device_id": device.id, "exported": True, "config": device.model_dump()})


DEVICE_ACTIONS.update({
    cls.action: cls
    for cls in (
        CloneDevice, MoveDevice, SetDeviceTemplate, GetDeviceHealth, CalibrateDevice,
        ResetDevice, EnableDevice, DisableDevice, SetDevicePolling, AssignDeviceToArea,
        LinkDevicePoints, GetDeviceDiagnostics, SetDeviceAlarmLimits, ExportDeviceConfig,
    )
})
