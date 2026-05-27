"""Mock Tool layer — all tools route writes through the Mock World.

§1.4.7 of the development plan: every concrete MockTool subclass must declare
``intended_entities`` and ``referenced_entities`` as ``@staticmethod`` — the base
class enforces this at subclass-init time so cascade-failure detection can be
relied upon downstream.
"""
from tools import (
    deployment,
    manage_alarms,
    manage_graphics,
    manage_history,
    manage_pages,
    manage_points,
    manage_scripts,
)
from tools._base import (
    REFERENCE_ERROR_CODES,
    ErrorCode,
    MockTool,
    ToolResult,
    fail,
    ok,
)

__all__ = [
    "ErrorCode",
    "MockTool",
    "REFERENCE_ERROR_CODES",
    "ToolResult",
    "deployment",
    "fail",
    "manage_alarms",
    "manage_graphics",
    "manage_history",
    "manage_pages",
    "manage_points",
    "manage_scripts",
    "ok",
]
