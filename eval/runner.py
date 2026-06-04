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
import hashlib
import json
import random
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.config import ExperimentConfig, load_config
from agent.orchestrator import assemble
from eval.schema import GoldenRecord, load_golden_dataset
from world import MockWorld

DEFAULT_DATASET = "eval/golden_dataset.jsonl"
DEFAULT_JUDGE_MODEL = "claude-opus-4-7"


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

    agent = assemble(
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
    completed = 0
    failed = 0
    retried = 0
    skipped = 0
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

    total = len(selected_records) * reps
    print(f"Run directory: {run_dir}")
    print(f"Loaded {len(records)} records; selected {len(selected_records)}; reps={reps}; expected traces={total}")

    try:
        for record in selected_records:
            print(f"\n[{record.id}] {record.query}")
            for rep_index in range(reps):
                pair = (record.id, rep_index)
                if pair in completed_pairs:
                    skipped += 1
                    print(f"  - rep {rep_index}: skipped (already completed)")
                    continue

                seed = seed_base + rep_index
                max_attempts = args.max_reruns + 1
                for attempt_index in range(max_attempts):
                    prefix = f"  - rep {rep_index}, attempt {attempt_index + 1}/{max_attempts}"
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
                        terminal = result["execution"]["terminal_state"]
                        turns = result["execution"]["total_turns"]
                        if _technical_success(result):
                            completed += 1
                            print(f"{prefix}: ok terminal={terminal} turns={turns}")
                            break

                        raise RuntimeError(
                            f"technical failure terminal={terminal} reason={result['execution'].get('termination_reason')}"
                        )
                    except Exception as exc:
                        if attempt_index < args.max_reruns:
                            retried += 1
                            print(f"{prefix}: retrying after {type(exc).__name__}: {exc}")
                            continue

                        failed += 1
                        failure = {
                            "record_id": record.id,
                            "query": record.query,
                            "rep_index": rep_index,
                            "seed": seed,
                            "attempt_index": attempt_index,
                            "max_reruns": args.max_reruns,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                            "record": record.model_dump(mode="json"),
                            "created_at": _utc_iso(),
                        }
                        _append_jsonl(failures_path, failure)
                        print(f"{prefix}: failed after retries: {type(exc).__name__}: {exc}")
    finally:
        status = "completed" if failed == 0 else "completed_with_failures"
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
            failed_traces=failed,
            retried_traces=retried,
            skipped_traces=skipped,
            completed_traces=completed,
            freeze_mode=freeze_mode,
        )
        _write_json(meta_path, metadata)
        if args.freeze:
            _freeze_files(run_dir)

    print("\n=== Runner Complete ===")
    print(f"completed={completed} skipped={skipped} retried={retried} failed={failed}")
    print(f"traces={traces_path}")
    print(f"failures={failures_path}")
    print(f"metadata={meta_path}")
    return 0 if failed == 0 else 1


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
