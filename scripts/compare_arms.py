"""Score experiment arms and test the differences between them properly.

Replaces the ad-hoc per-wave analysis scripts. The scoring half is the same as
before; the point of this file is the statistics, which were previously done by
eye and twice led to a wrong conclusion being adopted and then reversed.

Three things the design has to respect:

**The sampling unit is the case, not the run.** Reps of the same golden case are
repeated measures of one item, not independent samples. Treating 318 runs as 318
observations overstates n by the rep factor and makes everything look
significant. Every test here aggregates to a per-case mean first and then treats
the 106 cases as the sample.

**Comparisons are paired.** Arms share seeds, so case *c* under arm X and case
*c* under arm Y are matched. Pairing removes between-case difficulty variance,
which dominates: cases range from trivial to impossible, and that spread is far
larger than any effect being measured.

**The measurement is noisy per call.** LongCat-2.0 is not deterministic at
temperature 0 — one case measured 2/5 vs 3/5 on identical config, seed and tree.
So a difference is reported with an interval, not just a point estimate, and a
per-case story is only evidence when the same case moves in most reps.

Reported per comparison:

* mean paired difference in the metric, with a **95% bootstrap CI** resampling
  *cases* (10k resamples, seeded — reruns give identical numbers);
* a **two-sided paired permutation test** (10k sign flips of the per-case
  differences), which assumes only that the differences are symmetric under H0;
* **Holm-corrected** p-values across all arms compared against the reference, so
  comparing seven arms to one baseline does not manufacture a hit;
* the run-level **fixed/broke ledger**, which is descriptive only — it is not a
  test, because its units are correlated within a case.

Usage:

    python scripts/compare_arms.py \
        --arm A:results_w14:A --arm Jnew:results_w14:Jnew \
        --ref A --metric task_success
"""
from __future__ import annotations

import argparse
import io
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from eval.metrics import evaluate_traces  # noqa: E402
from eval.schema import load_golden_dataset  # noqa: E402

TRIALS = Path(r"D:/GitHub/SCADA-trials")
MAX_REPS = 12
BOOTSTRAP = 10_000
PERMUTATIONS = 10_000
SEED = 20260731
NOACT = {"reject", "ask_for_clarification", "fail_or_clarify"}


# ------------------------------------------------------------------ loading
def load_arm(root: Path, prefix: str) -> dict[tuple[str, int], dict]:
    traces: dict[tuple[str, int], dict] = {}
    for rep in range(MAX_REPS):
        path = root / f"{prefix}_rep{rep}" / "traces.jsonl"
        if not path.is_file():
            continue
        for line in path.open(encoding="utf-8"):
            if not line.strip():
                continue
            trace = json.loads(line)
            execution = trace.get("execution") or {}
            if execution.get("total_turns", 0) == 0 and execution.get("terminal_state") == "UNKNOWN":
                continue
            traces[(trace["query"]["golden_id"], rep)] = trace
    return traces


# ------------------------------------------------------------------ statistics
def per_case_means(rows: list[dict], metric: str) -> dict[str, float]:
    """Collapse reps to one value per case — the unit the tests operate on."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(metric)
        if value is not None:
            buckets[row["golden_id"]].append(float(value))
    return {case: statistics.mean(vals) for case, vals in buckets.items()}


def bootstrap_ci(diffs: list[float], rng: random.Random,
                 resamples: int = BOOTSTRAP, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile CI for the mean paired difference, resampling cases."""
    n = len(diffs)
    if n < 2:
        return (float("nan"), float("nan"))
    means = []
    for _ in range(resamples):
        means.append(statistics.fmean(diffs[rng.randrange(n)] for _ in range(n)))
    means.sort()
    lo = means[int((alpha / 2) * resamples)]
    hi = means[min(resamples - 1, int((1 - alpha / 2) * resamples))]
    return lo, hi


