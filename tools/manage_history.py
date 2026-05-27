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
