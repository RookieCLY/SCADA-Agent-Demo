"""Golden Dataset experiment runner.

The runner owns experiment-level concerns from the development plan:

- one immutable-ish result directory per config/model/run_id
- config snapshot and metadata capture
- deterministic dataset selection and repetitions
- breakpoint resume
- technical-failure reruns, recorded separately in `_failures.jsonl`
- offline-judge separation via an empty `judges.jsonl` placeholder

It deliberately does not run the LLM judge. Judge output is produced later and
merged by aggregation scripts.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.config import ExperimentConfig, load_config
from agent.orchestrator import assemble as assemble_current
from eval.schema import GoldenRecord, load_golden_dataset
from world import MockWorld

DEFAULT_DATASET = "eval/golden_dataset.jsonl"
DEFAULT_JUDGE_MODEL = "claude-opus-4-7"


def _resolve_assemble(name: str):
    """Pick the orchestrator under test.

    ``current`` (default) preserves existing behaviour exactly. ``agent_old``
    swaps in the superseded orchestrator via ``eval/_baseline_adapter.py`` so a
    run can measure the *old loop* against today's dataset, tool library, model,
    and metrics — the only clean code-vs-code baseline available, since the
    pre-``perf/Kate`` tree has no eval harness at all.
    """
    if name == "agent_old":
        from eval._baseline_adapter import assemble_baseline

        return assemble_baseline
    return assemble_current


class RateLimiter:
    """Simple sliding-window rate limiter (RPM-based).

    Each call to ``acquire()`` blocks until a slot is available within the
    rolling minute window defined by ``max_per_minute``.
    """

    def __init__(self, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._lock = threading.Lock()
        self._timestamps: list[float] = []

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            cutoff = now - 60.0
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            while len(self._timestamps) >= self._max:
                sleep_s = self._timestamps[0] - cutoff + 0.1
                if sleep_s > 0:
                    time.sleep(sleep_s)
                now = time.monotonic()
                cutoff = now - 60.0
                self._timestamps = [t for t in self._timestamps if t > cutoff]
            self._timestamps.append(now)


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _short_run_id(*parts: str) -> str:
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:8]


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def _git_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    return bool(result.stdout.strip())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _select_records(
    records: list[GoldenRecord],
    *,
    golden_ids: list[str],
    run_all: bool,
    dataset_sample: int | None,
    sample_seed: int,
    shuffle_sample: bool,
    config: ExperimentConfig,
) -> list[GoldenRecord]:
    selected = records

    if golden_ids:
        by_id = {record.id: record for record in records}
        missing = [golden_id for golden_id in golden_ids if golden_id not in by_id]
        if missing:
            raise ValueError(f"Golden IDs not found: {', '.join(missing)}")
        selected = [by_id[golden_id] for golden_id in golden_ids]
    elif run_all:
        selected = records
    else:
        sample_size = dataset_sample if dataset_sample is not None else config.dataset.sample_size
        if sample_size is None:
            raise ValueError("Specify --golden-ids, --dataset-sample, or --all")
        if sample_size <= 0:
            raise ValueError("--dataset-sample must be positive")
        selected = list(records)
        if shuffle_sample:
            rng = random.Random(sample_seed)
            rng.shuffle(selected)
        selected = selected[:sample_size]

    if dataset_sample is not None and (golden_ids or run_all):
        if dataset_sample <= 0:
            raise ValueError("--dataset-sample must be positive")
        selected = list(selected)
        if shuffle_sample:
            rng = random.Random(sample_seed)
            rng.shuffle(selected)
        selected = selected[:dataset_sample]

    return selected


def _completed_pairs(traces_path: Path) -> set[tuple[str, int]]:
    pairs: set[tuple[str, int]] = set()
    for row in _read_jsonl(traces_path):
        query = row.get("query", {})
        experiment = row.get("experiment", {})
        execution = row.get("execution", {})
        golden_id = query.get("golden_id")
        rep_index = experiment.get("rep_index")
        terminal_state = execution.get("terminal_state")
        if not isinstance(golden_id, str) or not isinstance(rep_index, int):
            continue
        if terminal_state == "UNKNOWN" or execution.get("early_terminated"):
            continue
        pairs.add((golden_id, rep_index))
    return pairs


def _world_from_record(record: GoldenRecord) -> MockWorld:
    return MockWorld.model_validate(record.initial_world or {})


def _technical_success(result: dict[str, Any]) -> bool:
    execution = result.get("execution", {})
    if execution.get("terminal_state") == "UNKNOWN":
        return False
    return not execution.get("early_terminated")


def _freeze_files(run_dir: Path) -> None:
    for path in run_dir.rglob("*"):
        if path.is_file():
            path.chmod(path.stat().st_mode & ~stat.S_IWRITE)


def _build_metadata(
    *,
    config_path: Path,
    config_hash: str,
    config: ExperimentConfig,
    provider: str,
    model: str,
    run_id: str,
    run_dir: Path,
    dataset_path: Path,
    dataset_version: str,
    dataset_split: str,
    selected_records: list[GoldenRecord],
    reps: int,
    seed_base: int,
    judge_model: str,
    started_at: str,
    status: str,
    failed_traces: int = 0,
    retried_traces: int = 0,
    skipped_traces: int = 0,
    completed_traces: int = 0,
    completed_at: str | None = None,
    freeze_mode: str = "metadata",
) -> dict[str, Any]:
    return {
        "config_name": config.name,
        "config_path": str(config_path),
        "config_hash": config_hash,
        "code_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "provider": provider,
        "model": model,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "dataset_path": str(dataset_path),
        "dataset_version": dataset_version,
        "dataset_split": dataset_split,
        "n_queries": len(selected_records),
        "n_reps": reps,
        "n_expected_traces": len(selected_records) * reps,
        "seed_base": seed_base,
        "selected_golden_ids": [record.id for record in selected_records],
        "failed_traces": failed_traces,
        "retried_traces": retried_traces,
        "skipped_traces": skipped_traces,
        "completed_traces": completed_traces,
        "judge_model": judge_model,
        "judge_status": "not_run",
        "freeze_mode": freeze_mode,
    }


def _run_one_pair(
    *,
    record: GoldenRecord,
    rep_index: int,
    seed: int,
    max_reruns: int,
    config_path: Path,
    args: argparse.Namespace,
    run_id: str,
    config_hash: str,
    code_commit: str,
    write_lock: threading.Lock,
    rate_limiter: RateLimiter | None,
) -> dict[str, Any]:
    """Execute one (golden_id, rep) pair — called from a worker thread.

    Each worker creates its own Agent (because LLM providers hold per-instance
    mutable message state). A shared ``write_lock`` serialises writes to the
    shared traces.jsonl / _failures.jsonl so files don't get corrupted.
    """
    if rate_limiter is not None:
        rate_limiter.acquire()
    agent = _resolve_assemble(getattr(args, "orchestrator", "current"))(
        config_path,
        model_override=args.model,
        provider_override=args.provider,
        results_root=args.results_root,
        run_id=run_id,
        dataset_version=args.dataset_version,
        code_commit=code_commit,
        config_hash_override=config_hash,
        write_lock=write_lock,
    )

    max_attempts = max_reruns + 1
    for attempt_index in range(max_attempts):
        try:
            world = _world_from_record(record)
            result = agent.run(
                record.query,
                golden_id=record.id,
                initial_world=world,
                rep_index=rep_index,
                seed=seed,
                complexity=record.complexity,
                domain=record.domain,
            )
            if _technical_success(result):
                return {
                    "status": "completed",
                    "record_id": record.id,
                    "rep_index": rep_index,
                    "terminal": result["execution"]["terminal_state"],
                    "turns": result["execution"]["total_turns"],
                }
            raise RuntimeError(
                f"technical failure terminal={result['execution']['terminal_state']} "
                f"reason={result['execution'].get('termination_reason')}"
            )
        except Exception as exc:
            if attempt_index < max_reruns:
                continue
            # All attempts exhausted — record failure
            failure = {
                "record_id": record.id,
                "query": record.query,
                "rep_index": rep_index,
                "seed": seed,
                "attempt_index": attempt_index,
                "max_reruns": max_reruns,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "record": record.model_dump(mode="json"),
                "created_at": _utc_iso(),
            }
            failures_path = agent.tracer.run_dir / "_failures.jsonl"
            with write_lock:
                _append_jsonl(failures_path, failure)
            return {
                "status": "failed",
                "record_id": record.id,
                "rep_index": rep_index,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }


def run_experiment(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = load_config(config_path)
    dataset_path = Path(args.dataset or config.dataset.path or DEFAULT_DATASET)
    records = load_golden_dataset(dataset_path)

    if args.model:
        config.model.name = args.model
    if args.provider:
        config.model.provider = args.provider

    reps = args.reps if args.reps is not None else config.repetitions
    if reps <= 0:
        raise ValueError("--reps must be positive")

    workers = args.workers
    if workers == 0:
        workers = os.cpu_count() or 4

    seed_base = args.seed_base if args.seed_base is not None else config.seed_base
    selected_records = _select_records(
        records,
        golden_ids=_parse_csv(args.golden_ids),
        run_all=args.all,
        dataset_sample=args.dataset_sample,
        sample_seed=args.sample_seed,
        shuffle_sample=args.shuffle_sample,
        config=config,
    )

    config_hash = _sha256_file(config_path)
    code_commit = _git_commit()
    run_id = args.run_id or _short_run_id(
        config_hash,
        config.model.provider,
        config.model.name,
        str(time.time_ns()),
    )

    # Prime the run directory with one "bootstrap" agent
    agent = _resolve_assemble(args.orchestrator)(
        config_path,
        model_override=args.model,
        provider_override=args.provider,
        results_root=args.results_root,
        run_id=run_id,
        dataset_version=args.dataset_version,
        code_commit=code_commit,
        config_hash_override=config_hash,
    )

    run_dir = agent.tracer.run_dir
    traces_path = agent.tracer.traces_path
    failures_path = run_dir / "_failures.jsonl"
    judges_path = run_dir / "judges.jsonl"
    config_snapshot_path = run_dir / "_config.yaml"
    meta_path = run_dir / "_meta.json"

    if traces_path.exists() and traces_path.stat().st_size > 0 and not args.resume:
        raise FileExistsError(f"Run directory already contains traces. Use --resume or a new --run-id: {run_dir}")

    if not config_snapshot_path.exists():
        shutil.copy2(config_path, config_snapshot_path)
    judges_path.touch(exist_ok=True)
    failures_path.touch(exist_ok=True)

    started_at = _utc_iso()
    write_lock = threading.Lock()
    counters_lock = threading.Lock()
    counters = {"completed": 0, "failed": 0, "retried": 0, "skipped": 0}
    completed_pairs = _completed_pairs(traces_path) if args.resume else set()

    freeze_mode = "filesystem" if args.freeze else "metadata"
    metadata = _build_metadata(
        config_path=config_path,
        config_hash=config_hash,
        config=agent.config,
        provider=agent.config.model.provider,
        model=agent.config.model.name,
        run_id=run_id,
        run_dir=run_dir,
        dataset_path=dataset_path,
        dataset_version=args.dataset_version,
        dataset_split=args.dataset_split,
        selected_records=selected_records,
        reps=reps,
        seed_base=seed_base,
        judge_model=args.judge_model,
        started_at=started_at,
        status="running",
        freeze_mode=freeze_mode,
    )
    _write_json(meta_path, metadata)

    # Build the work list
    work_items: list[dict[str, Any]] = []
    for record in selected_records:
        for rep_index in range(reps):
            pair = (record.id, rep_index)
            if pair in completed_pairs:
                with counters_lock:
                    counters["skipped"] += 1
                continue
            work_items.append({
                "record": record,
                "rep_index": rep_index,
                "seed": seed_base + rep_index,
            })

    total = len(selected_records) * reps
    rate_limiter = RateLimiter(args.rate_limit) if args.rate_limit > 0 else None
    print(f"Run directory: {run_dir}")
    print(f"Loaded {len(records)} records; selected {len(selected_records)}; reps={reps}; expected traces={total}")
    if workers > 1:
        print(f"Concurrency: {workers} workers over {len(work_items)} work items")
    if rate_limiter is not None:
        print(f"Rate limit: {args.rate_limit} RPM")

    if not work_items:
        print("All pairs already completed — nothing to do.")
        metadata["status"] = "completed"
        metadata["completed_at"] = _utc_iso()
        _write_json(meta_path, metadata)
        print(f"completed={counters['completed']} skipped={counters['skipped']} failed={counters['failed']}")
        return 0

    print_idx = [0]  # mutable counter for print-friendly ordering under lock

    def _print_status(result: dict[str, Any]) -> None:
        with counters_lock:
            print_idx[0] += 1
            idx = print_idx[0]
            if result["status"] == "completed":
                counters["completed"] += 1
                tag = "ok"
                detail = f"terminal={result['terminal']} turns={result['turns']}"
            elif result["status"] == "skipped":
                counters["skipped"] += 1
                tag = "skipped"
                detail = "(already completed)"
            else:
                counters["failed"] += 1
                tag = "FAIL"
                detail = f"{result['error_type']}: {result['error']}"
            print(f"  [{idx}/{len(work_items)}] {result['record_id']} rep={result['rep_index']} {tag} {detail}")

    try:
        if workers <= 1:
            # Serial path — simpler output, reuses single agent
            for item in work_items:
                record = item["record"]
                rep_index = item["rep_index"]
                result = _run_one_pair(
                    record=record,
                    rep_index=rep_index,
                    seed=item["seed"],
                    max_reruns=args.max_reruns,
                    config_path=config_path,
                    args=args,
                    run_id=run_id,
                    config_hash=config_hash,
                    code_commit=code_commit,
                    write_lock=write_lock,
                    rate_limiter=rate_limiter,
                )
                _print_status(result)
        else:
            # Concurrent path
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_item: dict[concurrent.futures.Future, dict[str, Any]] = {}
                for item in work_items:
                    future = executor.submit(
                        _run_one_pair,
                        record=item["record"],
                        rep_index=item["rep_index"],
                        seed=item["seed"],
                        max_reruns=args.max_reruns,
                        config_path=config_path,
                        args=args,
                        run_id=run_id,
                        config_hash=config_hash,
                        code_commit=code_commit,
                        write_lock=write_lock,
                        rate_limiter=rate_limiter,
                    )
                    future_to_item[future] = item

                for future in concurrent.futures.as_completed(future_to_item):
                    try:
                        result = future.result()
                    except Exception as exc:
                        item = future_to_item[future]
                        result = {
                            "status": "failed",
                            "record_id": item["record"].id,
                            "rep_index": item["rep_index"],
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    _print_status(result)
    except KeyboardInterrupt:
        print("\nInterrupted — waiting for in-flight workers...")
        raise
    finally:
        with counters_lock:
            c = dict(counters)
        status = "completed" if c["failed"] == 0 else "completed_with_failures"
        metadata = _build_metadata(
            config_path=config_path,
            config_hash=config_hash,
            config=agent.config,
            provider=agent.config.model.provider,
            model=agent.config.model.name,
            run_id=run_id,
            run_dir=run_dir,
            dataset_path=dataset_path,
            dataset_version=args.dataset_version,
            dataset_split=args.dataset_split,
            selected_records=selected_records,
            reps=reps,
            seed_base=seed_base,
            judge_model=args.judge_model,
            started_at=started_at,
            completed_at=_utc_iso(),
            status=status,
            failed_traces=c["failed"],
            retried_traces=c["retried"],
            skipped_traces=c["skipped"],
            completed_traces=c["completed"],
            freeze_mode=freeze_mode,
        )
        _write_json(meta_path, metadata)
        if args.freeze:
            _freeze_files(run_dir)

    print("\n=== Runner Complete ===")
    print(f"completed={counters['completed']} skipped={counters['skipped']} retried={counters['retried']} failed={counters['failed']}")
    print(f"traces={traces_path}")
    print(f"failures={failures_path}")
    print(f"metadata={meta_path}")
    return 0 if counters["failed"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Golden Dataset experiments through the SCADA agent")
    parser.add_argument("--config", required=True, help="Path to experiment YAML config")
    parser.add_argument("--dataset", default=None, help=f"Path to golden dataset JSONL (default: {DEFAULT_DATASET})")
    parser.add_argument("--golden-ids", help="Comma-separated golden IDs to run")
    parser.add_argument("--dataset-sample", type=int, help="Run the first N selected records")
    parser.add_argument("--shuffle-sample", action="store_true", help="Shuffle before applying --dataset-sample")
    parser.add_argument("--sample-seed", type=int, default=42, help="Seed for --shuffle-sample")
    parser.add_argument("--all", action="store_true", help="Run all records in the dataset")
    parser.add_argument("--reps", type=int, default=None, help="Repetitions per record (defaults to config.repetitions)")
    parser.add_argument("--seed-base", type=int, default=None, help="Base seed (defaults to config.seed_base)")
    parser.add_argument("--model", help="Override the config model name")
    parser.add_argument("--provider", help="Override the config model provider")
    parser.add_argument("--results-root", default="results", help="Root output directory")
    parser.add_argument("--run-id", help="Explicit run ID. Use with --resume to continue an existing run")
    parser.add_argument("--resume", action="store_true", help="Skip already completed golden_id/rep pairs")
    parser.add_argument("--max-reruns", type=int, default=3, help="Retries for technical failures")
    parser.add_argument("--dataset-version", default="dev", help="Dataset version to record in traces and metadata")
    parser.add_argument("--dataset-split", default="dev", help="Dataset split label for metadata")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL, help="Judge model recorded for offline scoring")
    parser.add_argument(
        "--freeze",
        action="store_true",
        help="Mark result files read-only after completion. Metadata is always marked frozen logically.",
    )
    parser.add_argument(
        "--orchestrator",
        choices=["current", "agent_old"],
        default="current",
        help=(
            "Which orchestrator to measure. 'agent_old' runs the superseded loop "
            "through today's harness as a before/after baseline (see "
            "eval/_baseline_adapter.py)."
        ),
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=1,
        help="Number of concurrent workers (default: 1, serial). Set to 0 for CPU-count auto-detect.",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=0,
        help="Max RPM (requests per minute) for LLM calls. 0 = no explicit limit (default: 0).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_experiment(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
