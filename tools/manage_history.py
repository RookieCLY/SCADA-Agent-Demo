"""manage_history — historian / archive configuration per tag.

The demo does NOT actually record samples; it only tracks the configuration
(enabled, storage_mode, sample_interval_s, deadband, retention_days) and
returns a synthetic time-window query so workflows that ask "show me the last
N samples" can be exercised. The point of recording the *config* in the
MockWorld is so that downstream Resources (``scada://history/{tag}``) and the
``deployment`` validator can see whether a point has history enabled.
"""
from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator

from tools._base import ErrorCode, MockTool, ToolResult, fail, ok
from world import HistoryStorageMode, MockWorld
from world.models import HistoryConfig

DOMAIN = "manage_history"


# ---------------------------------------------------------------- enable_history
class EnableHistoryArgs(BaseModel):
    action: Literal["enable_history"] = "enable_history"
    tag: str
    storage_mode: HistoryStorageMode = "periodic"
    sample_interval_s: float = Field(default=1.0, gt=0)
    deadband: float = Field(default=0.0, ge=0)
    retention_days: int = Field(default=30, ge=1)


class EnableHistory(MockTool):
    name = "enable_history"
    domain = DOMAIN
    action = "enable_history"
    description = "Turn on historian sampling for a tag (creates the config if needed)."
    args_model = EnableHistoryArgs
    examples = [
        "把温度记录到历史库",
        "enable history for TEMP_101",
        "把压力点位加入历史归档",
        "开启数据存档",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"histories.{args.tag}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"points.{args.tag}"]

    def run(self, args: EnableHistoryArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.points:
            return fail(ErrorCode.POINT_NOT_FOUND, f"point {args.tag} not found")
        cfg = HistoryConfig(
            tag=args.tag,
            enabled=True,
            storage_mode=args.storage_mode,
            sample_interval_s=args.sample_interval_s,
            deadband=args.deadband,
            retention_days=args.retention_days,
        )
        world.histories[args.tag] = cfg
        return ok(
            data={"tag": args.tag, "enabled": True},
            world_diff={"added_or_modified": {f"histories.{args.tag}": cfg.model_dump()}, "removed": []},
        )


# ---------------------------------------------------------------- disable_history
class DisableHistoryArgs(BaseModel):
    action: Literal["disable_history"] = "disable_history"
    tag: str


class DisableHistory(MockTool):
    name = "disable_history"
    domain = DOMAIN
    action = "disable_history"
    description = "Pause historian sampling for a tag without deleting the configuration."
    args_model = DisableHistoryArgs
    examples = [
        "暂停历史记录",
        "stop archiving this tag",
        "把数据存档关掉",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"histories.{args.tag}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"histories.{args.tag}"]

    def run(self, args: DisableHistoryArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.histories:
            return fail(ErrorCode.POINT_NOT_FOUND, f"history config for {args.tag} not found")
        world.histories[args.tag].enabled = False
        return ok(
            data={"tag": args.tag, "enabled": False},
            world_diff={"added_or_modified": {f"histories.{args.tag}.enabled": False}, "removed": []},
        )


# ---------------------------------------------------------------- set_retention
class SetRetentionArgs(BaseModel):
    action: Literal["set_retention"] = "set_retention"
    tag: str
    retention_days: int = Field(ge=1)


class SetRetention(MockTool):
    name = "set_retention"
    domain = DOMAIN
    action = "set_retention"
    description = "Change the retention window (in days) of a tag's history config."
    args_model = SetRetentionArgs
    examples = [
        "把历史保留时间改成 90 天",
        "set retention to 7 days",
        "保留半年的数据",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"histories.{args.tag}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"histories.{args.tag}"]

    def run(self, args: SetRetentionArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.histories:
            return fail(ErrorCode.POINT_NOT_FOUND, f"history config for {args.tag} not found")
        world.histories[args.tag].retention_days = args.retention_days
        return ok(
            data={"tag": args.tag, "retention_days": args.retention_days},
            world_diff={
                "added_or_modified": {f"histories.{args.tag}.retention_days": args.retention_days},
                "removed": [],
            },
        )


# ---------------------------------------------------------------- query_history (read-only)
class QueryHistoryArgs(BaseModel):
    action: Literal["query_history"] = "query_history"
    tag: str
    window_s: float = Field(default=60.0, gt=0)
    max_samples: int = Field(default=20, ge=1, le=1000)


class QueryHistory(MockTool):
    name = "query_history"
    domain = DOMAIN
    action = "query_history"
    description = "Read a synthetic historical window for a tag (deterministic sine-wave demo)."
    args_model = QueryHistoryArgs
    examples = [
        "查最近一分钟的温度趋势",
        "show the last 60 seconds of pressure samples",
        "拉取历史曲线",
        "看一下最近的历史数据",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return []

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"histories.{args.tag}"]

    def run(self, args: QueryHistoryArgs, world: MockWorld) -> ToolResult:
        if args.tag not in world.histories:
            return fail(ErrorCode.POINT_NOT_FOUND, f"history config for {args.tag} not found")
        cfg = world.histories[args.tag]
        if not cfg.enabled:
            return fail(ErrorCode.BUSINESS_RULE, f"history for {args.tag} is disabled")
        n = max(1, min(args.max_samples, int(args.window_s / max(cfg.sample_interval_s, 1e-3))))
        # Deterministic synthetic samples — sine wave seeded by tag hash so repeated
        # queries produce identical traces, which is required for reproducibility.
        seed = sum(ord(c) for c in args.tag) % 1000
        samples = [
            {"t": i, "v": round(math.sin((seed + i) / 7.0), 4)}
            for i in range(n)
        ]
        return ok(data={"tag": args.tag, "samples": samples, "count": n})


# ---------------------------------------------------------------- list_history
class ListHistoryArgs(BaseModel):
    action: Literal["list_history"] = "list_history"
    enabled_only: bool = False


class ListHistory(MockTool):
    name = "list_history"
    domain = DOMAIN
    action = "list_history"
    description = "List the configured historian tags and their parameters."
    args_model = ListHistoryArgs
    examples = [
        "列出所有正在归档的点位",
        "show me historian config",
        "哪些点位被记录到历史库",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return []

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return []

    def run(self, args: ListHistoryArgs, world: MockWorld) -> ToolResult:
        items = [
            cfg.model_dump()
            for cfg in world.histories.values()
            if not args.enabled_only or cfg.enabled
        ]
        return ok(data={"count": len(items), "histories": items})


# ---------------------------------------------------------------- registry hookup
HISTORY_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (EnableHistory, DisableHistory, SetRetention, QueryHistory, ListHistory)
}

ManageHistoryArgs = Annotated[
    Union[
        EnableHistoryArgs,
        DisableHistoryArgs,
        SetRetentionArgs,
        QueryHistoryArgs,
        ListHistoryArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN",
    "HISTORY_ACTIONS",
    "ManageHistoryArgs",
    "DisableHistory",
    "DisableHistoryArgs",
    "EnableHistory",
    "EnableHistoryArgs",
    "ListHistory",
    "ListHistoryArgs",
    "QueryHistory",
    "QueryHistoryArgs",
    "SetRetention",
    "SetRetentionArgs",
]


# ============================================================ extension tools
def _hist_diff(tag, cfg):
    return {"added_or_modified": {f"histories.{tag}": cfg.model_dump()}, "removed": []}


def _need_history(world, tag):
    if tag not in world.histories:
        return fail(ErrorCode.BUSINESS_RULE, f"history not enabled for tag {tag}")
    return None


class SetSampleIntervalArgs(BaseModel):
    action: Literal["set_sample_interval"] = "set_sample_interval"
    tag: str
    sample_interval_s: float = Field(gt=0, le=3600)


class SetSampleInterval(MockTool):
    name = "set_sample_interval"
    domain = DOMAIN; action = "set_sample_interval"
    description = "Set the historian sampling interval (seconds) for a tag."
    args_model = SetSampleIntervalArgs
    examples = ["把历史采样周期设成1秒", "sample this tag every 5 seconds", "调整历史记录频率"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]

    def run(self, args: SetSampleIntervalArgs, world: MockWorld) -> ToolResult:
        err = _need_history(world, args.tag)
        if err: return err
        c = world.histories[args.tag]; c.sample_interval_s = args.sample_interval_s
        return ok(data={"tag": args.tag}, world_diff=_hist_diff(args.tag, c))


class SetHistoryDeadbandArgs(BaseModel):
    action: Literal["set_history_deadband"] = "set_history_deadband"
    tag: str
    deadband: float = Field(ge=0)


class SetHistoryDeadband(MockTool):
    name = "set_history_deadband"
    domain = DOMAIN; action = "set_history_deadband"
    description = "Set the archive deadband so only meaningful changes are stored."
    args_model = SetHistoryDeadbandArgs
    examples = ["给历史记录设置死区", "only archive changes bigger than 0.5", "减少历史存储量用死区"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]

    def run(self, args: SetHistoryDeadbandArgs, world: MockWorld) -> ToolResult:
        err = _need_history(world, args.tag)
        if err: return err
        c = world.histories[args.tag]; c.deadband = args.deadband
        return ok(data={"tag": args.tag}, world_diff=_hist_diff(args.tag, c))


class DeleteHistoryArgs(BaseModel):
    action: Literal["delete_history"] = "delete_history"
    tag: str


class DeleteHistory(MockTool):
    name = "delete_history"
    domain = DOMAIN; action = "delete_history"
    description = "Remove the historian configuration for a tag (stops archiving)."
    args_model = DeleteHistoryArgs
    examples = ["删除这个点位的历史配置", "stop archiving this tag entirely", "移除历史记录设置"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]

    def run(self, args: DeleteHistoryArgs, world: MockWorld) -> ToolResult:
        err = _need_history(world, args.tag)
        if err: return err
        del world.histories[args.tag]
        return ok(data={"tag": args.tag}, world_diff={"added_or_modified": {}, "removed": [f"histories.{args.tag}"]})


class GetHistoryStatsArgs(BaseModel):
    action: Literal["get_history_stats"] = "get_history_stats"
    tag: str


class GetHistoryStats(MockTool):
    name = "get_history_stats"
    domain = DOMAIN; action = "get_history_stats"
    description = "Get min/max/avg/count statistics of a tag's stored history."
    args_model = GetHistoryStatsArgs
    examples = ["查看历史数据的统计", "what's the average of TEMP_101 today", "统计一下历史数据"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]

    def run(self, args: GetHistoryStatsArgs, world: MockWorld) -> ToolResult:
        return ok(data={"tag": args.tag, "stats": {}})


class ExportHistoryArgs(BaseModel):
    action: Literal["export_history"] = "export_history"
    tag: str
    format: Literal["csv", "parquet"] = "csv"


class ExportHistory(MockTool):
    name = "export_history"
    domain = DOMAIN; action = "export_history"
    description = "Export a tag's historical data to a file."
    args_model = ExportHistoryArgs
    examples = ["导出历史数据", "export TEMP_101 history to CSV", "把历史曲线数据导出来"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]

    def run(self, args: ExportHistoryArgs, world: MockWorld) -> ToolResult:
        return ok(data={"tag": args.tag, "format": args.format})


class SetStoragePolicyArgs(BaseModel):
    action: Literal["set_storage_policy"] = "set_storage_policy"
    tag: str
    policy: Literal["raw", "compressed", "aggregated"] = "compressed"


class SetStoragePolicy(MockTool):
    name = "set_storage_policy"
    domain = DOMAIN; action = "set_storage_policy"
    description = "Choose how a tag's history is stored (raw / compressed / aggregated)."
    args_model = SetStoragePolicyArgs
    examples = ["设置历史存储策略", "store this tag compressed", "配置历史数据的存储方式"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}.storage_policy"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]

    def run(self, args: SetStoragePolicyArgs, world: MockWorld) -> ToolResult:
        err = _need_history(world, args.tag)
        if err: return err
        cfg = world.histories[args.tag]; cfg.storage_policy = args.policy
        return ok(data={"tag": args.tag, "policy": args.policy},
                  world_diff={"added_or_modified": {f"histories.{args.tag}": cfg.model_dump()},
                              "removed": []})


class SetHistoryAggregationArgs(BaseModel):
    action: Literal["set_history_aggregation"] = "set_history_aggregation"
    tag: str
    method: Literal["average", "min", "max", "sum", "last"] = "average"
    window_s: int = Field(default=60, ge=1, le=86400)


class SetHistoryAggregation(MockTool):
    name = "set_history_aggregation"
    domain = DOMAIN; action = "set_history_aggregation"
    description = "Configure server-side aggregation (rollups) for a tag's history."
    args_model = SetHistoryAggregationArgs
    examples = ["设置历史数据的聚合方式", "roll up this tag to 1-minute averages", "配置历史数据的降采样"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}.aggregation"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]

    def run(self, args: SetHistoryAggregationArgs, world: MockWorld) -> ToolResult:
        err = _need_history(world, args.tag)
        if err: return err
        return ok(data={"tag": args.tag, "method": args.method})


class PurgeHistoryArgs(BaseModel):
    action: Literal["purge_history"] = "purge_history"
    tag: str
    before_days: int = Field(ge=1, le=3650)


class PurgeHistory(MockTool):
    name = "purge_history"
    domain = DOMAIN; action = "purge_history"
    description = "Purge stored history older than N days for a tag (destructive)."
    args_model = PurgeHistoryArgs
    examples = ["清理旧的历史数据", "purge history older than 365 days", "删掉一年前的历史"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]

    def run(self, args: PurgeHistoryArgs, world: MockWorld) -> ToolResult:
        err = _need_history(world, args.tag)
        if err: return err
        cfg = world.histories[args.tag]
        if cfg.stored_days <= args.before_days:
            # Nothing is older than the cutoff. A genuine no-op, and reported as
            # one rather than as a successful purge.
            return ok(data={"tag": args.tag, "purged_before_days": args.before_days,
                            "removed_samples": 0})
        kept = args.before_days / cfg.stored_days
        removed = cfg.stored_samples - int(cfg.stored_samples * kept)
        cfg.stored_days = args.before_days
        cfg.stored_samples -= removed
        return ok(
            data={"tag": args.tag, "purged_before_days": args.before_days,
                  "removed_samples": removed},
            world_diff={"added_or_modified": {f"histories.{args.tag}": cfg.model_dump()},
                        "removed": []},
        )


class BackfillHistoryArgs(BaseModel):
    action: Literal["backfill_history"] = "backfill_history"
    tag: str
    source_file: str


class BackfillHistory(MockTool):
    name = "backfill_history"
    domain = DOMAIN; action = "backfill_history"
    description = "Backfill a tag's history from an external data file."
    args_model = BackfillHistoryArgs
    examples = ["回填历史数据", "backfill this tag from the lab CSV", "把缺失的历史补上"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}.data"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]

    def run(self, args: BackfillHistoryArgs, world: MockWorld) -> ToolResult:
        err = _need_history(world, args.tag)
        if err: return err
        return ok(data={"tag": args.tag, "backfilled": True})


class GetHistoryGapsArgs(BaseModel):
    action: Literal["get_history_gaps"] = "get_history_gaps"
    tag: str
    last_n_days: int = Field(default=7, ge=1, le=365)


class GetHistoryGaps(MockTool):
    name = "get_history_gaps"
    domain = DOMAIN; action = "get_history_gaps"
    description = "Find gaps (missing intervals) in a tag's stored history."
    args_model = GetHistoryGapsArgs
    examples = ["查看历史数据的缺口", "are there gaps in this tag's history", "历史数据有没有断档"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]

    def run(self, args: GetHistoryGapsArgs, world: MockWorld) -> ToolResult:
        return ok(data={"tag": args.tag, "gaps": []})


class ConfigureHistorianConnectionArgs(BaseModel):
    action: Literal["configure_historian_connection"] = "configure_historian_connection"
    endpoint: str
    kind: Literal["influxdb", "pi", "timescale", "internal"] = "internal"


class ConfigureHistorianConnection(MockTool):
    name = "configure_historian_connection"
    domain = DOMAIN; action = "configure_historian_connection"
    description = "Configure the backing historian datastore connection."
    args_model = ConfigureHistorianConnectionArgs
    examples = ["配置历史库连接", "point the historian at our InfluxDB", "设置历史数据库地址"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return ["project_meta.historian.connection"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ConfigureHistorianConnectionArgs, world: MockWorld) -> ToolResult:
        return ok(data={"kind": args.kind, "configured": True})


class SetHistoryPrecisionArgs(BaseModel):
    action: Literal["set_history_precision"] = "set_history_precision"
    tag: str
    decimals: int = Field(ge=0, le=10)


class SetHistoryPrecision(MockTool):
    name = "set_history_precision"
    domain = DOMAIN; action = "set_history_precision"
    description = "Set the stored decimal precision for a tag's history values."
    args_model = SetHistoryPrecisionArgs
    examples = ["设置历史值的小数位数", "store this tag with 2 decimals", "调整历史数据精度"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}.precision"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"histories.{args.tag}"]

    def run(self, args: SetHistoryPrecisionArgs, world: MockWorld) -> ToolResult:
        err = _need_history(world, args.tag)
        if err: return err
        return ok(data={"tag": args.tag, "decimals": args.decimals})


HISTORY_ACTIONS.update({
    cls.action: cls
    for cls in (
        SetSampleInterval, SetHistoryDeadband, DeleteHistory, GetHistoryStats,
        ExportHistory, SetStoragePolicy, SetHistoryAggregation, PurgeHistory,
        BackfillHistory, GetHistoryGaps, ConfigureHistorianConnection, SetHistoryPrecision,
    )
})
