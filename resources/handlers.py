"""Concrete Resource handlers — read-only views over the Mock World.

Each handler takes a ``FrozenWorld`` plus URI-template parameters and returns
a plain ``dict[str, Any]`` payload.
"""
from __future__ import annotations

from typing import Any

from resources._base import FrozenWorld, ResourceNotFound


# ---------------------------------------------------------------- pages
def list_pages(world: FrozenWorld) -> dict[str, Any]:
    pages = [
        {"id": p.id, "name": p.name, "widgets": len(p.widgets)}
        for p in world.pages.values()
    ]
    return {"count": len(pages), "pages": pages}


def get_page(world: FrozenWorld, page_id: str) -> dict[str, Any]:
    pages = world.pages
    if page_id not in pages:
        raise ResourceNotFound(f"page {page_id} not found")
    page = pages[page_id]
    return {
        "id": page.id,
        "name": page.name,
        "resolution": list(page.resolution),
        "background": page.background,
        "widget_ids": list(page.widgets.keys()),
    }


def list_widgets(world: FrozenWorld, page_id: str) -> dict[str, Any]:
    pages = world.pages
    if page_id not in pages:
        raise ResourceNotFound(f"page {page_id} not found")
    widgets = [w.model_dump() for w in pages[page_id].widgets.values()]
    return {"page_id": page_id, "count": len(widgets), "widgets": widgets}


# ---------------------------------------------------------------- points
def list_points(world: FrozenWorld, **filters: Any) -> dict[str, Any]:
    """``scada://points`` and ``scada://points?filter=TEMP`` / ?type=analog``."""
    f = filters.get("filter")
    t = filters.get("type")
    items: list[dict[str, Any]] = []
    for p in world.points.values():
        if f and f not in p.tag:
            continue
        if t and p.type != t:
            continue
        items.append(p.model_dump())
    return {"count": len(items), "points": items}


def get_point(world: FrozenWorld, tag: str) -> dict[str, Any]:
    if tag not in world.points:
        raise ResourceNotFound(f"point {tag} not found")
    return world.points[tag].model_dump()


# ---------------------------------------------------------------- devices
def list_devices(world: FrozenWorld, **filters: Any) -> dict[str, Any]:
    t = filters.get("type")
    items = [d.model_dump() for d in world.devices.values() if not t or d.type == t]
    return {"count": len(items), "devices": items}


def get_device(world: FrozenWorld, device_id: str) -> dict[str, Any]:
    if device_id not in world.devices:
        raise ResourceNotFound(f"device {device_id} not found")
    return world.devices[device_id].model_dump()


# ---------------------------------------------------------------- alarms
def list_alarms(world: FrozenWorld, **filters: Any) -> dict[str, Any]:
    t = filters.get("type")
    items = [a.model_dump() for a in world.alarms.values() if not t or a.type == t]
    return {"count": len(items), "alarms": items}


# ---------------------------------------------------------------- history
def query_history(world: FrozenWorld, tag: str, **q: Any) -> dict[str, Any]:
    """Return the history *config* for a tag.

    The synthetic sample data is intentionally accessed via the
    ``manage_history.query_history`` Tool (Tools are the only path that writes
    side-effects, even synthetic ones like "increment a counter"). Resources
    return only the static config.
    """
    if tag not in world.histories:
        raise ResourceNotFound(f"no history config for {tag}")
    return world.histories[tag].model_dump()


# ---------------------------------------------------------------- scripts
def list_scripts(world: FrozenWorld, **filters: Any) -> dict[str, Any]:
    trig = filters.get("trigger")
    items = [s.model_dump() for s in world.scripts.values() if not trig or s.trigger == trig]
    return {"count": len(items), "scripts": items}


# ---------------------------------------------------------------- deployments
def deployment_status(world: FrozenWorld, deployment_id: str) -> dict[str, Any]:
    if deployment_id not in world.deployments:
        return {"deployment_id": deployment_id, "status": "draft", "errors": []}
    return world.deployments[deployment_id].model_dump()


__all__ = [
    "deployment_status",
    "get_device",
    "get_page",
    "get_point",
    "list_alarms",
    "list_devices",
    "list_pages",
    "list_points",
    "list_scripts",
    "list_widgets",
    "query_history",
]
