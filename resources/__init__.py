"""Resources — read-only Mock World views (Phase 2 §1.4.9).

The default registry exposes 9 resource URIs covering pages, points, devices,
alarms, history configs, scripts, and deployments. These never mutate state;
``read_resource`` is the only way the LLM addresses them.

A registry is registered with ``build_default_resource_registry`` and consumed
by the orchestrator only when ``architecture.resources_separation`` is true.
"""
from __future__ import annotations

from resources._base import (
    FrozenWorld,
    ResourceMeta,
    ResourceNotFound,
    ResourceRegistry,
)
from resources.handlers import (
    deployment_status,
    get_device,
    get_page,
    get_point,
    list_alarms,
    list_devices,
    list_pages,
    list_points,
    list_scripts,
    list_widgets,
    query_history,
)


def build_default_resource_registry() -> ResourceRegistry:
    reg = ResourceRegistry()

    # pages
    reg.register(
        "scada://pages", "List all HMI pages (id, name, widget count).", list_pages
    )
    reg.register(
        "scada://pages/{page_id}", "Get a single page's metadata + widget IDs.", get_page
    )
    reg.register(
        "scada://pages/{page_id}/widgets",
        "List the widgets placed on a page.",
        list_widgets,
    )

    # points
    reg.register(
        "scada://points",
        "List all SCADA points; supports ?filter=substr&type=analog|digital|string.",
        list_points,
    )
    reg.register(
        "scada://points/{tag}", "Get a point by tag (analog/digital/string).", get_point
    )

    # devices
    reg.register(
        "scada://devices",
        "List devices; supports ?type=reactor|pump|tank|… filter.",
        list_devices,
    )
    reg.register("scada://devices/{device_id}", "Get a device by id.", get_device)

    # alarms
    reg.register(
        "scada://alarms",
        "List configured alarms; supports ?type=analog|digital filter.",
        list_alarms,
    )

    # history
    reg.register(
        "scada://history/{tag}",
        "Read the historian configuration for a tag.",
        query_history,
    )

    # scripts
    reg.register(
        "scada://scripts",
        "List user scripts; supports ?trigger=on_change|on_alarm|periodic|on_event.",
        list_scripts,
    )

    # deployments
    reg.register(
        "scada://deployments/{deployment_id}",
        "Inspect deployment status, validation errors, target.",
        deployment_status,
    )

    return reg


__all__ = [
    "FrozenWorld",
    "ResourceMeta",
    "ResourceNotFound",
    "ResourceRegistry",
    "build_default_resource_registry",
]
