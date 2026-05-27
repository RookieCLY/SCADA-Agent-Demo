"""WorldStore interface. Phase 1 only ships the in-memory backend; SQLite and
Redis backends are placeholders for §1.4.8.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class WorldStore(ABC):
    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """Deep-copyable serialisation of the current world."""

    @abstractmethod
    def restore(self, snap: dict[str, Any]) -> None:
        """Replace state with `snap`."""

    @abstractmethod
    def diff(self, other: "WorldStore") -> dict[str, Any]:
        """Compute the entity-level diff against another world."""

    @abstractmethod
    def reset(self) -> None:
        """Clear all state — used for inter-test isolation."""
