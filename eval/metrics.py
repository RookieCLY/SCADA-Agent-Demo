"""Deterministic metrics for Golden Dataset traces.

This module implements the non-judge layers from the development plan:

- error-code and schema checks
- final-state diff matching
- trajectory and unified logical-tool scoring
- resource/read-before-write and cascade-failure diagnostics

LLM-as-Judge results remain a separate artifact. Aggregation can join these
rows with `judges.jsonl` later.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agent.tool_registry import ToolRegistry, build_default_registry
from eval.schema import ExpectedTrajectory, GoldenRecord, load_golden_dataset

REFERENCE_ERROR_SUFFIX = "_NOT_FOUND"
TYPE_MISMATCH_CODES = {"TYPE_MISMATCH", "ALREADY_BOUND"}
OK_CODES = {"OK", None, ""}


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            next_key = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                out.update(_flatten(value, next_key))
            else:
                out[next_key] = value
        return out
    return {prefix: obj}


def _same_path_or_child(path: str, expected: str) -> bool:
    return path == expected or path.startswith(f"{expected}.") or expected.startswith(f"{path}.")


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    if den == 0:
        return default
    return num / den


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _edit_distance(left: list[str], right: list[str]) -> int:
    if not left:
        return len(right)
    if not right:
        return len(left)
    prev = list(range(len(right) + 1))
    for i, left_item in enumerate(left, start=1):
        cur = [i]
        for j, right_item in enumerate(right, start=1):
            cost = 0 if left_item == right_item else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def _aggregate_world_diff(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    added_or_modified: dict[str, Any] = {}
    removed: set[str] = set()
    for call in tool_calls:
        world_diff = call.get("world_diff") or {}
        for path, value in _flatten(world_diff.get("added_or_modified", {})).items():
            added_or_modified[path] = value
            removed.discard(path)
        for path in world_diff.get("removed", []) or []:
            removed.add(path)
            for existing in list(added_or_modified):
                if _same_path_or_child(existing, path):
                    added_or_modified.pop(existing, None)
    return {"added_or_modified": added_or_modified, "removed": sorted(removed)}


ALIASABLE_KEY_FIELD_COLLECTIONS = {"alarms", "pages", "scripts"}


def _split_entity_path(path: str) -> tuple[str, str] | None:
	"""Split a flattened diff path into ``(entity_prefix, field_path)``.

	Most entities use ``collection.entity_id.field``. Page widgets are nested
	inside pages, so ``pages.page_id.widgets.widget_id.field`` is treated as a
	separate aliasable entity from the page itself.
	"""
	parts = path.split(".")
	if len(parts) >= 5 and parts[0] == "pages" and parts[2] == "widgets":
		return ".".join(parts[:4]), ".".join(parts[4:])
	if len(parts) < 3:
		return None
	return ".".join(parts[:2]), ".".join(parts[2:])


def _group_by_entity(flat: dict[str, Any]) -> dict[str, dict[str, Any]]:
	"""Group flattened ``collection.entity_id.field`` paths by entity prefix."""
	grouped: dict[str, dict[str, Any]] = {}
	for path, value in flat.items():
		split = _split_entity_path(path)
		if split is None:
			continue
		prefix, field_path = split
		grouped.setdefault(prefix, {})[field_path] = value
	return grouped


def _entity_alias_scope(prefix: str) -> str | None:
	"""Return the alias scope for an entity prefix, or ``None`` if unsafe."""
	parts = prefix.split(".")
	if len(parts) >= 4 and parts[0] == "pages" and parts[2] == "widgets":
		# Widget IDs may be generated, but only within the same page.
		return ".".join(parts[:3])
	if len(parts) >= 2 and parts[0] in ALIASABLE_KEY_FIELD_COLLECTIONS:
		return parts[0]
	return None


def _candidate_alias_scopes(prefix: str, entity_aliases: dict[str, str]) -> set[str]:
	"""Return acceptable actual scopes for alias candidates."""
	scope = _entity_alias_scope(prefix)
	if scope is None:
		return set()
	scopes = {scope}
	parts = prefix.split(".")
	if len(parts) >= 4 and parts[0] == "pages" and parts[2] == "widgets":
		page_prefix = ".".join(parts[:2])
		if page_prefix in entity_aliases:
			scopes.add(f"{entity_aliases[page_prefix]}.widgets")
	return scopes


def _entity_match_order(prefix: str) -> tuple[int, str]:
	"""Match parent pages before nested widgets so page aliases can cascade."""
	parts = prefix.split(".")
	if len(parts) >= 2 and parts[0] == "pages" and (len(parts) < 4 or parts[2] != "widgets"):
		return (0, prefix)
	if len(parts) >= 4 and parts[0] == "pages" and parts[2] == "widgets":
		return (1, prefix)
	return (2, prefix)


def _match_key_fields(
	want_add: dict[str, Any],
	actual_add: dict[str, Any],
) -> tuple[dict[str, str], list[str], list[dict[str, Any]], list[str]]:
	"""Resolve generated-entity aliases for ``key_fields`` matching.

	For each expected entity (e.g. ``alarms.alarm_PT101_hi``) try to find an
	actual entity in the same top-level collection whose values satisfy every
	expected key field exactly. This lets a model pick its own generated ID
	(``alarms.alarm_pt101_high``) without being penalised, as long as the
	semantic fields match.

	Returns ``(entity_aliases, matched_paths, wrong_value, ambiguous)``:
	  - ``entity_aliases``: expected prefix -> actual prefix, only when they differ.
	  - ``matched_paths``: expected flattened paths whose value was found.
	  - ``wrong_value``: expected paths whose value differs from the resolved entity.
	  - ``ambiguous``: expected prefixes that matched more than one actual entity.
	"""
	expected_entities = _group_by_entity(want_add)
	actual_entities = _group_by_entity(actual_add)

	entity_aliases: dict[str, str] = {}
	matched_paths: list[str] = []
	wrong_value: list[dict[str, Any]] = []
	ambiguous: list[str] = []

	for prefix in sorted(expected_entities, key=_entity_match_order):
		want_fields = expected_entities[prefix]
		alias_scopes = _candidate_alias_scopes(prefix, entity_aliases)
		actual_fields = actual_entities.get(prefix)

		# 1) literal prefix present with every expected field equal -> no alias.
		if actual_fields is not None and all(
			actual_fields.get(field_path) == value
			for field_path, value in want_fields.items()
		):
			matched_paths.extend(f"{prefix}.{field_path}" for field_path in want_fields)
			continue

		# 2) look for a unique actual entity in the same alias scope that
		#    satisfies every expected key field exactly.
		candidates = []
		if alias_scopes:
			candidates = [
				cand_prefix
				for cand_prefix, cand_fields in actual_entities.items()
				if _entity_alias_scope(cand_prefix) in alias_scopes
				and all(
					cand_fields.get(field_path) == value
					for field_path, value in want_fields.items()
				)
			]

		if len(candidates) == 1:
			alias = candidates[0]
			if alias != prefix:
				entity_aliases[prefix] = alias
			matched_paths.extend(f"{prefix}.{field_path}" for field_path in want_fields)
			continue

		if len(candidates) > 1:
			ambiguous.append(prefix)

		# 3) no unique alias -> diagnose against the literal entity if present;
		#    fields that are neither matched nor wrong are reported as missing
		#    by the caller.
		for field_path, value in want_fields.items():
			path = f"{prefix}.{field_path}"
			if actual_fields is not None and field_path in actual_fields:
				if actual_fields[field_path] != value:
					wrong_value.append(
						{"key": path, "expected": value, "actual": actual_fields[field_path]}
					)
				else:
					matched_paths.append(path)

	return entity_aliases, matched_paths, wrong_value, ambiguous


def _compare_final_state_from_diff(
	actual_diff: dict[str, Any],
	expected: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
	mode = expected.get("match_mode", "subset")
	want_add = expected.get("added_or_modified", {}) or {}
	want_removed = set(expected.get("removed", []) or [])
	unchanged = expected.get("unchanged_keys_must_remain", []) or []
	actual_add = actual_diff.get("added_or_modified", {}) or {}
	actual_removed = set(actual_diff.get("removed", []) or [])

	report: dict[str, Any] = {
		"mode": mode,
		"missing": [],
		"wrong_value": [],
		"unexpected": [],
		"matched": [],
	}

	if mode == "key_fields":
		# ID-tolerant matching: a model may generate its own entity ID, so we
		# alias expected entities to semantically-equal actual entities.
		entity_aliases, matched_paths, wrong_value, ambiguous = _match_key_fields(
			want_add, actual_add
		)
		resolved = set(matched_paths) | {item["key"] for item in wrong_value}
		report["matched"] = matched_paths
		report["wrong_value"].extend(wrong_value)
		report["missing"].extend(path for path in want_add if path not in resolved)
		if entity_aliases:
			report["entity_aliases"] = entity_aliases
		if ambiguous:
			report["ambiguous_entity_match"] = ambiguous
	else:
		for path, expected_value in want_add.items():
			if path not in actual_add:
				report["missing"].append(path)
			elif actual_add[path] != expected_value:
				report["wrong_value"].append(
					{"key": path, "expected": expected_value, "actual": actual_add[path]}
				)
			else:
				report["matched"].append(path)

	for path in want_removed:
		if path not in actual_removed:
			report["missing"].append(f"removed:{path}")

	if mode == "strict":
		for path in actual_add:
			if path not in want_add:
				report["unexpected"].append(path)
		unexpected_removed = sorted(actual_removed ^ want_removed)
		if unexpected_removed:
			report["unexpected_removed"] = unexpected_removed

	for unchanged_path in unchanged:
		for changed_path in list(actual_add) + list(actual_removed):
			if _same_path_or_child(changed_path, unchanged_path):
				report["unexpected"].append(f"violated_unchanged:{unchanged_path}")
				break

	matched = not report["missing"] and not report["wrong_value"] and not report["unexpected"]
	if mode == "strict" and report.get("unexpected_removed"):
		matched = False
	if mode == "key_fields" and report.get("ambiguous_entity_match"):
		matched = False
	return matched, report


def _final_state_match(
    trace: dict[str, Any],
    golden: GoldenRecord,
    tool_calls: list[dict[str, Any]],
) -> tuple[bool, str, dict[str, Any], dict[str, Any]]:
    expected = golden.expected_final_state_diff.model_dump(mode="json")
    match_mode = expected.get("match_mode", "subset")
    snapshots = trace.get("world_snapshots", {}) or {}

    if isinstance(snapshots.get("final_state_match"), bool):
        return (
            snapshots["final_state_match"],
            snapshots.get("match_mode") or match_mode,
            snapshots.get("diff_against_expected") or {},
            snapshots.get("actual_diff") or {},
        )

    actual_diff = _aggregate_world_diff(tool_calls)
    matched, report = _compare_final_state_from_diff(actual_diff, expected)
    return matched, match_mode, report, actual_diff


def _logical_from_call(
    call: dict[str, Any],
    registry: ToolRegistry,
) -> tuple[str | None, str | None, bool]:
    selected = call.get("selected")
    action = call.get("action") or (call.get("args") or {}).get("action")
    if not isinstance(selected, str):
        return None, None, False
    if selected.startswith("workflow:"):
        handler_name = selected.rsplit(".", maxsplit=1)[-1]
        return "workflow", handler_name, True

    try:
        domain = registry.domain(selected)
    except KeyError:
        domain = None
    if domain is not None:
        if isinstance(action, str) and action in domain.actions:
            return selected, action, True
        return selected, action if isinstance(action, str) else None, False

    try:
        domain_name, action_name = registry.lookup(selected)
    except KeyError:
        return selected, action if isinstance(action, str) else None, False
    return domain_name, action_name, True


def _expected_logical_tools(
    golden: GoldenRecord,
    registry: ToolRegistry,
) -> list[tuple[str, str]]:
    trajectory = golden.expected_trajectory
    if trajectory is None:
        return []

    required_tools = trajectory.required_tools
    required_actions = trajectory.required_actions
    pairs: list[tuple[str, str]] = []

    for idx, action in enumerate(required_actions):
        tool_hint = required_tools[idx] if idx < len(required_tools) else None
        # An alternation entry ("create_text|create_widget") names several
        # acceptable spellings of one step; there is no single domain to resolve
        # it to, so keep it verbatim and let ``_logical_matches`` expand it. A
        # multi-domain hint degrades to the "*" wildcard the matcher already
        # understands.
        if len(_alts(action)) > 1 or (tool_hint and len(_alts(tool_hint)) > 1):
            pairs.append((tool_hint or "*", action))
            continue
        if tool_hint:
            try:
                domain = registry.domain(tool_hint)
                if action in domain.actions:
                    pairs.append((tool_hint, action))
                    continue
            except KeyError:
                pass
            try:
                domain_name, action_name = registry.lookup(tool_hint)
                pairs.append((domain_name, action_name))
                continue
            except KeyError:
                pass

        matching_domains = [
            domain.name for domain in registry.all_domains() if action in domain.actions
        ]
        if len(matching_domains) == 1:
            pairs.append((matching_domains[0], action))
        else:
            pairs.append(("*", action))
    return pairs


def _alts(entry: str) -> set[str]:
    """Split a ``"a|b"`` alternation entry into its alternatives.

    Entries without a ``|`` yield a one-element set, so every pre-existing
    annotation behaves exactly as before.
    """
    return {part.strip() for part in entry.split("|") if part.strip()}


def _logical_matches(actual: tuple[str, str], expected: tuple[str, str]) -> bool:
    expected_domains = _alts(expected[0])
    return ("*" in expected_domains or actual[0] in expected_domains) and actual[1] in _alts(
        expected[1]
    )


def _score_tool_selection(
    trace: dict[str, Any],
    golden: GoldenRecord,
    registry: ToolRegistry,
) -> dict[str, Any]:
    raw_calls = trace.get("tool_calls", []) or []
    logical_calls: list[tuple[str, str]] = []
    invalid_count = 0
    domain_matches = 0
    action_matches_when_domain_matches = 0

    expected = _expected_logical_tools(golden, registry)
    # Expand "a|b" alternations so the per-call domain/action accuracy counters
    # credit every spelling the case accepts, not just the first one listed.
    expected_domains = {alt for domain, _ in expected for alt in _alts(domain)}
    expected_actions_by_domain: dict[str, set[str]] = {}
    for domain, action in expected:
        for domain_alt in _alts(domain):
            expected_actions_by_domain.setdefault(domain_alt, set()).update(_alts(action))

    for call in raw_calls:
        domain, action, valid = _logical_from_call(call, registry)
        if not valid:
            invalid_count += 1
            continue
        if domain is None or action is None:
            invalid_count += 1
            continue
        if domain == "workflow":
            continue
        logical_calls.append((domain, action))
        domain_matched = domain in expected_domains or "*" in expected_domains
        if domain_matched:
            domain_matches += 1
            valid_actions = expected_actions_by_domain.get(domain, set()) | expected_actions_by_domain.get("*", set())
            if action in valid_actions:
                action_matches_when_domain_matches += 1

    matched_expected_indices: set[int] = set()
    correct_call_count = 0
    for actual in logical_calls:
        match_index = next(
            (
                idx
                for idx, expected_pair in enumerate(expected)
                if idx not in matched_expected_indices and _logical_matches(actual, expected_pair)
            ),
            None,
        )
        if match_index is not None:
            matched_expected_indices.add(match_index)
            correct_call_count += 1

    precision = _safe_div(correct_call_count, len(logical_calls), default=1.0 if not expected else 0.0)
    recall = _safe_div(len(matched_expected_indices), len(expected), default=1.0 if not logical_calls else 0.0)

    action_sequence = [action for _, action in logical_calls]
    expected_sequence = [action for _, action in expected]
    order_distance = _edit_distance(expected_sequence, action_sequence)
    order_correctness = 1.0 - _safe_div(
        order_distance,
        max(len(expected_sequence), len(action_sequence), 1),
        default=0.0,
    )

    return {
        "actual_logical_tools": [{"domain": domain, "action": action} for domain, action in logical_calls],
        "expected_logical_tools": [{"domain": domain, "action": action} for domain, action in expected],
        "tool_selection_precision": precision,
        "tool_selection_recall": recall,
        "tool_selection_f1": _f1(precision, recall),
        "hallucinated_tool_rate": _safe_div(invalid_count, len(raw_calls), default=0.0),
        "domain_match_accuracy": _safe_div(domain_matches, len(logical_calls), default=1.0 if not expected else 0.0),
        "action_match_accuracy": _safe_div(
            action_matches_when_domain_matches,
            domain_matches,
            default=1.0 if not expected else 0.0,
        ),
        "order_distance": order_distance,
        "order_correctness": max(0.0, order_correctness),
    }


def _error_metrics(tool_calls: list[dict[str, Any]], expected_error_code: str | None) -> dict[str, Any]:
    total = len(tool_calls)
    error_codes = [call.get("error_code") for call in tool_calls]
    non_ok_codes = [code for code in error_codes if code not in OK_CODES]
    distribution = Counter(str(code) for code in non_ok_codes)

    if expected_error_code is None:
        expected_error_code_match = len(non_ok_codes) == 0
    else:
        expected_error_code_match = expected_error_code in non_ok_codes

    return {
        "observed_error_codes": non_ok_codes,
        "expected_error_code_match": expected_error_code_match,
        "error_code_distribution": dict(distribution),
        "schema_error_rate": _safe_div(distribution.get("SCHEMA_ERROR", 0), total, default=0.0),
        "reference_error_rate": _safe_div(
            sum(count for code, count in distribution.items() if code.endswith(REFERENCE_ERROR_SUFFIX)),
            total,
            default=0.0,
        ),
        "type_mismatch_rate": _safe_div(
            sum(count for code, count in distribution.items() if code in TYPE_MISMATCH_CODES),
            total,
            default=0.0,
        ),
    }


def _terminal_state_ok(actual: Any, trajectory: ExpectedTrajectory) -> bool:
    """Whether the run ended in a state the case accepts.

    ``allowed_terminal_states`` supports ``!STATE`` exclusions so a case can say
    "any resting state except these" — see ``ExpectedTrajectory`` for why an
    exact ``DONE`` is the wrong expectation in this runtime. With the field empty
    this is the original exact comparison against ``terminal_state``.
    """
    allowed = trajectory.allowed_terminal_states
    if not allowed:
        return actual == trajectory.terminal_state
    excluded = {entry[1:] for entry in allowed if entry.startswith("!")}
    included = {entry for entry in allowed if not entry.startswith("!")}
    if actual in excluded:
        return False
    return not included or actual in included


def _all_required_present(entries: list[str], available: set[Any]) -> bool:
    """Every entry satisfied, where an entry is satisfied by any of its ``|`` alternatives."""
    return all(bool(_alts(entry) & available) for entry in entries)


def _required_ratio(entries: list[str], available: set[Any]) -> float:
    if not entries:
        return 1.0
    return sum(1 for entry in entries if _alts(entry) & available) / len(entries)


def _step_efficiency(ideal_steps: int | None, step_count: int) -> float | None:
    """``ideal_steps / step_count``, clamped to 1.0.

    ``None`` ideal means the case declares no expectation at all — the metric is
    genuinely unavailable and must stay ``None`` rather than default to something
    that silently averages in.

    ``ideal_steps == 0`` means the case expects *no* tool calls (a reject or
    clarification case). Making no calls is then perfectly efficient, and making
    any is not — without this branch such cases divided by an ideal of 1 and
    scored a spurious 1.0 for a single wrong call.
    """
    if ideal_steps is None:
        return None
    if ideal_steps == 0:
        return 1.0 if step_count == 0 else 0.0
    if step_count == 0:
        return 0.0
    return min(1.0, ideal_steps / step_count)


def _ideal_steps_from_expectations(golden: GoldenRecord) -> int | None:
    """Lower bound on the tool calls a case needs, from its declared expectations.

    Used only when ``expected_trajectory`` is absent. Counts the distinct
    entities in ``expected_final_state_diff`` — each needs at least one call to
    create — plus removals, which each need a delete. Widget entities nest inside
    pages and are counted separately, matching ``_split_entity_path``.

    Returns 0 for cases whose expected behaviour is to *not* act (reject /
    clarification), and ``None`` when the case declares no expectations, so the
    caller can leave the metric unavailable instead of inventing one.
    """
    if golden.expected_behavior in {"reject", "ask_for_clarification"}:
        return 0
    want = golden.expected_final_state_diff
    entities = {
        prefix
        for prefix, _ in (
            split for split in (_split_entity_path(p) for p in _flatten(want.added_or_modified))
            if split is not None
        )
    }
    removed = set(want.removed or [])
    total = len(entities) + len(removed)
    return total or None


def _trajectory_metrics(
    trace: dict[str, Any],
    golden: GoldenRecord,
    tool_calls: list[dict[str, Any]],
    selection: dict[str, Any],
) -> dict[str, Any]:
    trajectory = golden.expected_trajectory
    step_count = len(tool_calls)
    execution = trace.get("execution", {}) or {}
    loop_stuck = bool(execution.get("early_terminated")) or execution.get("terminal_state") == "UNKNOWN"

    if trajectory is None:
        # Every case in ``eval/golden_dataset.jsonl`` now declares an
        # ``expected_trajectory``, so this branch is reached only by ad-hoc or
        # user-authored cases. It used to cover 94 of 106 records, which meant
        # ``step_efficiency`` was averaged over ~11% of the dataset: any
        # cross-config comparison built on it was reading 24 rows and reporting
        # them as if they were 212 — a difference of one case moved it by
        # several points.
        #
        # ``expected_final_state_diff`` is declared far more often (65 of 106) and
        # is already ground truth, so it gives an honest lower bound on the ideal
        # step count: every distinct entity the case expects to exist needs at
        # least one tool call to create it. That is a *bound*, not a trajectory —
        # it cannot say which tools or in what order — so it is used only for
        # ``step_efficiency`` and never to synthesise ``trajectory_match``.
        # Nothing is fabricated: cases that declare neither still return None.
        ideal = _ideal_steps_from_expectations(golden)
        return {
            "trajectory_match": None,
            "terminal_state_match": None,
            "required_tools_match": None,
            "required_actions_match": None,
            "forbidden_tools_violated": False,
            "step_count": step_count,
            "step_efficiency": _step_efficiency(ideal, step_count),
            "step_efficiency_basis": None if ideal is None else "final_state_diff",
            "loop_stuck": loop_stuck,
            "trajectory_score": 1.0,
        }

    actual_tools = {item["domain"] for item in selection["actual_logical_tools"]}
    actual_actions = {item["action"] for item in selection["actual_logical_tools"]}
    raw_selected = {call.get("selected") for call in tool_calls}

    forbidden_tools = set(trajectory.forbidden_tools)
    # ``actual_actions`` belongs in the forbidden check as much as the domains do.
    # Without it a forbidden atomic such as ``deploy_project`` was only caught in
    # *flat* mode, where ``raw_selected`` holds the atomic name; in hierarchical
    # mode the same call is recorded as ``selected="deployment"`` with
    # ``action="deploy_project"`` and slipped through unflagged. The safety
    # expectation has to bite in both tool surfaces or it cannot be compared
    # across the configs that differ precisely in tool surface.
    observed = actual_tools | actual_actions | raw_selected
    terminal_state_match = _terminal_state_ok(execution.get("terminal_state"), trajectory)
    required_tools_match = _all_required_present(trajectory.required_tools, actual_tools | raw_selected)
    required_actions_match = _all_required_present(
        trajectory.required_actions, actual_actions | raw_selected
    )
    forbidden_tools_violated = bool(forbidden_tools & observed)
    step_bounds_match = trajectory.min_steps <= step_count <= trajectory.max_steps
    trajectory_match = (
        terminal_state_match
        and required_tools_match
        and required_actions_match
        and not forbidden_tools_violated
        and step_bounds_match
    )

    tools_ratio = _required_ratio(trajectory.required_tools, actual_tools | raw_selected)
    actions_ratio = _required_ratio(trajectory.required_actions, actual_actions | raw_selected)
    trajectory_score = 0.5 * tools_ratio + 0.5 * actions_ratio
    if forbidden_tools_violated:
        trajectory_score = 0.0

    # A case that requires no actions expects the agent *not* to act (reject /
    # clarification). Its ideal step count is 0, and ``_step_efficiency`` scores
    # zero calls as perfect. Clamping to 1 here — as this did before reject cases
    # carried trajectories — would have scored every correct refusal 0.0 for
    # efficiency, penalising exactly the behaviour the case rewards.
    if not trajectory.required_actions and golden.expected_behavior in {
        "reject",
        "ask_for_clarification",
    }:
        ideal_steps = 0
    elif not trajectory.required_actions and golden.expected_behavior == "fail_or_clarify":
        # ``fail_or_clarify`` admits *two* correct strategies with different step
        # counts: attempt the operation and report the error the case expects, or
        # ask before acting. There is no single ideal, and picking one scores the
        # other strategy 0.0 — with ``max(..., 1)`` below, a correct clarification
        # on golden-044 / -076 scored 0.0 while the arm that clarified most was
        # the one being measured. These cases also decline to constrain the count
        # themselves (``min_steps: 0, max_steps: 8``), so the honest answer is the
        # one ``_step_efficiency`` already defines for "no expectation declared".
        ideal_steps = None
    else:
        ideal_steps = max(len(trajectory.required_actions), trajectory.min_steps, 1)

    return {
        "trajectory_match": trajectory_match,
        "terminal_state_match": terminal_state_match,
        "required_tools_match": required_tools_match,
        "required_actions_match": required_actions_match,
        "forbidden_tools_violated": forbidden_tools_violated,
        "step_bounds_match": step_bounds_match,
        "step_count": step_count,
        "step_efficiency": _step_efficiency(ideal_steps, step_count),
        # Mirrors the ``final_state_diff`` branch: no basis is recorded when the
        # case declares no ideal, so a row with a null efficiency says *why*.
        "step_efficiency_basis": None if ideal_steps is None else "trajectory",
        "loop_stuck": loop_stuck,
        "trajectory_score": trajectory_score,
    }


def _parameter_metrics(
	tool_calls: list[dict[str, Any]],
	final_state_match: bool,
	final_state_report: dict[str, Any],
	actual_diff: dict[str, Any],
	golden: GoldenRecord,
) -> dict[str, Any]:
	total = len(tool_calls)
	valid_count = sum(1 for call in tool_calls if call.get("schema_valid") is True)
	schema_violations = sum(
		1
		for call in tool_calls
		if call.get("schema_valid") is False or call.get("error_code") == "SCHEMA_ERROR"
	)

	want_add = golden.expected_final_state_diff.added_or_modified
	if want_add:
		matched_paths = final_state_report.get("matched")
		if matched_paths is not None:
			# Alias-aware: the comparator already resolved generated IDs, so
			# count expected paths it confirmed (covers strict/subset/key_fields).
			matched = len(set(matched_paths) & set(want_add))
		else:
			# Fallback for precomputed snapshots without a matched list.
			actual_add = actual_diff.get("added_or_modified", {}) or {}
			matched = sum(1 for path, value in want_add.items() if actual_add.get(path) == value)
		parameter_match = matched / len(want_add)
	else:
		parameter_match = 1.0 if final_state_match else 0.0

	return {
		"parameter_validity": _safe_div(valid_count, total, default=1.0),
		"parameter_match": parameter_match,
		"schema_violation_rate": _safe_div(schema_violations, total, default=0.0),
		"final_state_missing_count": len(final_state_report.get("missing", []) or []),
		"final_state_wrong_value_count": len(final_state_report.get("wrong_value", []) or []),
		"final_state_unexpected_count": len(final_state_report.get("unexpected", []) or []),
	}


def _detect_cascade_failures(tool_calls: list[dict[str, Any]]) -> int:
    failed_intended_entities: dict[str, int] = {}
    cascade_count = 0

    for index, call in enumerate(tool_calls):
        if call.get("result_ok") is not False:
            continue

        for entity_id in call.get("intended_entities") or []:
            failed_intended_entities[entity_id] = index

        error_code = call.get("error_code")
        if not (isinstance(error_code, str) and error_code.endswith(REFERENCE_ERROR_SUFFIX)):
            continue

        referenced = call.get("referenced_entities") or []
        if any(entity_id in failed_intended_entities for entity_id in referenced):
            cascade_count += 1

    return cascade_count


def _resource_query_before_action(trace: dict[str, Any], tool_calls: list[dict[str, Any]]) -> bool | None:
    write_turns: list[int] = []
    for call in tool_calls:
        world_diff = call.get("world_diff") or {}
        if world_diff.get("added_or_modified") or world_diff.get("removed"):
            turn = call.get("turn")
            if isinstance(turn, int):
                write_turns.append(turn)

    if not write_turns:
        return None

    first_write_turn = min(write_turns)
    for read in trace.get("resource_reads", []) or []:
        turn = read.get("turn")
        if isinstance(turn, int) and turn <= first_write_turn:
            return True
    return False


def _task_success_deterministic(
    golden: GoldenRecord,
    *,
    final_state_match: bool,
    expected_error_code_match: bool,
    trajectory_match: bool | None,
    tool_calls: list[dict[str, Any]],
) -> bool:
    no_world_mutation = not any(
        (call.get("world_diff") or {}).get("added_or_modified")
        or (call.get("world_diff") or {}).get("removed")
        for call in tool_calls
    )
    trajectory_ok = True if trajectory_match is None else trajectory_match

    if golden.expected_behavior == "success":
        return final_state_match and expected_error_code_match and trajectory_ok
    if golden.expected_behavior in {"reject", "ask_for_clarification"}:
        return final_state_match and no_world_mutation and trajectory_ok
    if golden.expected_behavior == "fail_or_clarify":
        return final_state_match and (expected_error_code_match or no_world_mutation) and trajectory_ok
    return False


def _no_world_mutation(tool_calls: list[dict[str, Any]]) -> bool:
    return not any(
        (call.get("world_diff") or {}).get("added_or_modified")
        or (call.get("world_diff") or {}).get("removed")
        for call in tool_calls
    )


def _clamp01(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and not math.isnan(value):
        return max(0.0, min(1.0, float(value)))
    return default


_WORKFLOW_ID_ALIASES = {
    "alarm_config": "alarm_config",
    "chemical_production_screen": "chemical_screen",
    "chemical_screen": "chemical_screen",
    "deployment_check": "deployment_check",
    "graphics_layout": "graphics_layout",
    "history_query": "history_query",
    "point_binding": "point_binding",
    "point_creation": "point_creation",
    "pump_station_screen": "pump_station_screen",
    "script_config": "script_config",
}


def _normalize_workflow_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return _WORKFLOW_ID_ALIASES.get(text, text)


def _success_breakdown(
    golden: GoldenRecord,
    *,
    final_state_match: bool,
    expected_error_code_match: bool,
    trajectory_match: bool | None,
    workflow_hit: bool | None,
    parameter_validity: float,
    parameter_match: float,
    tool_selection_f1: float,
    step_efficiency: float,
    tool_calls: list[dict[str, Any]],
    state_score: float,
    trajectory_score: float,
) -> dict[str, Any]:
    """Return layered success metrics beyond strict all-or-nothing success."""
    no_world_mutation = _no_world_mutation(tool_calls)
    trajectory_success = True if trajectory_match is None else bool(trajectory_match)
    workflow_compliance = True if workflow_hit is None else bool(workflow_hit)
    constraint_safe_success = no_world_mutation if golden.expected_behavior in {
        "reject",
        "ask_for_clarification",
        "fail_or_clarify",
    } else True

    if golden.expected_behavior == "success":
        behavior_success = final_state_match and expected_error_code_match
        functional_success = behavior_success and parameter_validity == 1.0
    elif golden.expected_behavior in {"reject", "ask_for_clarification"}:
        behavior_success = final_state_match and no_world_mutation
        functional_success = behavior_success
    elif golden.expected_behavior == "fail_or_clarify":
        behavior_success = final_state_match and (expected_error_code_match or no_world_mutation)
        functional_success = behavior_success
    else:
        behavior_success = False
        functional_success = False

    strict_success = functional_success and trajectory_success
    weighted_success = (
        0.40 * state_score
        + 0.25 * _clamp01(parameter_match)
        + 0.20 * _clamp01(tool_selection_f1)
        + 0.10 * _clamp01(step_efficiency)
        + 0.05 * trajectory_score
    )

    return {
        "functional_success": functional_success,
        "behavior_success": behavior_success,
        "trajectory_success": trajectory_success,
        "workflow_compliance": workflow_compliance,
        "constraint_safe_success": constraint_safe_success,
        "strict_success": strict_success,
        "weighted_success": weighted_success,
    }


def _is_call_out_of_scope(call: dict[str, Any]) -> bool:
    if call.get("error_code") == "OUT_OF_SCOPE":
        return True
    selected = call.get("selected")
    action = call.get("action")
    visible_tools = call.get("visible_tools")
    if selected and selected.startswith("workflow:"):
        return False
    if not isinstance(visible_tools, list) or not visible_tools:
        return False
    for vt in visible_tools:
        # ``visible_tools`` is normally a list of descriptors
        # (``{"name": ..., "allowed_actions": [...]}``), but some traces record
        # it as a bare list of tool-name strings — the superseded orchestrator
        # in ``agent_old/`` does this on two of its three ToolCallRecord sites.
        # Treat a string entry as a descriptor with no action restriction:
        # the name being visible is all the information that trace carries, and
        # crashing here would make those runs unscoreable rather than merely
        # coarser.
        if isinstance(vt, str):
            if vt == selected:
                return False
            continue
        if not isinstance(vt, dict):
            continue
        if vt.get("name") == selected:
            # A descriptor with **no** ``allowed_actions`` key is a *flat* tool
            # surface entry: it names the atomic itself, and there are no
            # sub-actions to restrict — so visibility is the entire check.
            #
            # Distinguishing that from "``allowed_actions`` present but the
            # action is not in it" matters a great deal. Previously the missing
            # key defaulted to ``[]``, and since the dispatcher fills in
            # ``action`` for flat calls too (from the registry reverse table),
            # *every* flat-mode call fell through and was scored out-of-scope.
            # That is why config A reported an out_of_scope_tool_rate of 0.786
            # while its runtime recorded zero OUT_OF_SCOPE errors across 557
            # calls. The inflation hits exactly the flat baseline the paper
            # compares the hierarchical configs against.
            if "allowed_actions" not in vt:
                return False
            allowed_actions = vt.get("allowed_actions") or []
            if action:
                if action in allowed_actions:
                    return False
            else:
                return False
    return True


def evaluate_trace(
    trace: dict[str, Any],
    golden: GoldenRecord,
    registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    registry = registry or build_default_registry()
    tool_calls = trace.get("tool_calls", []) or []

    final_state_match, match_mode, final_state_report, actual_diff = _final_state_match(
        trace, golden, tool_calls
    )
    selection = _score_tool_selection(trace, golden, registry)
    errors = _error_metrics(tool_calls, golden.expected_error_code)
    trajectory = _trajectory_metrics(trace, golden, tool_calls, selection)
    parameters = _parameter_metrics(
        tool_calls,
        final_state_match,
        final_state_report,
        actual_diff,
        golden,
    )
    cascade_count = _detect_cascade_failures(tool_calls)
    resource_before_action = _resource_query_before_action(trace, tool_calls)
    visible_counts = [call.get("visible_count") for call in tool_calls if isinstance(call.get("visible_count"), int)]
    llm_calls = trace.get("llm_calls", []) or []
    totals = trace.get("totals", {}) or {}
    execution = trace.get("execution", {}) or {}
    experiment = trace.get("experiment", {}) or {}
    query = trace.get("query", {}) or {}
    workflow = trace.get("workflow", {}) or {}

    selected_workflow = workflow.get("selected_workflow")
    workflow_hit = (
        None
        if golden.expected_workflow_id is None
        else _normalize_workflow_id(selected_workflow) == _normalize_workflow_id(golden.expected_workflow_id)
    )
    strict_task_success = _task_success_deterministic(
        golden,
        final_state_match=final_state_match,
        expected_error_code_match=errors["expected_error_code_match"],
        trajectory_match=trajectory["trajectory_match"],
        tool_calls=tool_calls,
    )
    matched_keys = final_state_report.get("matched", []) or []
    missing_keys = final_state_report.get("missing", []) or []
    wrong_keys = final_state_report.get("wrong_value", []) or []
    unexpected_keys = final_state_report.get("unexpected", []) or []
    total_keys = len(matched_keys) + len(missing_keys) + len(wrong_keys) + len(unexpected_keys)
    state_score = len(matched_keys) / total_keys if total_keys > 0 else (1.0 if final_state_match else 0.0)

    success = _success_breakdown(
        golden,
        final_state_match=final_state_match,
        expected_error_code_match=errors["expected_error_code_match"],
        trajectory_match=trajectory["trajectory_match"],
        workflow_hit=workflow_hit,
        parameter_validity=parameters["parameter_validity"],
        parameter_match=parameters["parameter_match"],
        tool_selection_f1=selection["tool_selection_f1"],
        step_efficiency=trajectory["step_efficiency"],
        tool_calls=tool_calls,
        state_score=state_score,
        trajectory_score=trajectory["trajectory_score"],
    )
    task_success = success["functional_success"]

    return {
        "trace_id": trace.get("trace_id"),
        "config_name": experiment.get("config_name"),
        "model": experiment.get("model"),
        "golden_id": query.get("golden_id"),
        "rep": experiment.get("rep_index"),
        "seed": experiment.get("seed"),
        "complexity": query.get("complexity"),
        "domain": query.get("domain"),
        "expected_behavior": golden.expected_behavior,
        "expected_workflow_id": golden.expected_workflow_id,
        "selected_workflow": selected_workflow,
        "workflow_hit": workflow_hit,
        "visible_count_mean": sum(visible_counts) / len(visible_counts) if visible_counts else 0.0,
        "tool_selection_precision": selection["tool_selection_precision"],
        "tool_selection_recall": selection["tool_selection_recall"],
        "tool_selection_f1": selection["tool_selection_f1"],
        "hallucinated": selection["hallucinated_tool_rate"] > 0,
        "hallucinated_tool_rate": selection["hallucinated_tool_rate"],
        "out_of_scope": any(_is_call_out_of_scope(call) for call in tool_calls),
        "out_of_scope_tool_rate": _safe_div(
            sum(1 for call in tool_calls if _is_call_out_of_scope(call)),
            len(tool_calls),
            default=0.0,
        ),
        "domain_match_accuracy": selection["domain_match_accuracy"],
        "action_match_accuracy": selection["action_match_accuracy"],
        "param_valid": parameters["parameter_validity"] == 1.0,
        "parameter_validity": parameters["parameter_validity"],
        "parameter_match": parameters["parameter_match"],
        "schema_violation_rate": parameters["schema_violation_rate"],
        "task_success": task_success,
        "task_success_deterministic": strict_task_success,
        # Whether this case declares an ``expected_trajectory`` at all.
        # ``trajectory_success`` defaults to True without one, so for such cases
        # ``strict_success`` is *identical* to ``functional_success`` rather than
        # stricter. All 106 shipped golden cases now declare one; the flag stays
        # so an aggregation over an ad-hoc or trimmed dataset can still say how
        # much of it the trajectory columns actually cover.
        "trajectory_available": golden.expected_trajectory is not None,
        **success,
        "final_state_match": final_state_match,
        "match_mode": match_mode,
        "final_state_report": final_state_report,
        "expected_error_code": golden.expected_error_code,
        **errors,
        # A single representative error code for the failure taxonomy. The
        # parquet schema and make_report.py both expect a top-level ``error_code``
        # but ``_error_metrics`` only ever emitted ``observed_error_codes`` /
        # ``error_code_distribution``, so the column was always null and every
        # failure rendered as "错误码: UNKNOWN". We surface the last non-OK code
        # (typically the one that ended the run), or "OK" when nothing failed.
        "error_code": (errors["observed_error_codes"][-1] if errors["observed_error_codes"] else "OK"),
        **trajectory,
        "order_distance": selection["order_distance"],
        "order_correctness": selection["order_correctness"],
        "cascade_failure_count": cascade_count,
        "cascade_failure_rate": _safe_div(cascade_count, len(tool_calls), default=0.0),
        "resource_query_before_action": resource_before_action,
        "input_tokens": totals.get("input_tokens", sum(call.get("input_tokens", 0) for call in llm_calls)),
        "output_tokens": totals.get("output_tokens", sum(call.get("output_tokens", 0) for call in llm_calls)),
        "cost_usd": totals.get("cost_usd", 0.0),
        "e2e_latency_ms": totals.get("e2e_latency_ms"),
        "latency_per_turn_ms": (
            totals.get("e2e_latency_ms") / execution.get("total_turns")
            if execution.get("total_turns") and totals.get("e2e_latency_ms") is not None
            else None
        ),
        "actual_logical_tools": selection["actual_logical_tools"],
        "expected_logical_tools": selection["expected_logical_tools"],
        "actual_world_diff": actual_diff,
        "judge_completion": None,
        "judge_tool": None,
        "judge_param": None,
        "judge_efficiency": None,
    }


def evaluate_traces(
    traces: Iterable[dict[str, Any]],
    golden_records: Iterable[GoldenRecord],
    registry: ToolRegistry | None = None,
    *,
    skip_missing_golden: bool = False,
) -> list[dict[str, Any]]:
    registry = registry or build_default_registry()
    golden_by_id = {record.id: record for record in golden_records}
    rows: list[dict[str, Any]] = []

    for trace in traces:
        golden_id = (trace.get("query") or {}).get("golden_id")
        if golden_id not in golden_by_id:
            if skip_missing_golden:
                continue
            raise KeyError(f"Trace references unknown golden_id: {golden_id!r}")
        rows.append(evaluate_trace(trace, golden_by_id[golden_id], registry))
    return rows


def aggregate_summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    if not rows:
        return {"n": 0}

    numeric_fields = [
        "tool_selection_precision",
        "tool_selection_recall",
        "tool_selection_f1",
        "hallucinated_tool_rate",
        "out_of_scope_tool_rate",
        "domain_match_accuracy",
        "action_match_accuracy",
        "parameter_validity",
        "parameter_match",
        "schema_violation_rate",
        "step_count",
        "step_efficiency",
        "order_correctness",
        "cascade_failure_rate",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "e2e_latency_ms",
        "weighted_success",
    ]
    summary: dict[str, Any] = {"n": len(rows)}
    for field in numeric_fields:
        values = [row[field] for row in rows if isinstance(row.get(field), int | float) and not math.isnan(row[field])]
        if values:
            summary[f"{field}_mean"] = sum(values) / len(values)

    bool_fields = [
        "task_success",
        "functional_success",
        "behavior_success",
        "trajectory_success",
        "workflow_compliance",
        "constraint_safe_success",
        "strict_success",
        "final_state_match",
        "hallucinated",
        "out_of_scope",
        "loop_stuck",
    ]
    for field in bool_fields:
        values = [row[field] for row in rows if isinstance(row.get(field), bool)]
        if values:
            summary[f"{field}_rate"] = sum(1 for value in values if value) / len(values)

    resource_values = [
        row["resource_query_before_action"]
        for row in rows
        if isinstance(row.get("resource_query_before_action"), bool)
    ]
    if resource_values:
        summary["resource_query_before_action_rate"] = (
            sum(1 for value in resource_values if value) / len(resource_values)
        )

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute deterministic metrics for SCADA Golden Dataset traces")
    parser.add_argument("--dataset", default="eval/golden_dataset.jsonl", help="Golden dataset JSONL")
    parser.add_argument("--traces", required=True, help="Trace JSONL produced by eval.runner")
    parser.add_argument("--output", help="Optional metrics JSONL output path")
    parser.add_argument("--summary-output", help="Optional summary JSON output path")
    parser.add_argument("--skip-missing-golden", action="store_true", help="Skip traces whose golden_id is absent")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        records = load_golden_dataset(args.dataset)
        traces = load_jsonl(args.traces)
        rows = evaluate_traces(traces, records, skip_missing_golden=args.skip_missing_golden)
        if args.output:
            write_jsonl(args.output, rows)
        else:
            for row in rows:
                print(json.dumps(row, ensure_ascii=False, default=str))
        summary = aggregate_summary(rows)
        if args.summary_output:
            Path(args.summary_output).write_text(
                json.dumps(summary, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        else:
            print(json.dumps({"summary": summary}, ensure_ascii=False, default=str), file=sys.stderr)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
