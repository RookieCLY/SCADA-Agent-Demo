#!/usr/bin/env bash
# results_w24 — K11 (w23 residue wave) on gpt-5.6-terra, 104 golden cases x 3
# reps, seeds 42-44 matching results_w23 so the archived w23 A and K10 runs are
# the paired references (their code paths are untouched by the K11 changes that
# matter to the plan tier; the two tool-schema edits also reach A, which is why
# the *confirmatory* wave re-runs A on the final tree — this wave is the cheap
# mechanism check against the matched control K10).
#
# Idempotent: --resume with a fixed --run-id, safe to re-run.
#
# PRE-SPECIFIED (before any w24 data): PRIMARY K11 vs A (w23) all cases;
# SECONDARY K11 vs K10 (w23). Mechanism cases expected to move:
#   007 015 017 020 026 027 031 040 046 048 057 066 069 071 073 080 084 086
#   087 092 098 104
# Guard cases expected NOT to regress (reject/clarify/fail_or_clarify and
# current K10 wins): 006 008 011 021 022 023 024 028 030 034 039 042 045 049
#   055 060 067 076 078 089 091 093
set -uo pipefail
cd "$(dirname "$0")/.."
PY=".venv/Scripts/python.exe"
ROOT="../results_w24"

# Provider-blocked (HTTP 400 content filter), excluded for pairing — see run_w23.sh.
BLOCKED="golden-059,golden-074"

for i in 0 1 2; do
  seed=$((42 + i))
  echo "=== K11 rep$i (seed $seed) ==="
  "$PY" -m eval.runner --config "configs/K11_residue.yaml" --all \
    --exclude-golden-ids "$BLOCKED" \
    --provider docode --model gpt-5.6-terra \
    --reps 1 --seed-base "$seed" \
    --results-root "$ROOT" --run-id "K11_rep${i}" \
    --resume --max-reruns 0 2>&1 | tail -2 || true
done
echo "W24 DONE"
