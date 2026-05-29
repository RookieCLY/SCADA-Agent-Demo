"""Golden Dataset Schema definition."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExpectedFinalStateDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_mode: Literal["strict", "subset", "key_fields"] = "subset"
    added_or_modified: dict[str, Any] = Field(default_factory=dict)
    removed: list[str] = Field(default_factory=list)
    unchanged_keys_must_remain: list[str] = Field(default_factory=list)


class ExpectedTrajectory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_steps: int = 1
    max_steps: int = 10
    required_tools: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    terminal_state: str = "DONE"


GoldenBehavior = Literal["success", "fail_or_clarify", "ask_for_clarification", "reject"]
Complexity = Literal["simple", "medium", "complex"]


class GoldenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    query: str
    domain: str
    complexity: Complexity
    initial_world: dict[str, dict[str, Any]] = Field(default_factory=dict)
    expected_behavior: GoldenBehavior
    expected_final_state_diff: ExpectedFinalStateDiff
    expected_trajectory: ExpectedTrajectory | None = None
    expected_error_code: str | None = None
    expected_workflow_id: str | None = None
    expected_alternative: str | None = None
    rubric_hints: list[str] = Field(default_factory=list)


def load_golden_dataset(path: str | Path) -> list[GoldenRecord]:
    """Load golden dataset from a JSONL file."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(GoldenRecord.model_validate_json(line))
    return records
