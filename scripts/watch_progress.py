#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path

def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count

def get_completed_count(traces_path: Path) -> int:
    if not traces_path.exists():
        return 0
    completed = 0
    with traces_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                exec_info = row.get("execution", {})
                term = exec_info.get("terminal_state")
                early = exec_info.get("early_terminated")
                if term != "UNKNOWN" and not early:
                    completed += 1
            except Exception:
                pass
    return completed

def draw_progress_bar(completed: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return "[" + "." * width + "] 0%"
    pct = completed / total
    filled = int(round(pct * width))
    bar = "#" * filled + "." * (width - filled)
    return f"[{bar}] {pct:.1%}"

def monitor(results_dir: Path, expected_per_run: int, interval: int):
    try:
        while True:
            # Clear screen
            os.system('cls' if os.name == 'nt' else 'clear')
            
            print("=" * 90)
            print(f" SCADA Agent Experiment Progress Monitor (Active directory: {results_dir})")
            print(f" Refresh interval: {interval}s | Current time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 90)
            
            if not results_dir.exists():
                print(f"Directory not found: {results_dir}")
                time.sleep(interval)
                continue
                
            # Scan for run folders containing traces.jsonl
            run_dirs = []
            for item in sorted(results_dir.iterdir()):
                if item.is_dir() and (item / "traces.jsonl").exists():
                    run_dirs.append(item)
                    
            if not run_dirs:
                print("No run directories containing traces.jsonl found.")
                print("Make sure experiments have started and are writing to this directory.")
            else:
                print(f"{'Run ID / Config':<25} | {'OK Trace':<8} | {'Failed':<8} | {'Progress Bar (Total Expected: ' + str(expected_per_run) + ')':<30}")
                print("-" * 90)
                
                total_ok = 0
                total_fail = 0
                for run_dir in run_dirs:
                    traces_path = run_dir / "traces.jsonl"
                    failures_path = run_dir / "_failures.jsonl"
                    
                    ok_count = get_completed_count(traces_path)
                    fail_count = count_lines(failures_path)
                    
                    total_ok += ok_count
                    total_fail += fail_count
                    
                    bar_str = draw_progress_bar(ok_count, expected_per_run)
                    print(f"{run_dir.name:<25} | {ok_count:<8} | {fail_count:<8} | {bar_str}")
                
                print("-" * 90)
                grand_total = len(run_dirs) * expected_per_run
                total_bar = draw_progress_bar(total_ok, grand_total, width=30)
                print(f"{'GRAND TOTAL':<25} | {total_ok:<8} | {total_fail:<8} | {total_bar}")
                
            print("=" * 90)
            print("Press Ctrl+C to exit this monitor.")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")

def main():
    parser = argparse.ArgumentParser(description="Watch SCADA experiment progress in real time.")
    parser.add_argument(
        "--results-dir",
        type=str,
        default="results/phase4_v1",
        help="Path to results directory containing experiments (default: results/phase4_v1)."
    )
    parser.add_argument(
        "--expected",
        type=int,
        default=500,
        help="Expected successful traces per experiment config (default: 500)."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Refresh interval in seconds (default: 5)."
    )
    args = parser.parse_args()
    
    monitor(Path(args.results_dir), args.expected, args.interval)

if __name__ == "__main__":
    main()