def permutation_p(diffs: list[float], rng: random.Random,
                  permutations: int = PERMUTATIONS) -> float:
    """Two-sided paired permutation test: flip the sign of each case difference.

    Under H0 the arm label carries no information, so a per-case difference is
    equally likely to have either sign. Non-zero differences only — all-zero
    cases contribute nothing either way.
    """
    observed = abs(statistics.fmean(diffs))
    nonzero = [d for d in diffs if d != 0.0]
    if not nonzero:
        return 1.0
    n = len(diffs)
    hits = 0
    for _ in range(permutations):
        total = 0.0
        for d in diffs:
            total += d if rng.random() < 0.5 else -d
        if abs(total / n) >= observed - 1e-12:
            hits += 1
    return (hits + 1) / (permutations + 1)


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni step-down adjustment; controls family-wise error."""
    ordered = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(ordered)
    adjusted: dict[str, float] = {}
    running = 0.0
    for i, (name, p) in enumerate(ordered):
        value = min(1.0, (m - i) * p)
        running = max(running, value)  # enforce monotonicity
        adjusted[name] = running
    return adjusted


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", required=True, help="NAME:RESULTS_DIR:PREFIX")
    ap.add_argument("--ref", required=True, help="arm every other arm is tested against")
    ap.add_argument("--metric", default="task_success")
    ap.add_argument("--subset", choices=["all", "noact", "capability"], default="all")
    ap.add_argument("--dataset", default=str(REPO / "eval" / "golden_dataset.jsonl"))
    ap.add_argument("--title", default="arm comparison")
    args = ap.parse_args()

    golden = load_golden_dataset(args.dataset)
    keep = {g.id for g in golden}
    if args.subset == "noact":
        keep = {g.id for g in golden if g.expected_behavior in NOACT}
    elif args.subset == "capability":
        keep = {g.id for g in golden if g.expected_behavior not in NOACT}

    arms: dict[str, dict[str, float]] = {}
    runs: dict[str, dict[tuple[str, int], dict]] = {}
    order: list[str] = []
    for spec in args.arm:
        name, results_dir, prefix = spec.split(":")
        traces = load_arm(TRIALS / results_dir, prefix)
        traces = {k: v for k, v in traces.items() if k[0] in keep}
        if not traces:
            print(f"!! {name}: no traces under {results_dir} ({prefix}_rep*)")
            continue
        keys = sorted(traces)
        rows = evaluate_traces([traces[k] for k in keys], golden, skip_missing_golden=True)
        for k, r in zip(keys, rows, strict=False):
            r["rep"] = k[1]
        arms[name] = per_case_means(rows, args.metric)
        runs[name] = {k: r for k, r in zip(keys, rows, strict=False)}
        order.append(name)

    if args.ref not in arms:
        print(f"reference arm {args.ref!r} not loaded")
        return 1

    print("=" * 96)
    print(f"{args.title}  —  metric={args.metric}  subset={args.subset}  ref={args.ref}")
    print("=" * 96)
    print(f"{'arm':8s}{'cases':>7s}{'reps':>6s}{'mean':>9s}   "
          f"{'Δ vs ref':>9s}  {'95% CI (bootstrap over cases)':>31s}  {'perm p':>9s}{'Holm p':>9s}")

    raw_p: dict[str, float] = {}
    lines: dict[str, tuple] = {}
    for name in order:
        cases = arms[name]
        nreps = len({k[1] for k in runs[name]})
        mean = 100 * statistics.fmean(cases.values())
        if name == args.ref:
            lines[name] = (len(cases), nreps, mean, None, None, None)
            continue
        shared = sorted(set(cases) & set(arms[args.ref]))
        diffs = [cases[c] - arms[args.ref][c] for c in shared]
        rng = random.Random(SEED)
        lo, hi = bootstrap_ci(diffs, rng)
        p = permutation_p(diffs, random.Random(SEED + 1))
        raw_p[name] = p
        lines[name] = (len(shared), nreps, mean, 100 * statistics.fmean(diffs),
                       (100 * lo, 100 * hi), p)

    adj = holm(raw_p) if raw_p else {}
    for name in order:
        n, nreps, mean, delta, ci, p = lines[name]
        if delta is None:
            print(f"{name:8s}{n:>7d}{nreps:>6d}{mean:>9.2f}   {'—':>9s}  {'(reference)':>31s}")
            continue
        star = "" if adj[name] >= 0.05 else ("***" if adj[name] < 0.001 else
                                             "**" if adj[name] < 0.01 else "*")
        print(f"{name:8s}{n:>7d}{nreps:>6d}{mean:>9.2f}   {delta:>+9.2f}  "
              f"{f'[{ci[0]:+.2f}, {ci[1]:+.2f}]':>31s}  {p:>9.4f}{adj[name]:>8.4f}{star}")

    print()
    print("  Δ and CI are in percentage points, paired per case. n = cases, not runs:")
    print("  reps of a case are repeated measures, so runs are not independent observations.")
    print("  Holm-corrected across the arms tested against the reference. * <.05  ** <.01  *** <.001")
    print("  A CI spanning 0 means the data do not distinguish the arms, whatever the means say.")

    # descriptive only, and labelled as such
    print()
    print("  run-level ledger (descriptive, NOT a test — runs are correlated within a case):")
    for name in order:
        if name == args.ref:
            continue
        common = sorted(set(runs[name]) & set(runs[args.ref]))
        won = sum(1 for k in common
                  if runs[name][k][args.metric] and not runs[args.ref][k][args.metric])
        lost = sum(1 for k in common
                   if runs[args.ref][k][args.metric] and not runs[name][k][args.metric])
        print(f"    {name:8s} n={len(common):4d}  {name} better on {won:3d}, "
              f"{args.ref} better on {lost:3d}, net {won - lost:+d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
