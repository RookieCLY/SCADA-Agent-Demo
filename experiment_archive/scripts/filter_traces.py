#!/usr/bin/env python3
"""Clean duplicated or empty traces from experiment trace JSONL files.

For each (golden_id, rep_index) group, keep one best trace:
1. Prefer traces that reached a non-UNKNOWN terminal state without early termination.
2. Prefer traces with actual LLM/tool activity.
3. Prefer more completed turns.
4. Prefer the latest record as a deterministic tie-breaker.
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import OrderedDict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class FileSummary:
    path: str
    original_records: int
    filtered_records: int
    removed_records: int
    duplicate_groups: int
    parse_errors: int
    skipped_records: int
    backup_path: str | None
    changed: bool


def load_jsonl(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    records: list[tuple[int, dict[str, Any]]] = []
    parse_errors = 0
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue
            if isinstance(obj, dict):
                records.append((line_number, obj))
            else:
                parse_errors += 1
    return records, parse_errors


def trace_key(record: dict[str, Any]) -> tuple[str, int] | None:
    query = record.get("query") or {}
    experiment = record.get("experiment") or {}
    golden_id = query.get("golden_id") or record.get("golden_id")
    rep_index = experiment.get("rep_index")
    if rep_index is None:
        rep_index = query.get("rep") or record.get("rep")
    if golden_id is None or rep_index is None:
        return None
    try:
        return str(golden_id), int(rep_index)
    except (TypeError, ValueError):
        return None


def activity_count(record: dict[str, Any]) -> int:
    return (
        len(record.get("llm_calls") or [])
        + len(record.get("tool_calls") or [])
        + len(record.get("resource_reads") or [])
        + len(record.get("states") or [])
    )


def record_score(line_number: int, record: dict[str, Any]) -> tuple[int, int, int, int, int]:
    execution = record.get("execution") or {}
    terminal_state = execution.get("terminal_state")
    early_terminated = bool(execution.get("early_terminated"))
    total_turns = execution.get("total_turns") or 0
    try:
        total_turns_int = int(total_turns)
    except (TypeError, ValueError):
        total_turns_int = 0

    completed = int(terminal_state not in (None, "UNKNOWN") and not early_terminated)
    has_activity = int(activity_count(record) > 1 or len(record.get("llm_calls") or []) > 0 or len(record.get("tool_calls") or []) > 0)
    not_empty_unknown = int(not (terminal_state == "UNKNOWN" and total_turns_int == 0 and activity_count(record) <= 1))
    return completed, has_activity, not_empty_unknown, total_turns_int, line_number


def filter_traces_file(file_path: Path, backup: bool = True, dry_run: bool = False) -> FileSummary:
    records, parse_errors = load_jsonl(file_path)
    groups: OrderedDict[tuple[str, int], list[tuple[int, dict[str, Any]]]] = OrderedDict()
    skipped_records = 0

    for line_number, record in records:
        key = trace_key(record)
        if key is None:
            skipped_records += 1
            continue
        groups.setdefault(key, []).append((line_number, record))

    filtered: list[dict[str, Any]] = []
    duplicate_groups = 0
    for group in groups.values():
        if len(group) > 1:
            duplicate_groups += 1
        best_line, best_record = max(group, key=lambda item: record_score(item[0], item[1]))
        filtered.append(best_record)

    removed_records = len(records) - len(filtered)
    changed = removed_records > 0 or parse_errors > 0 or skipped_records > 0
    backup_path: Path | None = None

    if changed and not dry_run:
        if backup:
            backup_path = file_path.with_suffix(".jsonl.bak")
            if not backup_path.exists():
                shutil.copy2(file_path, backup_path)
        with file_path.open("w", encoding="utf-8", newline="\n") as f:
            for record in filtered:
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    return FileSummary(
        path=str(file_path),
        original_records=len(records),
        filtered_records=len(filtered),
        removed_records=removed_records,
        duplicate_groups=duplicate_groups,
        parse_errors=parse_errors,
        skipped_records=skipped_records,
        backup_path=str(backup_path) if backup_path else None,
        changed=changed,
    )


def iter_trace_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.rglob("traces.jsonl"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean duplicated/empty traces in traces.jsonl files.")
    parser.add_argument("paths", nargs="+", help="Trace file(s) or directories containing traces.jsonl")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak files before modifying traces.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned changes without modifying files.")
    parser.add_argument("--summary", default=None, help="Optional JSON summary output path.")
    args = parser.parse_args()

    summaries: list[FileSummary] = []
    for raw_path in args.paths:
        target = Path(raw_path)
        if not target.exists():
            print(f"Missing path: {target}")
            continue
        for trace_file in iter_trace_files(target):
            summary = filter_traces_file(trace_file, backup=not args.no_backup, dry_run=args.dry_run)
            summaries.append(summary)
            marker = "DRY" if args.dry_run else "OK"
            print(
                f"[{marker}] {trace_file}: {summary.original_records} -> {summary.filtered_records} "
                f"removed={summary.removed_records} duplicate_groups={summary.duplicate_groups} "
                f"parse_errors={summary.parse_errors} skipped={summary.skipped_records}"
            )

    total_removed = sum(item.removed_records for item in summaries)
    total_changed = sum(1 for item in summaries if item.changed)
    print(f"Summary: files={len(summaries)} changed={total_changed} removed_records={total_removed}")

    if args.summary:
        summary_path = Path(args.summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump([asdict(item) for item in summaries], f, ensure_ascii=False, indent=2)
        print(f"Wrote summary to {summary_path}")


if __name__ == "__main__":
    main()
