"""Dispatcher — unwrap Domain Tool calls into Atomic Tool execution.

In hierarchical mode the LLM emits e.g. ``manage_alarms({action: "create_analog_alarm", ...})``;
the dispatcher pulls the action field off the args, looks up the matching
``MockTool`` handler via the registry, and runs it against the world.

L1 schema validation happens inside ``dispatch`` — args are parsed through the
discriminated union (or the atomic's own args model in flat mode), and any
Pydantic ``ValidationError`` is converted to an ``SCHEMA_ERROR`` ToolResult so
downstream code never sees an exception.
"""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from agent.tool_registry import ToolRegistry
from tools._base import ErrorCode, ToolResult, fail
from world import MockWorld


# ============================================================ shared call output
def _schema_error(e: ValidationError) -> ToolResult:
    # Trim pydantic error messages to the first 3 lines for log-friendliness
    msg = "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:3]
    )
    return fail(ErrorCode.SCHEMA_ERROR, msg)


# ============================================================ flat-mode entry
def dispatch_atomic(
    registry: ToolRegistry,
    tool_name: str,
    raw_args: dict[str, Any],
    world: MockWorld,
) -> tuple[ToolResult, BaseModel | None, float]:
    """Run an atomic tool by name. Returns (result, parsed_args_or_None, latency_ms)."""
    t0 = time.perf_counter()
    try:
        meta = registry.atomic(tool_name)
    except KeyError:
        return fail(ErrorCode.SCHEMA_ERROR, f"unknown tool {tool_name!r}"), None, 0.0
    try:
        # The atomic's args model has the discriminator already pinned to a Literal,
        # so we drop any caller-supplied `action` to avoid Literal mismatch:
        clean = {k: v for k, v in raw_args.items() if k != "action"}
        args = meta.args_model.model_validate({**clean, "action": meta.action})
    except ValidationError as e:
        return _schema_error(e), None, (time.perf_counter() - t0) * 1000
    result = meta.handler.run(args, world)
    return result, args, (time.perf_counter() - t0) * 1000


# ============================================================ hierarchical-mode entry
def dispatch_domain(
    registry: ToolRegistry,
    domain_name: str,
    raw_args: dict[str, Any],
    world: MockWorld,
) -> tuple[ToolResult, BaseModel | None, float, str | None]:
    """Run a domain tool by name. Returns (result, parsed_args, latency_ms, action).

    The action field is required in ``raw_args``; missing or unknown actions
    surface as SCHEMA_ERROR.
    """
    t0 = time.perf_counter()
    try:
        domain = registry.domain(domain_name)
    except KeyError:
        return (
            fail(ErrorCode.SCHEMA_ERROR, f"unknown domain {domain_name!r}"),
            None,
            0.0,
            None,
        )
    action = raw_args.get("action")
    if not isinstance(action, str):
        return (
            fail(ErrorCode.SCHEMA_ERROR, "domain call missing string `action` field"),
            None,
            (time.perf_counter() - t0) * 1000,
            None,
        )
    if action not in domain.actions:
        return (
            fail(
                ErrorCode.SCHEMA_ERROR,
                f"unknown action {action!r} for domain {domain_name!r}; "
                f"allowed: {sorted(domain.actions)}",
            ),
            None,
            (time.perf_counter() - t0) * 1000,
            action,
        )
    meta = domain.actions[action]
    try:
        adapter = TypeAdapter(domain.union_model)
        args = adapter.validate_python(raw_args)
    except ValidationError as e:
        return _schema_error(e), None, (time.perf_counter() - t0) * 1000, action
    result = meta.handler.run(args, world)
    return result, args, (time.perf_counter() - t0) * 1000, action


__all__ = ["dispatch_atomic", "dispatch_domain"]
