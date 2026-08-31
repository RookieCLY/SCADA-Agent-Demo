"""MockTool base class — the four-layer validation pipeline.

Per §1.4.7 of the development plan:
- L1 (SCHEMA_ERROR)   handled by Pydantic when args are validated
- L2 (*_NOT_FOUND)    handled by the Tool body querying the world
- L3 (TYPE_MISMATCH / ALREADY_BOUND / ...) — business rules
- L4 — actually write the world + log the diff

The base class enforces that every concrete subclass implements two static
methods — ``intended_entities`` and ``referenced_entities`` — at *class
definition* time, not at first call. This is the prerequisite for the §G.3
Cascade-Failure detector: without these, the tracer cannot tell which calls
caused which downstream failures.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel


# ============================================================ Error code system
class ErrorCode:
    OK = "OK"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    PAGE_NOT_FOUND = "PAGE_NOT_FOUND"
    WIDGET_NOT_FOUND = "WIDGET_NOT_FOUND"
    POINT_NOT_FOUND = "POINT_NOT_FOUND"
    ALARM_NOT_FOUND = "ALARM_NOT_FOUND"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    ALREADY_BOUND = "ALREADY_BOUND"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    BUSINESS_RULE = "BUSINESS_RULE"
    # Refused by the runtime safety policy (§4.7 "outer cage"), *not* by the
    # system prompt. A POLICY_DENIED call never reaches the tool handler, so
    # no world mutation is possible regardless of what the LLM was told.
    POLICY_DENIED = "POLICY_DENIED"


REFERENCE_ERROR_CODES = frozenset({
    ErrorCode.PAGE_NOT_FOUND,
    ErrorCode.WIDGET_NOT_FOUND,
    ErrorCode.POINT_NOT_FOUND,
    ErrorCode.ALARM_NOT_FOUND,
    ErrorCode.DEVICE_NOT_FOUND,
})


# ============================================================ Tool result
@dataclass
class ToolResult:
    ok: bool
    error_code: str
    error_msg: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    world_diff: dict[str, Any] | None = None


# ============================================================ MockTool base
class MockTool(ABC):
    """Concrete Tool actions inherit from this.

    Subclasses MUST:
      - Set ``name`` (snake_case identifier, matches ``(domain, action)`` reverse table)
      - Set ``domain`` (one of the Domain Tool names, e.g. ``"manage_alarms"``)
      - Set ``args_model`` to a Pydantic model class
      - Implement ``run(args, world)`` returning a ToolResult
      - Implement ``intended_entities`` and ``referenced_entities`` as ``@staticmethod``
    """

    name: ClassVar[str] = ""
    domain: ClassVar[str] = ""
    action: ClassVar[str] = ""
    description: ClassVar[str] = ""
    args_model: ClassVar[type[BaseModel]]
    examples: ClassVar[list[str]] = []
    required_state: ClassVar[set[str] | None] = None

    # ------- subclass-init enforcement (the bit that protects §G.3) ----------
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "_is_abstract", False):
            return
        for m in ("intended_entities", "referenced_entities"):
            sub = cls.__dict__.get(m)
            if sub is None or sub is getattr(MockTool, m, None):
                raise TypeError(
                    f"{cls.__name__} must implement static method {m!r} "
                    "(see §1.4.7 / §G.3 of the development plan)."
                )
            if not isinstance(sub, staticmethod):
                raise TypeError(
                    f"{cls.__name__}.{m} must be declared with @staticmethod."
                )
        if not cls.name:
            raise TypeError(f"{cls.__name__} must set class attribute `name`.")
        if not cls.domain:
            raise TypeError(f"{cls.__name__} must set class attribute `domain`.")
        if not cls.action:
            raise TypeError(f"{cls.__name__} must set class attribute `action`.")
        if not hasattr(cls, "args_model"):
            raise TypeError(f"{cls.__name__} must set class attribute `args_model`.")

    # ----------------------------------------- contract methods (subclasses)
    @staticmethod
    @abstractmethod
    def intended_entities(args: BaseModel) -> list[str]:
        """Entity IDs this call is meant to create or modify, e.g. ``["alarms.alarm_001"]``."""

    @staticmethod
    @abstractmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        """Entity IDs this call reads from the world, e.g. ``["points.TEMP_101"]``."""

    @abstractmethod
    def run(self, args: BaseModel, world: Any) -> ToolResult: ...


# ============================================================ Failure helper
def fail(code: str, msg: str) -> ToolResult:
    return ToolResult(ok=False, error_code=code, error_msg=msg)


def ok(data: dict[str, Any] | None = None, world_diff: dict[str, Any] | None = None) -> ToolResult:
    return ToolResult(ok=True, error_code=ErrorCode.OK, data=data or {}, world_diff=world_diff)


# ============================================================ Public symbols
__all__ = [
    "ErrorCode",
    "MockTool",
    "REFERENCE_ERROR_CODES",
    "ToolResult",
    "fail",
    "ok",
]
