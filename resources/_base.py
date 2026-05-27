"""Resources base — URI templates, frozen world views, registry.

Per §1.4.9 of the development plan:

* Resources never mutate the world; the world view they receive is frozen.
* Resources do not occupy Tool slots in the LLM-facing schema and do not
  enter Tool RAG ranking — they're addressed by URI via ``read_resource``.
* Failure semantics are limited to "not found"; business failures only exist
  on the Tool side.

The minimal contract for a Resource handler:

    handler(world: FrozenWorld, **path_params) -> dict[str, Any]

A handler that wants to flag "not found" raises ``ResourceNotFound``; everything
else is a plain return value that the orchestrator records in
``trace.resource_reads``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlsplit

from world import MockWorld


class ResourceNotFound(KeyError):
    """The URI matched a template but the underlying entity does not exist."""


# ============================================================ frozen view
class FrozenWorld:
    """A read-only proxy around a MockWorld.

    The proxy enforces immutability by exposing only the entity dictionaries as
    *copies* — handlers can iterate / index but cannot ``world.pages["p1"] = ...``
    smuggle a write back. The point is architectural enforcement, not raw
    performance; for the demo, deep-copying the few-hundred-byte payloads on
    each read is fine and keeps the read/write split tamper-proof.
    """

    __slots__ = ("_w",)

    def __init__(self, world: MockWorld) -> None:
        self._w = world

    @property
    def pages(self):
        return {k: v.model_copy(deep=True) for k, v in self._w.pages.items()}

    @property
    def points(self):
        return {k: v.model_copy(deep=True) for k, v in self._w.points.items()}

    @property
    def alarms(self):
        return {k: v.model_copy(deep=True) for k, v in self._w.alarms.items()}

    @property
    def devices(self):
        return {k: v.model_copy(deep=True) for k, v in self._w.devices.items()}

    @property
    def histories(self):
        return {k: v.model_copy(deep=True) for k, v in self._w.histories.items()}

    @property
    def scripts(self):
        return {k: v.model_copy(deep=True) for k, v in self._w.scripts.items()}

    @property
    def deployments(self):
        return {k: v.model_copy(deep=True) for k, v in self._w.deployments.items()}

    @property
    def project_meta(self):
        # plain dict deepcopy
        import copy

        return copy.deepcopy(self._w.project_meta)


# ============================================================ template parsing
_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _compile_template(template: str) -> tuple[re.Pattern[str], list[str]]:
    """``scada://pages/{page_id}/widgets`` → regex + ['page_id']."""
    params: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1)
        params.append(name)
        return f"(?P<{name}>[^/?#]+)"

    pattern = _PARAM_RE.sub(_sub, template)
    pattern = "^" + pattern + r"(?:\?(?P<_query>.*))?$"
    return re.compile(pattern), params


# ============================================================ registry
@dataclass(frozen=True)
class ResourceMeta:
    uri_template: str
    description: str
    handler: Callable[..., dict[str, Any]]
    pattern: re.Pattern[str]
    params: list[str]


class ResourceRegistry:
    """Central registry — registered at import time from each ``resources/*.py`` module."""

    def __init__(self) -> None:
        self._items: list[ResourceMeta] = []

    def register(
        self,
        uri_template: str,
        description: str,
        handler: Callable[..., dict[str, Any]],
    ) -> None:
        pattern, params = _compile_template(uri_template)
        self._items.append(
            ResourceMeta(
                uri_template=uri_template,
                description=description,
                handler=handler,
                pattern=pattern,
                params=params,
            )
        )

    def all(self) -> list[ResourceMeta]:
        return list(self._items)

    def read(self, uri: str, world: MockWorld) -> dict[str, Any]:
        """Resolve ``uri`` against registered templates and call its handler."""
        # Strip the scheme so the template form scada://X matches just X
        parsed = urlsplit(uri)
        netloc = parsed.netloc
        path = parsed.path
        query = parsed.query
        # rebuild the "matchable" string in template form, e.g. ``pages/p1``
        match_str = (netloc + path).lstrip("/")
        # also expose ?foo=bar for query-parameter templates
        if query:
            match_str = match_str + "?" + query

        for meta in self._items:
            tmpl_match = meta.uri_template
            if tmpl_match.startswith("scada://"):
                tmpl_match = tmpl_match[len("scada://"):]
            pattern, _ = _compile_template(tmpl_match)
            m = pattern.match(match_str)
            if not m:
                continue
            path_params: dict[str, Any] = {
                k: v for k, v in m.groupdict().items() if k != "_query" and v is not None
            }
            # Surface query-string parameters as plain kwargs as well
            qstr = m.groupdict().get("_query")
            if qstr:
                for k, vs in parse_qs(qstr, keep_blank_values=True).items():
                    path_params.setdefault(k, vs[0] if len(vs) == 1 else vs)
            frozen = FrozenWorld(world)
            return meta.handler(frozen, **path_params)

        raise ResourceNotFound(f"no resource handler matches uri {uri!r}")

    def describe_for_llm(self) -> list[dict[str, Any]]:
        """Return a compact JSON-serialisable catalogue for the LLM system prompt."""
        return [
            {"uri": m.uri_template, "description": m.description, "params": list(m.params)}
            for m in self._items
        ]


__all__ = [
    "FrozenWorld",
    "ResourceMeta",
    "ResourceNotFound",
    "ResourceRegistry",
]
