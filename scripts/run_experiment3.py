#!/usr/bin/env python3
"""Experiment 3 batch runner — Top-K sweep on config D (hierarchical+RAG+Workflow).

Sweep dimensions:
  top_k   ∈ {5, 10, 15, 20, 30}
  config   = D_hier_rag_workflow (fixed)
  model    × 3
  reps     = 3
  queries  = 50  (--sample 50)

run-id = ``{prefix}_k{K}_D_wf``

Usage examples::

    # dry-run
    python scripts/run_experiment3.py --prefix exp3_v1 --dry-run

    # run with mimo + deepseek (default)
    python scripts/run_experiment3.py --prefix exp3_v1

    # run only K=10 and K=20
    python scripts/run_experiment3.py --prefix exp3_v1 --top-k-values 10,20

    # smoke test (5 queries, 1 rep)
    python scripts/run_experiment3.py --prefix exp3_test --reps 1 --sample 5
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

# ── experiment matrix ──────────────────────────────────────────────────────────
# Single config for Top-K sweep (must have Tool RAG enabled)
SWEEP_CONFIG: tuple[str, str, str] = (
    "configs/D_hier_rag_workflow.yaml", "D_wf", "D: hier+RAG+Workflow",
)

DEFAULT_TOP_K_VALUES = [5, 10, 15, 20, 30]

# Concurrency presets per provider  (workers, rate_limit, model)
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "xiaomi-mimo": {"workers": 3, "rate_limit": 0, "model": "mimo-v2.5-pro"},
    "deepseek":    {"workers": 3, "rate_limit": 0, "model": "deepseek-chat"},
}

RESULT_FILE = "_exp3_topk_sweep.json"


# ── helpers ────────────────────────────────────────────────────────────────────
def _build_run_id(prefix: str, top_k: int, abbr: str) -> str:
    return f"{prefix}_k{top_k}_{abbr}"


def _generate_topk_config(base_config_path: str, top_k: int) -> Path:
    """Create a temp YAML config with ``architecture.tool_rag.top_k`` overridden."""
    with open(base_config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Ensure the nested structure exists
    config.setdefault("architecture", {}).setdefault("tool_rag", {})["top_k"] = top_k

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="exp3_sweep_", delete=False, encoding="utf-8",
    )
    yaml.dump(config, tmp, allow_unicode=True, default_flow_style=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    return tmp_path


def _run_experiment(
    run_id: str,
    config_path: str | Path,
    provider: str,
    model: str | None,
    reps: int,
    workers: int,
    rate_limit: int,
    reruns: int,
    resume: bool,
    dry_run: bool,
    prefix: str,
    sample: int = 0,
) -> dict[str, Any]:
    """Invoke ``eval.runner`` as a subprocess and return the exit code + summary."""
    argv = [
        sys.executable, "-m", "eval.runner",
        "--config", str(config_path),
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
    if reruns != 3:
        argv += ["--max-reruns", str(reruns)]
    if resume:
        argv += ["--resume"]

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
        description="Experiment 3 — Top-K sweep on config D (hierarchical+RAG+Workflow)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--prefix", required=True,
        help="User prefix for all run-ids (e.g. 'exp3_v1'). "
             "Final run-id = {prefix}_k{N}_{config_abbr}.",
    )
    parser.add_argument(
        "--models", default="xiaomi-mimo",
        help="Comma-separated provider names (default: xiaomi-mimo). "
             "Each model gets its own pass over the sweep.",
    )
    parser.add_argument(
        "--top-k-values", default="5,10,15,20,30",
        help="Comma-separated top_k values to sweep (default: 5,10,15,20,30).",
    )
    parser.add_argument("--reps", type=int, default=3, help="Repetitions per query (default: 3)")
    parser.add_argument("--max-reruns", type=int, default=10, dest="max_reruns", help="Retries for technical failures (default: 10)")
    parser.add_argument("--sample", type=int, default=50, help="Run only the first N queries (default: 50)")
    parser.add_argument("--resume", action="store_true", help="Skip experiments whose trace file already has entries")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without executing")
    args = parser.parse_args(argv)

    providers = [p.strip() for p in args.models.split(",") if p.strip()]
    top_k_values = [int(k.strip()) for k in args.top_k_values.split(",") if k.strip().isdigit()]

    if not top_k_values:
        print("No valid top_k values. Check --top-k-values.", file=sys.stderr)
        return 2

    cfg_path, abbr, desc = SWEEP_CONFIG

    # ── print plan ─────────────────────────────────────────────────────────────
    n_experiments = len(top_k_values) * len(providers)
    n_traces = n_experiments * args.sample * args.reps
    print(f"\n{'='*60}")
    print(f"Experiment 3 (Top-K sweep) plan  —  prefix={args.prefix}")
    print(f"{'='*60}")
    print(f"  Models      : {providers}")
    print(f"  Top-K values: {top_k_values}")
    print(f"  Config      : {abbr} ({desc})")
    print(f"  Reps        : {args.reps}")
    print(f"  Reruns      : {args.max_reruns}")
    print(f"  Sample      : {args.sample}")
    print(f"  Resume      : {args.resume}")
    print(f"  Total experiments : {n_experiments}")
    print(f"  Total traces      : ~{n_traces}")
    print()

    for provider in providers:
        pd = PROVIDER_DEFAULTS.get(provider, {"workers": 3, "rate_limit": 0})
        for k in top_k_values:
            run_id = _build_run_id(args.prefix, k, abbr)
            w = pd.get("workers", 3)
            rpm = pd.get("rate_limit", 0)
            rpm_str = f"  rpm={rpm}" if rpm > 0 else ""
            print(f"  {run_id:<45s}  w={w}{rpm_str}  (top_k={k})")
    print()

    if args.dry_run:
        print("[dry-run] No experiments executed.")
        return 0

    # ── execute ─────────────────────────────────────────────────────────────────
    results: list[dict[str, Any]] = []
    total_start = time.time()
    temp_files: list[Path] = []

    try:
        for provider in providers:
            pd = PROVIDER_DEFAULTS.get(provider, {"workers": 3, "rate_limit": 0})
            model = pd.get("model")
            for k in top_k_values:
                run_id = _build_run_id(args.prefix, k, abbr)
                w = pd.get("workers", 3)
                rpm = pd.get("rate_limit", 0)

                # Generate temp config with top_k override
                tmp_config = _generate_topk_config(cfg_path, k)
                temp_files.append(tmp_config)

                result = _run_experiment(
                    run_id=run_id,
                    config_path=tmp_config,
                    provider=provider,
                    model=model,
                    reps=args.reps,
                    workers=w,
                    rate_limit=rpm,
                    reruns=args.max_reruns,
                    resume=args.resume,
                    dry_run=False,
                    prefix=args.prefix,
                    sample=args.sample,
                )
                results.append(result)

                status_icon = "OK" if result["status"] == "ok" else "FAIL"
                elapsed_s = result.get("elapsed_s", 0)
                print(f"\n[{status_icon}] {run_id}  status={result['status']}  elapsed={elapsed_s}s")

    finally:
        # Clean up temp config files
        for tf in temp_files:
            try:
                tf.unlink(missing_ok=True)
            except Exception:
                pass

    # ── summary ──────────────────────────────────────────────────────────────────
    total_elapsed = time.time() - total_start
    ok_count = sum(1 for r in results if r["status"] == "ok")
    fail_count = len(results) - ok_count

    print(f"\n{'='*60}")
    print(f"Experiment 3 complete")
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
            {
                "experiment": "exp3_topk_sweep",
                "prefix": args.prefix,
                "top_k_values": top_k_values,
                "results": results,
                "elapsed_s": round(total_elapsed, 1),
            },
            indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )
    print(f"  Result summary saved to: {result_path}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
