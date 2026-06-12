"""Tool Registry — central source of truth for all MockTool subclasses.

The registry has two LLM-facing views, switched by ``ArchitectureConfig``:

* **Flat mode** — each Atomic Tool is exposed individually
  (``create_analog_alarm``, ``bind_point``, …). This is the baseline that
  the paper's H1 measures against.
* **Hierarchical mode** — Domain Tools are exposed with a discriminated
  ``action`` field; the dispatcher unwraps the union internally
  (``manage_alarms`` → ``create_analog_alarm`` / …).

§3.3.1 requires a **reverse table** mapping every Atomic name to its
``(domain, action)`` so that flat-vs-hierarchical scoring lives in the
same logical space. The registry builds that table at startup and
self-checks for completeness — any unmapped Atomic raises immediately
(fail-fast).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent.config import ArchitectureConfig
from tools import (
    deployment,
    manage_alarms,
    manage_communication,
    manage_databases,
    manage_devices,
    manage_graphics,
    manage_history,
    manage_notifications,
    manage_pages,
    manage_points,
    manage_recipes,
    manage_reports,
    manage_schedules,
    manage_scripts,
    manage_security,
    manage_trends,
    manage_users,
)
from tools._base import MockTool


# ----------------------------------------------------- per-tool metadata bundle
@dataclass(frozen=True)
class ToolMeta:
    name: str
    domain: str
    action: str
    description: str
    args_model: type[BaseModel]
    examples: list[str]
    required_state: set[str] | None
    handler: MockTool


# ----------------------------------------------------- per-domain bundle
@dataclass(frozen=True)
class DomainMeta:
    name: str
    description: str
    union_model: Any  # discriminated union typing.Annotated[...]
    actions: dict[str, ToolMeta]


# ----------------------------------------------------- registry
class ToolRegistry:
    def __init__(self) -> None:
        self._atomics: dict[str, ToolMeta] = {}
        self._domains: dict[str, DomainMeta] = {}
        # reverse table: atomic_name -> (domain, action)
        self._reverse: dict[str, tuple[str, str]] = {}

    # ----------------------------------------- registration --------------
    def register_domain(
        self,
        domain: str,
        union_model: Any,
        actions: dict[str, type[MockTool]],
        description: str = "",
    ) -> None:
        bundle: dict[str, ToolMeta] = {}
        for action_name, cls in actions.items():
            inst = cls()
            meta = ToolMeta(
                name=cls.name,
                domain=cls.domain,
                action=cls.action,
                description=cls.description,
                args_model=cls.args_model,
                examples=list(cls.examples),
                required_state=set(cls.required_state) if cls.required_state else None,
                handler=inst,
            )
            if cls.domain != domain:
                raise ValueError(
                    f"{cls.__name__}.domain={cls.domain!r} mismatches register_domain({domain!r})"
                )
            if cls.action != action_name:
                raise ValueError(
                    f"{cls.__name__}.action={cls.action!r} mismatches action key {action_name!r}"
                )
            if cls.name in self._atomics:
                raise ValueError(f"duplicate atomic tool name {cls.name!r}")
            self._atomics[cls.name] = meta
            self._reverse[cls.name] = (cls.domain, cls.action)
            bundle[action_name] = meta
        self._domains[domain] = DomainMeta(
            name=domain, description=description, union_model=union_model, actions=bundle
        )

    # ----------------------------------------- queries -------------------
    def all_atomics(self) -> list[ToolMeta]:
        return list(self._atomics.values())

    def all_domains(self) -> list[DomainMeta]:
        return list(self._domains.values())

    def atomic(self, name: str) -> ToolMeta:
        if name not in self._atomics:
            raise KeyError(f"unknown atomic tool {name!r}")
        return self._atomics[name]

    def domain(self, name: str) -> DomainMeta:
        if name not in self._domains:
            raise KeyError(f"unknown domain tool {name!r}")
        return self._domains[name]

    def lookup(self, atomic_name: str) -> tuple[str, str]:
        """Reverse-lookup: atomic name → (domain, action) — used by metrics scoring."""
        if atomic_name not in self._reverse:
            raise KeyError(f"no (domain, action) entry for atomic {atomic_name!r}")
        return self._reverse[atomic_name]

    # ----------------------------------------- LLM-facing views ----------
    def visible_to_llm(self, arch: ArchitectureConfig) -> list[dict[str, Any]]:
        """Return the JSON-schema dicts the LLM is allowed to see.

        - Flat mode: one schema per atomic tool
        - Hierarchical mode: one schema per domain tool with a discriminated union
        """
        if arch.hierarchical_tools:
            out: list[dict[str, Any]] = []
            for d in self._domains.values():
                actions_block = {
                    a.action: {
                        "description": a.description,
                        "schema": a.args_model.model_json_schema(),
                    }
                    for a in d.actions.values()
                }
                out.append(
                    {
                        "name": d.name,
                        "kind": "domain",
                        "description": d.description or f"Dispatcher for {d.name}",
                        "actions": actions_block,
                    }
                )
            return out
        # flat
        return [
            {
                "name": a.name,
                "kind": "atomic",
                "domain": a.domain,
                "action": a.action,
                "description": a.description,
                "schema": a.args_model.model_json_schema(),
            }
            for a in self._atomics.values()
        ]

    # ----------------------------------------- self-check ----------------
    def selfcheck(self) -> None:
        """Verify the reverse-lookup table is complete & domains are well-formed.

        Called from ``build_default_registry`` so any future refactor that
        breaks the mapping fails at import time, not in the middle of an
        experiment run.
        """
        # 1. every atomic must have a (domain, action) entry
        for name in self._atomics:
            if name not in self._reverse:
                raise RuntimeError(f"reverse-lookup missing entry for atomic {name!r}")
        # 2. every (domain, action) referenced by reverse-lookup must exist
        for atomic_name, (d, a) in self._reverse.items():
            if d not in self._domains:
                raise RuntimeError(f"reverse-lookup references unknown domain {d!r}")
            if a not in self._domains[d].actions:
                raise RuntimeError(f"reverse-lookup references unknown action {d}.{a}")
        # 3. every domain must have ≥ 1 action
        for d, meta in self._domains.items():
            if not meta.actions:
                raise RuntimeError(f"domain {d!r} has no actions")

    # ----------------------------------------- generated-examples loader -
    def merge_generated_examples(
        self, path: str | Path, *, dedup: bool = True
    ) -> dict[str, int]:
        """Append LLM-generated examples from ``path`` to each atomic's ``examples``.

        The sidecar JSON is produced by ``scripts/generate_examples.py`` and
        is keyed by atomic name. Returns ``{atomic_name: appended_count}``
        for the tools that were actually augmented. Missing keys / unknown
        atomic names are silently skipped so the registry tolerates partial
        sidecars (Phase-2 §2.4 leaves this as an opt-in enhancement).
        """
        p = Path(path)
        if not p.is_file():
            return {}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}
        appended: dict[str, int] = {}
        for name, examples in raw.items():
            if not isinstance(examples, list):
                continue
            if name not in self._atomics:
                continue
            meta = self._atomics[name]
            existing = set(meta.examples) if dedup else set()
            added = 0
            for e in examples:
                if not isinstance(e, str):
                    continue
                if dedup and e in existing:
                    continue
                meta.examples.append(e)
                existing.add(e)
                added += 1
            if added:
                appended[name] = added
        return appended


# ============================================================ default sidecar
DEFAULT_GENERATED_EXAMPLES = Path("indices/generated_examples.json")


# ============================================================ default registry
def build_default_registry() -> ToolRegistry:
    """Construct the canonical registry covering all 7 domains (Phase 2)."""
    reg = ToolRegistry()
    reg.register_domain(
        domain="manage_alarms",
        union_model=manage_alarms.ManageAlarmsArgs,
        actions=manage_alarms.ALARM_ACTIONS,
        description="Create / update / enable / disable / delete SCADA alarms.",
    )
    reg.register_domain(
        domain="manage_points",
        union_model=manage_points.ManagePointsArgs,
        actions=manage_points.POINT_ACTIONS,
        description="Create / update / delete / list SCADA points (tags).",
    )
    reg.register_domain(
        domain="manage_pages",
        union_model=manage_pages.ManagePagesArgs,
        actions=manage_pages.PAGE_ACTIONS,
        description="HMI page & widget management, including point↔widget binding.",
    )
    reg.register_domain(
        domain="manage_graphics",
        union_model=manage_graphics.ManageGraphicsArgs,
        actions=manage_graphics.GRAPHICS_ACTIONS,
        description="Graphical primitives, layouts, grouping, styling.",
    )
    reg.register_domain(
        domain="manage_history",
        union_model=manage_history.ManageHistoryArgs,
        actions=manage_history.HISTORY_ACTIONS,
        description="Historian config + synthetic historical-window queries.",
    )
    reg.register_domain(
        domain="manage_scripts",
        union_model=manage_scripts.ManageScriptsArgs,
        actions=manage_scripts.SCRIPT_ACTIONS,
        description="User script CRUD (on_change / on_alarm / periodic / on_event).",
    )
    reg.register_domain(
        domain="deployment",
        union_model=deployment.DeploymentArgs,
        actions=deployment.DEPLOYMENT_ACTIONS,
        description="Project validation, deployment, rollback, status.",
    )
    reg.register_domain(
        domain="manage_devices",
        union_model=manage_devices.ManageDevicesArgs,
        actions=manage_devices.DEVICE_ACTIONS,
        description="Device catalog: create, update, delete, configure params, status queries.",
    )
    reg.register_domain(
        domain="manage_trends",
        union_model=manage_trends.ManageTrendsArgs,
        actions=manage_trends.TREND_ACTIONS,
        description="Trend curves: create groups, add pens, configure axes, sampling, scroll-back.",
    )
    reg.register_domain(
        domain="manage_recipes",
        union_model=manage_recipes.ManageRecipesArgs,
        actions=manage_recipes.RECIPE_ACTIONS,
        description="Batch recipes: steps, parameters, validation, activation, cloning.",
    )
    reg.register_domain(
        domain="manage_users",
        union_model=manage_users.ManageUsersArgs,
        actions=manage_users.USER_ACTIONS,
        description="User accounts: CRUD, role assignment, permissions, session policy.",
    )
    reg.register_domain(
        domain="manage_communication",
        union_model=manage_communication.ManageCommunicationArgs,
        actions=manage_communication.COMM_ACTIONS,
        description="Communication drivers: configure, start/stop polling, test, reset, stats.",
    )
    reg.register_domain(
        domain="manage_reports",
        union_model=manage_reports.ManageReportsArgs,
        actions=manage_reports.REPORT_ACTIONS,
        description="Report templates: sections, scheduling, generation, format, export.",
    )
    reg.register_domain(
        domain="manage_schedules",
        union_model=manage_schedules.ManageSchedulesArgs,
        actions=manage_schedules.SCHEDULE_ACTIONS,
        description="Scheduled jobs: triggers (cron/interval/event), actions, status.",
    )
    reg.register_domain(
        domain="manage_security",
        union_model=manage_security.ManageSecurityArgs,
        actions=manage_security.SECURITY_ACTIONS,
        description="Security: audit log, compliance checks, password policy, backup/restore.",
    )
    reg.register_domain(
        domain="manage_databases",
        union_model=manage_databases.ManageDatabasesArgs,
        actions=manage_databases.DATABASE_ACTIONS,
        description="External databases: connections, table creation, SQL queries, retention.",
    )
    reg.register_domain(
        domain="manage_notifications",
        union_model=manage_notifications.ManageNotificationsArgs,
        actions=manage_notifications.NOTIFICATION_ACTIONS,
        description="Alarm notifications: rules, escalation, channel config, testing.",
    )
    reg.selfcheck()
    if DEFAULT_GENERATED_EXAMPLES.is_file():
        reg.merge_generated_examples(DEFAULT_GENERATED_EXAMPLES)
    return reg


__all__ = [
    "DomainMeta",
    "ToolMeta",
    "ToolRegistry",
    "build_default_registry",
    "DEFAULT_GENERATED_EXAMPLES",
]
