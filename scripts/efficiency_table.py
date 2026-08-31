"""Paired efficiency comparison between arms: tokens, latency, tool calls.

Accuracy comparisons live in ``compare_arms.py``; this reports the *cost*
dimensions with the same statistical discipline — per-case means first (reps
are repeated measures), paired per case, bootstrap CI over cases. Latency
claims additionally require the arms to have run interleaved on the same
endpoint in the same session (see run_w25.sh); token counts are robust to
endpoint load, latency is not, and the table says which is which.

Usage:
    python scripts/efficiency_table.py --ref A \
        --arm A:results_w25:A --arm J:results_w25:J
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from compare_arms import (  # noqa: E402
    TRIALS,
    _force_utf8_stdout,
    bootstrap_ci,
    load_arm,
)

#: (label, unit, extractor over one trace)
DIMENSIONS = [
    ("input_tokens", "tok", lambda t: (t.get("totals") or {}).get("input_tokens") or 0),
    ("output_tokens", "tok", lambda t: (t.get("totals") or {}).get("output_tokens") or 0),
    ("e2e_latency", "s", lambda t: ((t.get("totals") or {}).get("e2e_latency_ms") or 0) / 1000),
    ("llm_calls", "n", lambda t: len(t.get("llm_calls") or [])),
    ("tool_calls", "n", lambda t: len(t.get("tool_calls") or [])),
]


def per_case(traces: dict, extract) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for (gid, _rep), trace in traces.items():
        buckets.setdefault(gid, []).append(float(extract(trace)))
    return {gid: statistics.mean(vals) for gid, vals in buckets.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", required=True, help="NAME:RESULTS_DIR:PREFIX")
    ap.add_argument("--ref", required=True)
    ap.add_argument("--trials-root", default=None)
    args = ap.parse_args()

    trials = Path(args.trials_root) if args.trials_root else TRIALS
    arms: dict[str, dict] = {}
    order: list[str] = []
    for spec in args.arm:
        name, results_dir, prefix = spec.split(":")
        traces = load_arm(trials / results_dir, prefix)
        if not traces:
            print(f"!! {name}: no traces under {results_dir} ({prefix}_rep*)")
            continue
        arms[name] = traces
        order.append(name)
    if args.ref not in arms:
        print(f"reference arm {args.ref!r} not loaded")
        return 1

    print("=" * 100)
    print(f"efficiency — per-case means, paired vs {args.ref}; latency is only "
          "claimable for arms run interleaved in one session")
    print("=" * 100)
    for label, unit, extract in DIMENSIONS:
        ref_cases = per_case(arms[args.ref], extract)
        print(f"\n{label} ({unit}):")
        for name in order:
            cases = per_case(arms[name], extract)
            mean = statistics.fmean(cases.values())
            if name == args.ref:
                print(f"  {name:8s} mean {mean:12,.1f}   (reference)")
                continue
            shared = sorted(set(cases) & set(ref_cases))
            diffs = [cases[c] - ref_cases[c] for c in shared]
            lo, hi = bootstrap_ci(diffs, random.Random(20260731))
            ratio = statistics.fmean(ref_cases[c] for c in shared) / mean if mean else float("inf")
            print(f"  {name:8s} mean {mean:12,.1f}   Δ {statistics.fmean(diffs):+12,.1f} "
                  f"  CI [{lo:+,.1f}, {hi:+,.1f}]   ref/arm ratio {ratio:6.1f}x")
    return 0


if __name__ == "__main__":
    _force_utf8_stdout()
    raise SystemExit(main())
