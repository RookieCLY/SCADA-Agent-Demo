#!/usr/bin/env python3
"""Filter out duplicate failed traces in traces.jsonl files.

Keeps only the successful (completed without early termination or UNKNOWN state) 
or the latest attempt per (golden_id, rep_index) combination.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from collections import OrderedDict

def filter_traces_file(file_path: Path, backup: bool = True) -> None:
    print(f"Processing: {file_path}")
    
    # Read all lines
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            records = [json.loads(line) for line in f if line.strip()]
        except Exception as e:
            print(f"  Error parsing JSONL: {e}")
            return

    # Group by (golden_id, rep_index) while preserving insertion order of the unique keys
    groups = OrderedDict()
    for record in records:
        golden_id = record.get("query", {}).get("golden_id")
        rep_index = record.get("experiment", {}).get("rep_index")
        if golden_id is None or rep_index is None:
            print(f"  Warning: skipping record without golden_id or rep_index: {record.get('trace_id')}")
            continue
        key = (golden_id, rep_index)
        if key not in groups:
            groups[key] = []
        groups[key].append(record)

    filtered_records = []
    removed_count = 0
    for key, group in groups.items():
        # Find the best record in the group:
        # Success = not early_terminated and terminal_state != "UNKNOWN"
        success_records = []
        for r in group:
            exec_info = r.get("execution", {})
            if exec_info.get("terminal_state") != "UNKNOWN" and not exec_info.get("early_terminated"):
                success_records.append(r)
        
        if success_records:
            # Keep the last successful attempt
            best = success_records[-1]
            removed_count += len(group) - 1
        else:
            # Keep the last failed attempt
            best = group[-1]
            removed_count += len(group) - 1
        
        filtered_records.append(best)

    print(f"  Original records: {len(records)}")
    print(f"  Filtered records: {len(filtered_records)}")
    print(f"  Removed duplicate failed attempts: {removed_count}")

    if removed_count == 0:
        print("  No duplicates to filter.")
        return

    # Backup the original file
    if backup:
        backup_path = file_path.with_suffix(".jsonl.bak")
        if not backup_path.exists():
            shutil.copy2(file_path, backup_path)
            print(f"  Backup saved to {backup_path}")
        else:
            print(f"  Backup already exists at {backup_path}, skipped backup creation")

    # Overwrite original file
    with open(file_path, "w", encoding="utf-8") as f:
        for record in filtered_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"  Successfully overwrote {file_path}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Filter out duplicate failed traces in traces.jsonl files, keeping only the successful or latest attempt per (golden_id, rep_index)")
    parser.add_argument("path", help="Path to a traces.jsonl file or a directory containing them")
    parser.add_argument("--no-backup", action="store_true", help="Do not create backup files (.bak)")
    args = parser.parse_args()

    target_path = Path(args.path)
    if not target_path.exists():
        print(f"Error: Path {target_path} does not exist.")
        return

    if target_path.is_file():
        filter_traces_file(target_path, backup=not args.no_backup)
    else:
        for file_path in target_path.rglob("traces.jsonl"):
            filter_traces_file(file_path, backup=not args.no_backup)

if __name__ == "__main__":
    main()
