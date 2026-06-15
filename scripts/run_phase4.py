#!/usr/bin/env python3
"""Phase 4 batch runner — runs the full ablation matrix in one go.

run-id = ``{prefix}_{config_abbr}``, where ``--prefix`` is user-provided
and the suffix is auto-generated from the config name.

Usage examples::

    # dry-run to preview the plan
    python scripts/run_phase4.py --prefix phase4_v1 --dry-run

    # run all 6 configs with mimo (default)
    python scripts/run_phase4.py --prefix phase4_v1

    # run with mimo + deepseek
    python scripts/run_phase4.py --prefix phase4_v1 --models xiaomi-mimo,deepseek

    # run only A, B, F
    python scripts/run_phase4.py --prefix phase4_v1 --configs A,B,F

    # resume (skip already-completed experiments)
    python scripts/run_phase4.py --prefix phase4_v1 --resume
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ── experiment matrix ──────────────────────────────────────────────────────────
# (yaml_path, abbreviation, description)
MAIN_CONFIGS: list[tuple[str, str, str]] = [
    ("configs/A_flat_baseline.yaml",        "A_flat",   "A: flat baseline"),
    ("configs/B_hierarchical_only.yaml",    "B_hier",   "B: hierarchical tools"),
    ("configs/C_hier_rag.yaml",             "C_rag",    "C: + Tool RAG"),
    ("configs/D_hier_rag_workflow.yaml",    "D_wf",     "D: + Workflow"),
    ("configs/E_with_state_machine.yaml",   "E_sm",     "E: + State Machine"),
    ("configs/F_full_four_in_one.yaml",     "F_full",   "F: + Resources (full)"),
]

# Concurrency presets per provider  (workers, rate_limit, model)
# F_full overrides are applied automatically for mimo.
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "xiaomi-mimo": {"workers": 3, "rate_limit": 0, "model": "mimo-v2.5-pro",
                     "F_full_workers": 3, "F_full_rpm": 0},
    "deepseek":    {"workers": 3, "rate_limit": 0, "model": "deepseek-v4-flash"},
    "openrouter":  {"workers": 3, "rate_limit": 0, "model": "openrouter/owl-alpha"},
}

RESULT_FILE = "_phase4_batch_result.json"


# ── helpers ────────────────────────────────────────────────────────────────────
def _build_run_id(prefix: str, abbr: str) -> str:
    return f"{prefix}_{abbr}"


def _run_experiment(
    run_id: str,
    config_path: str,
    provider: str,
    model: str | None,
    reps: int,
    workers: int,
    rate_limit: int,
    resume: bool,
    dry_run: bool,
    prefix: str,
    reruns: int = 10,
    sample: int = 0,
) -> dict[str, Any]:
    """Invoke ``eval.runner`` as a subprocess and return the exit code + summary."""
    argv = [
        sys.executable, "-m", "eval.runner",
        "--config", config_path,
        "--all" if sample <= 0 else f"--dataset-sample={sample}",
        "--reps", str(reps),
        "--provider", provider,
        "--run-id", run_id,
        "-w", str(workers),
        "--results-root", f"results/{prefix}",
    ]
    if model:
        argv += ["--model", model]
    if rate_limit > 0:
        argv += ["--rate-limit", str(rate_limit)]
    if resume:
        argv += ["--resume"]
    if reruns != 3:
        argv += ["--max-reruns", str(reruns)]

    cmd_str = " ".join(argv)
    if dry_run:
        return {"run_id": run_id, "status": "dry-run", "cmd": cmd_str}

    print(f"\n{'='*60}")
    print(f"[START] run_id={run_id}")
    print(f"  cmd: {cmd_str}")
    print(f"{'='*60}\n")
    t0 = time.time()

    proc = subprocess.run(argv, capture_output=False)
    elapsed = time.time() - t0

    return {
        "run_id": run_id,
        "status": "ok" if proc.returncode == 0 else f"exit-{proc.returncode}",
        "exit_code": proc.returncode,
        "elapsed_s": round(elapsed, 1),
        "cmd": cmd_str,
    }


# ── main ──────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 4 batch runner — execute the full ablation matrix",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--prefix", required=True,
        help="User prefix for all run-ids (e.g. 'phase4_v1'). "
             "Final run-id = {prefix}_{config_abbr}.",
    )
    parser.add_argument(
        "--models", default="xiaomi-mimo",
        help="Comma-separated provider names (default: xiaomi-mimo). "
             "Each model gets its own pass over the config matrix.",
    )
    parser.add_argument(
        "--configs", default="A,B,C,D,E,F",
        help="Comma-separated config letters to run (default: A,B,C,D,E,F).",
    )
    parser.add_argument("--reps", type=int, default=5, help="Repetitions per query (default: 5)")
    parser.add_argument("--max-reruns", type=int, default=10, dest="max_reruns", help="Retries for technical failures (default: 10)")
    parser.add_argument("--sample", type=int, default=0, help="Run only the first N queries (0 = all, default: 0)")
    parser.add_argument("--resume", action="store_true", help="Skip experiments whose trace file already has entries")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without executing")
    args = parser.parse_args(argv)

    providers = [p.strip() for p in args.models.split(",") if p.strip()]
    cfg_letters = {c.strip().upper() for c in args.configs.split(",") if c.strip()}
    configs = [c for c in MAIN_CONFIGS if c[1].split("_")[0] in cfg_letters]

    if not configs:
        print("No configs selected. Check --configs.", file=sys.stderr)
        return 2

    # ── print plan ─────────────────────────────────────────────────────────────
    n_experiments = len(configs) * len(providers)
    n_traces = n_experiments * 100 * args.reps
    print(f"\n{'='*60}")
    print(f"Phase 4 batch plan  —  prefix={args.prefix}")
    print(f"{'='*60}")
    print(f"  Models  : {providers}")
    print(f"  Configs : {[c[1] for c in configs]}")
    print(f"  Reps    : {args.reps}")
    print(f"  Reruns  : {args.max_reruns}")
    print(f"  Resume  : {args.resume}")
    print(f"  Total experiments : {n_experiments}")
    print(f"  Total traces      : ~{n_traces}")
    print()

    for provider in providers:
        pd = PROVIDER_DEFAULTS.get(provider, {"workers": 3, "rate_limit": 0})
        for cfg_path, abbr, desc in configs:
            run_id = _build_run_id(args.prefix, abbr)
            is_full = abbr.startswith("F")
            w = pd.get("F_full_workers" if is_full else "workers", pd.get("workers", 3))
            rpm = pd.get("F_full_rpm" if is_full else "rate_limit", pd.get("rate_limit", 0))
            rpm_str = f"  rpm={rpm}" if rpm > 0 else ""
            print(f"  {run_id:<40s}  w={w}{rpm_str}  ({desc})")
    print()

    if args.dry_run:
        print("[dry-run] No experiments executed.")
        return 0

    # ── execute ────────────────────────────────────────────────────────────────
    results: list[dict[str, Any]] = []
    total_start = time.time()

    for provider in providers:
        pd = PROVIDER_DEFAULTS.get(provider, {"workers": 3, "rate_limit": 0})
        model = pd.get("model")  # optional model override inside the provider
        for cfg_path, abbr, desc in configs:
            run_id = _build_run_id(args.prefix, abbr)
            is_full = abbr.startswith("F")
            w = pd.get("F_full_workers" if is_full else "workers", pd.get("workers", 3))
            rpm = pd.get("F_full_rpm" if is_full else "rate_limit", pd.get("rate_limit", 0))

            result = _run_experiment(
                run_id=run_id,
                config_path=cfg_path,
                provider=provider,
                model=model,
                reps=args.reps,
                workers=w,
                rate_limit=rpm,
                resume=args.resume,
                dry_run=False,
                prefix=args.prefix,
                reruns=args.max_reruns,
                sample=args.sample,
            )
            results.append(result)

            status_icon = "OK" if result["status"] == "ok" else "FAIL"
            elapsed_s = result.get("elapsed_s", 0)
            print(f"\n[{status_icon}] {run_id}  status={result['status']}  elapsed={elapsed_s}s")

    # ── summary ────────────────────────────────────────────────────────────────
    total_elapsed = time.time() - total_start
    ok_count = sum(1 for r in results if r["status"] == "ok")
    fail_count = len(results) - ok_count

    print(f"\n{'='*60}")
    print(f"Phase 4 batch complete")
    print(f"{'='*60}")
    print(f"  Total experiments : {len(results)}")
    print(f"  Passed            : {ok_count}")
    print(f"  Failed            : {fail_count}")
    print(f"  Total elapsed     : {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
    print()

    if fail_count > 0:
        print("  Failed experiments:")
        for r in results:
            if r["status"] != "ok":
                print(f"    [FAIL] {r['run_id']}  status={r['status']}")
        print()

    # Write result summary
    result_path = Path("results") / f"{args.prefix}_{RESULT_FILE}"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {"prefix": args.prefix, "results": results, "elapsed_s": round(total_elapsed, 1)},
            indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )
    print(f"  Result summary saved to: {result_path}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
