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
    """Path expectations for one golden case.

    Three conventions make it possible to declare a trajectory for *every* case
    rather than only the handful with a single unambiguous solution:

    ``required_tools`` / ``required_actions``
        Index-aligned, and an entry may list alternatives separated by ``|``
        (``"create_text|create_widget"``) meaning *any one of these satisfies
        the step*. Many SCADA edits have two equally correct spellings — placing
        a text label is ``manage_graphics.create_text`` or
        ``manage_pages.create_widget`` — and without alternation a widened
        dataset would score valid runs as trajectory violations.

    ``forbidden_tools``
        Entries may name a domain (``"deployment"``), an atomic
        (``"delete_page"``), or an action; all three are matched. This is the
        per-case safety expectation: what the agent must *not* touch.

    ``allowed_terminal_states``
        When non-empty it replaces ``terminal_state``, and an entry prefixed
        with ``!`` is an *exclusion*, so a list of only exclusions means "any
        state except these". Needed because a clean stop in this runtime lands
        on whatever state the agent was in — the model emitting
        ``next_state: DONE`` is prompt compliance, not task completion, and it
        correlates with the config under test. Requiring a literal ``DONE``
        would have folded that artifact into every trajectory verdict.
    """

    model_config = ConfigDict(extra="forbid")

    min_steps: int = 1
    max_steps: int = 10
    required_tools: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    terminal_state: str = "DONE"
    allowed_terminal_states: list[str] = Field(default_factory=list)


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
