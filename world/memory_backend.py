"""In-memory MockWorld backend.

Serves as the single source of truth for both Tools (write) and Resources (read,
Phase 2). Per §1.4.8 the world is reset between test cases — one query, one
world instance, no state leakage.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from world._base import WorldStore
from world.models import (
    Alarm,
    Deployment,
    Device,
    HistoryConfig,
    Page,
    Point,
    Script,
    Widget,
)

MatchMode = Literal["strict", "subset", "key_fields"]


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts/BaseModels into dot-path leaves.

    Lists are kept whole — list-of-dicts comparison is delegated to the caller.
    """
    out: dict[str, Any] = {}
    if isinstance(obj, BaseModel):
        obj = obj.model_dump()
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict | BaseModel):
                out.update(_flatten(v, key))
            else:
                out[key] = v
    else:
        out[prefix] = obj
    return out


class MockWorld(BaseModel, WorldStore):
    """World container — pages, points, alarms, devices, project metadata."""

    pages: dict[str, Page] = Field(default_factory=dict)
    points: dict[str, Point] = Field(default_factory=dict)
    alarms: dict[str, Alarm] = Field(default_factory=dict)
    devices: dict[str, Device] = Field(default_factory=dict)
    histories: dict[str, HistoryConfig] = Field(default_factory=dict)
    scripts: dict[str, Script] = Field(default_factory=dict)
    deployments: dict[str, Deployment] = Field(default_factory=dict)
    project_meta: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------ Store
    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.model_dump())

    def restore(self, snap: dict[str, Any]) -> None:
        new = MockWorld.model_validate(snap)
        self.pages = new.pages
        self.points = new.points
        self.alarms = new.alarms
        self.devices = new.devices
        self.histories = new.histories
        self.scripts = new.scripts
        self.deployments = new.deployments
        self.project_meta = new.project_meta

    def reset(self) -> None:
        self.pages.clear()
        self.points.clear()
        self.alarms.clear()
        self.devices.clear()
        self.histories.clear()
        self.scripts.clear()
        self.deployments.clear()
        self.project_meta.clear()

    def diff(self, other: "WorldStore") -> dict[str, Any]:
        if not isinstance(other, MockWorld):
            raise TypeError("MockWorld.diff requires another MockWorld")
        a, b = _flatten(self.model_dump()), _flatten(other.model_dump())
        added_or_modified: dict[str, Any] = {}
        removed: list[str] = []
        for k, vb in b.items():
            if k not in a:
                added_or_modified[k] = vb
            elif a[k] != vb:
                added_or_modified[k] = vb
        for k in a:
            if k not in b:
                removed.append(k)
        return {"added_or_modified": added_or_modified, "removed": removed}

    # ------------------------------------------------------- helpers / utils
    def snapshot_key(self, dot_path: str) -> Any:
        """Resolve a dot-path against the current snapshot."""
        node: Any = self.model_dump()
        for part in dot_path.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def hash(self) -> str:
        """SHA-256 of the canonicalised state — used in trace world snapshots."""
        payload = json.dumps(self.snapshot(), sort_keys=True, default=str).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    # ----------------------------------------- terminal-state match against expected
    def match_against_expected(
        self,
        expected: dict[str, Any],
        match_mode: MatchMode = "subset",
        initial: "MockWorld | None" = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Compare against a Golden `expected_final_state_diff` block.

        Returns (matched, report). `expected` follows the schema from §3.4.1:
          {
            "match_mode": "subset" | "strict" | "key_fields",
            "added_or_modified": {dot_path: value, ...},
            "removed": [dot_path, ...],
            "unchanged_keys_must_remain": [dot_path, ...]
          }
        """
        diff = (initial or MockWorld()).diff(self)
        mode = expected.get("match_mode", match_mode)
        want_add = expected.get("added_or_modified", {})
        want_rem = set(expected.get("removed", []))
        unchanged = expected.get("unchanged_keys_must_remain", [])

        report: dict[str, Any] = {"mode": mode, "missing": [], "unexpected": [], "wrong_value": []}

        actual_add = diff["added_or_modified"]
        actual_rem = set(diff["removed"])

        for k, v in want_add.items():
            if k not in actual_add:
                report["missing"].append(k)
            elif actual_add[k] != v:
                report["wrong_value"].append({"key": k, "expected": v, "actual": actual_add[k]})

        if mode == "strict":
            for k in actual_add:
                if k not in want_add:
                    report["unexpected"].append(k)
            if actual_rem != want_rem:
                report["unexpected_rem"] = sorted(actual_rem ^ want_rem)
        elif mode in ("subset", "key_fields"):
            # subset: extra adds are OK; only specified keys are checked
            pass

        for k in unchanged:
            if k in actual_add or k in actual_rem:
                report["unexpected"].append(f"violated_unchanged:{k}")

        matched = not (report["missing"] or report["wrong_value"] or report["unexpected"])
        if mode == "strict" and report.get("unexpected_rem"):
            matched = False
        return matched, report


def deep_copy_world(w: MockWorld) -> MockWorld:
    """Convenience helper — initial-state preservation for trace bookkeeping."""
    return MockWorld.model_validate(w.snapshot())
