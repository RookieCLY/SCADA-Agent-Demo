"""Deterministic workflow-step handlers.

These run inside the workflow engine instead of going to the LLM. The signature
follows ``DeterministicHandler`` in ``agent/workflow.py``:

    handler(world, ctx) -> result_dict

Any exception is interpreted as step failure (the engine will follow the
``on_failure`` edge if one is declared).

The handlers we ship cover the common "after the LLM finished step N, check
that the world is consistent" pattern.
"""
from __future__ import annotations

from typing import Any

from agent.workflow import register_handler, register_predicate
from tools.deployment import _collect_validation_errors


def validate_project(world: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """Run the global consistency check; raise on errors."""
    errs = _collect_validation_errors(world)
    if errs:
        raise RuntimeError(
            f"project validation failed: {len(errs)} issue(s); first 3: {errs[:3]}"
        )
    return {"ok": True, "checked_at": "validate_project"}


def assert_alarm_exists(world: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """Used by alarm-config follow-ups; succeed iff at least one alarm is configured."""
    if not world.alarms:
        raise RuntimeError("no alarms configured after CONFIG_ALARM step")
    return {"ok": True, "n_alarms": len(world.alarms)}


# ----------------------------------------- predicates (conditional / loop steps)
# Signature: predicate(world, ctx) -> bool. Pure reads over the world, used by
# ConditionalStep / LoopStep to let the engine branch and iterate (§4.3.3).
def project_is_valid(world: Any, ctx: dict[str, Any]) -> bool:
    """True when the project passes the global consistency check."""
    return not _collect_validation_errors(world)


def has_alarms(world: Any, ctx: dict[str, Any]) -> bool:
    return bool(world.alarms)


def has_points(world: Any, ctx: dict[str, Any]) -> bool:
    return bool(world.points)


# ----------------------------------------- registration
register_handler("handlers.validate_project", validate_project)
register_handler("handlers.assert_alarm_exists", assert_alarm_exists)

register_predicate("predicates.project_is_valid", project_is_valid)
register_predicate("predicates.has_alarms", has_alarms)
register_predicate("predicates.has_points", has_points)


__all__ = [
    "assert_alarm_exists",
    "has_alarms",
    "has_points",
    "project_is_valid",
    "validate_project",
]
