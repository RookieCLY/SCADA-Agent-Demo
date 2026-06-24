#!/usr/bin/env python3
"""Run offline LLM-as-Judge for experiment 1/2/3 result traces.

The script scans experiment run directories, appends judge rows to each
``judges.jsonl``, and supports resume by skipping trace_ids already present in
that output file.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.judges import (  # noqa: E402
    DEFAULT_MODEL as HEURISTIC_MODEL,
    DEFAULT_RUBRIC,
    OpenAICompatibleJudgeClient,
    _load_dotenv_into_environ,
    judge_trace,
    load_rubric,
)
from eval.metrics import evaluate_trace  # noqa: E402
from eval.schema import GoldenRecord, load_golden_dataset  # noqa: E402


DEFAULT_EXPERIMENT_ROOTS = {
    "1": Path("results/phase4_v1"),
    "2": Path("results/exp2_v1"),
    "3": Path("results/exp3_v1"),
}
DEFAULT_MIMO_MODEL = "mimo-v2.5-pro"


@dataclass
class RunSummary:
    experiment: str
    run_dir: str
    traces: int
    existing: int
    judged: int
    skipped: int
    failed: int
    output: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        f.flush()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def update_meta(run_dir: Path, **updates: Any) -> None:
    meta_path = run_dir / "_meta.json"
    if not meta_path.exists():
        return
    meta = read_json(meta_path)
    meta.update(updates)
    write_json(meta_path, meta)


def existing_trace_ids(path: Path) -> set[str]:
    seen: set[str] = set()
    for row in load_jsonl(path):
        trace_id = row.get("trace_id")
        if isinstance(trace_id, str) and trace_id:
            seen.add(trace_id)
    return seen


def trace_id(trace: dict[str, Any]) -> str | None:
    value = trace.get("trace_id")
    return value if isinstance(value, str) and value else None


def golden_id(trace: dict[str, Any]) -> str | None:
    query = trace.get("query") or {}
    value = query.get("golden_id") or trace.get("golden_id")
    return value if isinstance(value, str) and value else None


def discover_run_dirs(roots: dict[str, Path], selected: set[str]) -> list[tuple[str, Path]]:
    run_dirs: list[tuple[str, Path]] = []
    for experiment, root in roots.items():
        if experiment not in selected:
            continue
        if not root.exists():
            print(f"[warn] experiment {experiment} root not found: {root}")
            continue
        for traces_path in sorted(root.rglob("traces.jsonl")):
            run_dirs.append((experiment, traces_path.parent))
    return run_dirs


def resolve_model(provider: str, model: str | None) -> str | None:
    if model:
        return model
    if provider == "xiaomi-mimo":
        return DEFAULT_MIMO_MODEL
    if provider == "heuristic":
        return HEURISTIC_MODEL
    return None


def provider_for_judge(provider: str) -> str:
    return "openai-compatible" if provider == "xiaomi-mimo" else provider


def build_client(provider: str, model: str | None) -> OpenAICompatibleJudgeClient | None:
    if provider == "heuristic":
        return None
    return OpenAICompatibleJudgeClient(model=model)


def should_skip(trace: dict[str, Any], done: set[str], *, resume: bool) -> bool:
    tid = trace_id(trace)
    return bool(resume and tid and tid in done)


def run_one_dir(
    *,
    experiment: str,
    run_dir: Path,
    golden_by_id: dict[str, GoldenRecord],
    rubric: str,
    provider: str,
    model: str | None,
    client: OpenAICompatibleJudgeClient | None,
    output_name: str,
    resume: bool,
    limit_per_run: int | None,
    limit_state: dict[str, int | None],
    skip_missing_golden: bool,
    dry_run: bool,
    min_interval_s: float,
) -> RunSummary:
    traces_path = run_dir / "traces.jsonl"
    output_path = run_dir / output_name
    failures_path = run_dir / "_judge_failures.jsonl"
    traces = load_jsonl(traces_path)
    done = existing_trace_ids(output_path) if resume else set()
    if not resume and output_path.exists() and not dry_run:
        backup_path = output_path.with_suffix(output_path.suffix + ".bak")
        if not backup_path.exists():
            output_path.replace(backup_path)
        else:
            output_path.unlink()

    pending = [trace for trace in traces if not should_skip(trace, done, resume=resume)]
    if limit_per_run is not None:
        pending = pending[:limit_per_run]
    if limit_state["remaining"] is not None:
        pending = pending[: int(limit_state["remaining"])]

    print(
        f"\n[{experiment}] {run_dir} traces={len(traces)} existing={len(done)} "
        f"pending={len(pending)} output={output_path.name}"
    )

    if dry_run:
        return RunSummary(
            experiment=experiment,
            run_dir=str(run_dir),
            traces=len(traces),
            existing=len(done),
            judged=0,
            skipped=len(traces) - len(pending),
            failed=0,
            output=str(output_path),
        )

    update_meta(
        run_dir,
        judge_status="running",
        judge_model=model or provider,
        judge_started_at=utc_now(),
        judge_output=output_name,
    )

    judged = 0
    failed = 0
    last_call_at = 0.0
    for idx, trace in enumerate(pending, 1):
        gid = golden_id(trace)
        tid = trace_id(trace)
        if gid not in golden_by_id:
            if skip_missing_golden:
                failed += 1
                append_jsonl(
                    failures_path,
                    {
                        "trace_id": tid,
                        "golden_id": gid,
                        "error_type": "MissingGolden",
                        "error": f"golden_id not found: {gid!r}",
                        "created_at": utc_now(),
                    },
                )
                continue
            raise KeyError(f"{run_dir}: trace {tid} references unknown golden_id={gid!r}")

        now = time.monotonic()
        sleep_s = min_interval_s - (now - last_call_at)
        if sleep_s > 0:
            time.sleep(sleep_s)

        golden = golden_by_id[gid]
        try:
            metrics = evaluate_trace(trace, golden)
            result = judge_trace(
                golden,
                trace,
                metrics=metrics,
                rubric=rubric,
                provider=provider_for_judge(provider),
                model_name=model,
                client=client,
            )
            append_jsonl(output_path, result.to_json_row())
            judged += 1
            last_call_at = time.monotonic()
            print(
                f"  [{idx}/{len(pending)}] {gid} trace={tid or '-'} "
                f"overall={result.overall:.2f} passed={result.passed}",
                flush=True,
            )
        except Exception as exc:
            failed += 1
            append_jsonl(
                failures_path,
                {
                    "trace_id": tid,
                    "golden_id": gid,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "created_at": utc_now(),
                },
            )
            print(f"  [FAIL] {gid} trace={tid or '-'} {type(exc).__name__}: {exc}", flush=True)

        if limit_state["remaining"] is not None:
            limit_state["remaining"] = int(limit_state["remaining"]) - 1
            if int(limit_state["remaining"]) <= 0:
                break

    final_count = len(existing_trace_ids(output_path))
    complete = final_count >= len(traces) and failed == 0
    update_meta(
        run_dir,
        judge_status="completed" if complete else "partial",
        judge_model=model or provider,
        judge_completed_at=utc_now(),
        judge_output=output_name,
        judge_completed_traces=final_count,
        judge_failed_traces=failed,
    )

    return RunSummary(
        experiment=experiment,
        run_dir=str(run_dir),
        traces=len(traces),
        existing=len(done),
        judged=judged,
        skipped=len(traces) - len(pending),
        failed=failed,
        output=str(output_path),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run LLM-as-Judge for experiment 1/2/3 traces with resume support.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--experiments", default="1,2,3", help="Comma-separated experiment ids: 1,2,3")
    parser.add_argument("--exp1-root", default=str(DEFAULT_EXPERIMENT_ROOTS["1"]), help="Experiment 1 results root")
    parser.add_argument("--exp2-root", default=str(DEFAULT_EXPERIMENT_ROOTS["2"]), help="Experiment 2 results root")
    parser.add_argument("--exp3-root", default=str(DEFAULT_EXPERIMENT_ROOTS["3"]), help="Experiment 3 results root")
    parser.add_argument("--dataset", default="eval/golden_dataset.jsonl", help="Golden dataset JSONL")
    parser.add_argument("--rubric", default=str(DEFAULT_RUBRIC), help="Judge rubric markdown")
    parser.add_argument(
        "--provider",
        choices=["xiaomi-mimo", "openai-compatible", "heuristic"],
        default="xiaomi-mimo",
        help="Judge backend. xiaomi-mimo and openai-compatible use OpenAI-compatible chat completions.",
    )
    parser.add_argument("--model", default=None, help="Judge model name. Default for xiaomi-mimo is mimo-v2.5-pro.")
    parser.add_argument("--output-name", default="judges.jsonl", help="Per-run judge output file name")
    parser.add_argument("--summary", default="results/judge_batch_summary.json", help="Batch summary JSON")
    parser.add_argument("--no-resume", action="store_true", help="Do not skip existing judge rows; backup/replace output files")
    parser.add_argument("--limit-per-run", type=int, default=None, help="Judge at most N pending traces per run")
    parser.add_argument("--limit-total", type=int, default=None, help="Judge at most N pending traces across all runs")
    parser.add_argument("--rpm", type=int, default=0, help="Optional requests-per-minute throttle; 0 disables")
    parser.add_argument("--skip-missing-golden", action="store_true", help="Record and skip traces whose golden_id is absent")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without calling the judge or writing outputs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _load_dotenv_into_environ()

    selected = {item.strip() for item in args.experiments.split(",") if item.strip()}
    roots = {
        "1": Path(args.exp1_root),
        "2": Path(args.exp2_root),
        "3": Path(args.exp3_root),
    }
    run_dirs = discover_run_dirs(roots, selected)
    if not run_dirs:
        print("No run directories found.", file=sys.stderr)
        return 2

    model = resolve_model(args.provider, args.model)
    print(f"Judge provider={args.provider} model={model or '(env/default)'} resume={not args.no_resume}")
    print(f"Runs discovered: {len(run_dirs)}")

    if args.dry_run:
        client = None
    else:
        client = build_client(args.provider, model)
    rubric = load_rubric(args.rubric)
    golden_records = load_golden_dataset(args.dataset)
    golden_by_id = {record.id: record for record in golden_records}
    min_interval_s = 60.0 / args.rpm if args.rpm and args.rpm > 0 else 0.0
    limit_state: dict[str, int | None] = {"remaining": args.limit_total}

    summaries: list[RunSummary] = []
    for experiment, run_dir in run_dirs:
        if limit_state["remaining"] is not None and int(limit_state["remaining"]) <= 0:
            break
        summary = run_one_dir(
            experiment=experiment,
            run_dir=run_dir,
            golden_by_id=golden_by_id,
            rubric=rubric,
            provider=args.provider,
            model=model,
            client=client,
            output_name=args.output_name,
            resume=not args.no_resume,
            limit_per_run=args.limit_per_run,
            limit_state=limit_state,
            skip_missing_golden=args.skip_missing_golden,
            dry_run=args.dry_run,
            min_interval_s=min_interval_s,
        )
        summaries.append(summary)

    payload = {
        "created_at": utc_now(),
        "provider": args.provider,
        "model": model,
        "resume": not args.no_resume,
        "dry_run": args.dry_run,
        "summaries": [asdict(item) for item in summaries],
        "totals": {
            "runs": len(summaries),
            "traces": sum(item.traces for item in summaries),
            "existing": sum(item.existing for item in summaries),
            "judged": sum(item.judged for item in summaries),
            "skipped": sum(item.skipped for item in summaries),
            "failed": sum(item.failed for item in summaries),
        },
    }
    write_json(Path(args.summary), payload)
    print(f"\nSummary saved to {args.summary}")
    print(json.dumps(payload["totals"], ensure_ascii=False, indent=2))
    return 1 if payload["totals"]["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
