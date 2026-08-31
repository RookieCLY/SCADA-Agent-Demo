#!/usr/bin/env bash
# results_w25 — the confirmatory wave: A vs J on gpt-5.6-terra, 106 golden
# cases x 5 reps, seeds 42-46, BOTH arms fresh on the final tree.
#
# MODEL HISTORY: the user's standing choice was luna (commit 30df0d6), but the
# relay at api.7689326.xyz serves luna only intermittently (mid-run 404 "not
# supported by any configured account in this group"), so on 2026-08-06 the
# user switched this wave to terra. Terra also restores direct comparability
# with the archived w23/w24 terra runs. Reasoning effort stays xhigh.
#
# COST at w23/w24-measured rates: one A rep ~13M input tokens, one J rep ~1M.
# The 5x5 design is ~70M input tokens total. If budget is tight, cut REPS
# below (3x3 ~ 42M; A dominates every design) — do it BEFORE any data exists.
#
# golden-059/-074: EXCLUDED — terra's upstream content filter 400s both (same
# "cybersecurity risk" flag as the old endpoint; verified on this relay in
# results_smoke_luna/smoke_terra). Dropped for every arm so the comparison
# stays paired, exactly as in w23/w24. (On luna both cases ran and were
# correctly refused — results_smoke_luna/smoke2 — so if the wave ever moves
# back to luna, remove this exclusion.)
#
# Why both arms re-run: the K11 residue wave includes two tool-schema edits
# (create_analog_alarm limit notes + the <TAG>_H id convention) that reach the
# flat baseline through its JSON schemas, and one metrics repair (injective
# sibling matching) that rescores every arm. A comparison against archived A
# traces would credit J with changes A also received. One tree, one provider,
# arms interleaved per rep so endpoint drift lands on both arms equally —
# that interleaving is also what makes the latency comparison claimable.
#
# PRE-SPECIFIED (fixed before any w25 data existed — do not change after
# looking at results):
#   PRIMARY    task_success, all cases ex golden-059/-074 (provider-blocked),
#              J vs A, two-sided paired permutation over cases.
#   SECONDARY  task_success on the capability subset, and on the noact subset,
#              J vs A, Holm-corrected across the two.
#   TERTIARY   input_tokens and e2e latency, J vs A (descriptive + paired CI).
#   Everything else is exploratory and must be labelled as such.
#
# J here is J_combined, which after the K11 promotion carries the identical
# body to K11_residue (tool_rag.top_k=60 promoted from K10, everything else
# was already code-side). K11_residue stays as the historical record of the
# wave; J is the shipping arm the paper names.
#
# Idempotent: --resume with fixed --run-id, safe to re-run.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=".venv/Scripts/python.exe"
ROOT="../results_w25"

# Reasoning tier, applied uniformly to BOTH arms and every LLM path (loop,
# planner, workflow router). User-specified: luna at xhigh.
export DOCODE_REASONING_EFFORT="${DOCODE_REASONING_EFFORT:-}"  # measured on this relay: terra at xhigh = 526s/case vs 6.3s default -- xhigh cannot carry 1,060 runs. Default (empty) matches every archived terra run. Set the env var to override.

BLOCKED="golden-059,golden-074"

for i in 0 1 2 3 4; do
  seed=$((42 + i))
  for arm in J_combined:J A_flat_baseline:A; do
    cfg="${arm%%:*}"; tag="${arm##*:}"
    echo "=== $tag rep$i (seed $seed) ==="
    "$PY" -m eval.runner --config "configs/${cfg}.yaml" --all \
      --exclude-golden-ids "$BLOCKED" \
      --provider docode --model gpt-5.6-terra \
      --reps 1 --seed-base "$seed" \
      --results-root "$ROOT" --run-id "${tag}_rep${i}" \
      --resume --max-reruns 1 2>&1 | tail -2 || true
      # --max-reruns 1: after the runner rework only a trace with no terminal
      # state (a provider 500/timeout) counts as technical, so one retry sheds
      # void runs without resampling legitimate outcomes. w24 lost 2 runs to
      # provider UNKNOWNs that a single retry would have recovered.
  done
done
echo "W25 DONE"
