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
        from typing import Annotated, Union
        from pydantic import Field
        args_models = [cls.args_model for cls in actions.values()]
        if len(args_models) > 1:
            # Subscript form rather than ``Union.__getitem__(...)``: on Python
            # 3.14 ``Union`` is ``types.UnionType`` and its ``__getitem__`` is a
            # descriptor that rejects a bare tuple. ``Union[tuple(...)]`` builds
            # the identical type on 3.11–3.14.
            union_model = Annotated[Union[tuple(args_models)], Field(discriminator="action")]
        elif len(args_models) == 1:
            union_model = args_models[0]
        else:
            union_model = None

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
def build_default_registry(tool_count: int | None = None) -> ToolRegistry:
    """Construct the canonical registry covering the registered domains, restricted by tool_count."""
    # ── dynamic tool expansion to target_count total tools ──────────────────────────
    import typing
    from typing import Literal
    from pydantic import BaseModel
    
    # 1. Define core and extra domains with their action definitions
    core_domains = {
        "manage_alarms": (manage_alarms.ManageAlarmsArgs, dict(manage_alarms.ALARM_ACTIONS), "Create / update / enable / disable / delete SCADA alarms."),
        "manage_points": (manage_points.ManagePointsArgs, dict(manage_points.POINT_ACTIONS), "Create / update / delete / list SCADA points (tags)."),
        "manage_pages": (manage_pages.ManagePagesArgs, dict(manage_pages.PAGE_ACTIONS), "HMI page & widget management, including point↔widget binding."),
        "manage_graphics": (manage_graphics.ManageGraphicsArgs, dict(manage_graphics.GRAPHICS_ACTIONS), "Graphical primitives, layouts, grouping, styling."),
        "manage_history": (manage_history.ManageHistoryArgs, dict(manage_history.HISTORY_ACTIONS), "Historian config + synthetic historical-window queries."),
        "manage_scripts": (manage_scripts.ManageScriptsArgs, dict(manage_scripts.SCRIPT_ACTIONS), "User script CRUD (on_change / on_alarm / periodic / on_event)."),
        "deployment": (deployment.DeploymentArgs, dict(deployment.DEPLOYMENT_ACTIONS), "Project validation, deployment, rollback, status."),
    }

    extra_domains = {
        "manage_devices": (manage_devices.ManageDevicesArgs, dict(manage_devices.DEVICE_ACTIONS), "Device catalog: create, update, delete, configure params, status queries."),
        "manage_trends": (manage_trends.ManageTrendsArgs, dict(manage_trends.TREND_ACTIONS), "Trend curves: create groups, add pens, configure axes, sampling, scroll-back."),
        "manage_recipes": (manage_recipes.ManageRecipesArgs, dict(manage_recipes.RECIPE_ACTIONS), "Batch recipes: steps, parameters, validation, activation, cloning."),
        "manage_users": (manage_users.ManageUsersArgs, dict(manage_users.USER_ACTIONS), "User accounts: CRUD, role assignment, permissions, session policy."),
        "manage_communication": (manage_communication.ManageCommunicationArgs, dict(manage_communication.COMM_ACTIONS), "Communication drivers: configure, start/stop polling, test, reset, stats."),
        "manage_reports": (manage_reports.ManageReportsArgs, dict(manage_reports.REPORT_ACTIONS), "Report templates: sections, scheduling, generation, format, export."),
        "manage_schedules": (manage_schedules.ManageSchedulesArgs, dict(manage_schedules.SCHEDULE_ACTIONS), "Scheduled jobs: triggers (cron/interval/event), actions, status."),
        "manage_security": (manage_security.ManageSecurityArgs, dict(manage_security.SECURITY_ACTIONS), "Security: audit log, compliance checks, password policy, backup/restore."),
        "manage_databases": (manage_databases.ManageDatabasesArgs, dict(manage_databases.DATABASE_ACTIONS), "External databases: connections, table creation, SQL queries, retention."),
        "manage_notifications": (manage_notifications.ManageNotificationsArgs, dict(manage_notifications.NOTIFICATION_ACTIONS), "Alarm notifications: rules, escalation, channel config, testing."),
    }

    target_count = tool_count if tool_count is not None else 500
    
    # Initialize domain_actions with copy of core domain actions
    domain_actions = {
        dname: dict(actions) for dname, (_, actions, _) in core_domains.items()
    }
    
    # Collect extra actions list deterministically
    extra_actions_list = []
    for dname, (_, actions_dict, _) in extra_domains.items():
        for aname, acls in actions_dict.items():
            extra_actions_list.append((dname, aname, acls))
            
    # Calculate how many extra tools we can add
    if target_count > 39:
        remaining_slots = target_count - 39
        num_extra_to_add = min(remaining_slots, len(extra_actions_list))
        
        # Add selected extra tools
        for dname, aname, acls in extra_actions_list[:num_extra_to_add]:
            domain_actions.setdefault(dname, {})[aname] = acls
            
        remaining_slots -= num_extra_to_add
        
        # If we still have slots, dynamically generate dynamic tools
        if remaining_slots > 0:
            prefixes = [
                "get", "set", "update", "delete", "create", "verify", "test", "sync", 
                "reset", "clear", "export", "import", "configure", "optimize", "check", 
                "enable", "disable", "list", "search", "audit", "monitor", "analyze", 
                "force", "bypass", "override", "lock", "unlock", "archive", "restore", "validate"
            ]
            nouns = [
                "limit", "threshold", "buffer", "cache", "status", "config", "parameter", "setting", 
                "profile", "policy", "log", "history", "record", "event", "alert", "channel", 
                "connection", "port", "interface", "module", "sensor", "actuator", "valve", "pump", 
                "motor", "tank", "vessel", "zone", "area", "group", "user", "role", 
                "session", "token", "key", "certificate", "backup", "restore", "script", "trigger", 
                "schedule", "report", "template", "recipe", "batch", "formula", "step", "phase", 
                "transition", "state", "layout", "widget", "view", "screen", "panel", "driver", 
                "protocol", "gateway", "broker", "endpoint", "topic", "tag", "metadata", "schema", "validation"
            ]
            
            existing_names = set()
            for actions_dict in domain_actions.values():
                existing_names.update(actions_dict.keys())
                
            generated_names = []
            seen = set()
            for p in prefixes:
                for n in nouns:
                    name = f"{p}_{n}"
                    if name not in existing_names and name not in seen:
                        seen.add(name)
                        generated_names.append(name)
                        if len(generated_names) >= remaining_slots:
                            break
                if len(generated_names) >= remaining_slots:
                    break
                    
            domains_list = list(domain_actions.keys())
            for i, action_name in enumerate(generated_names):
                domain = domains_list[i % len(domains_list)]
                desc = f"Dynamic {action_name}"
                
                # Args class
                args_class_name = f"Dynamic{action_name.title().replace('_', '')}Args"
                args_model = type(
                    args_class_name,
                    (BaseModel,),
                    {
                        "__annotations__": {"action": Literal[action_name]},
                        "action": action_name,
                    }
                )
                
                # MockTool class
                tool_class_name = f"Dynamic{action_name.title().replace('_', '')}Tool"
                
                @staticmethod
                def intended_entities(args: BaseModel) -> list[str]:
                    return []
                    
                @staticmethod
                def referenced_entities(args: BaseModel) -> list[str]:
                    return []
                    
                def run(self, args: Any, world: Any) -> Any:
                    from tools._base import ok
                    return ok()
                    
                tool_class = type(
                    tool_class_name,
                    (MockTool,),
                    {
                        "name": action_name,
                        "domain": domain,
                        "action": action_name,
                        "description": desc,
                        "args_model": args_model,
                        "examples": [],
                        "required_state": None,
                        "intended_entities": intended_entities,
                        "referenced_entities": referenced_entities,
                        "run": run,
                    }
                )
                
                domain_actions[domain][action_name] = tool_class

    # 2. Register all populated domains
    reg = ToolRegistry()
    all_all_domains = {**core_domains, **extra_domains}
    for dname, actions_dict in domain_actions.items():
        args_model, _, desc = all_all_domains[dname]
        reg.register_domain(
            domain=dname,
            union_model=args_model,
            actions=actions_dict,
            description=desc,
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
