#!/usr/bin/env bash
# results_w27 — the rebuilt §4.7 safety probe (probe-101+) under the shipping J
# after the K11 residue wave, 5 reps, gpt-5.6-terra, seeds 42-46.
#
# Why: K11 rewrote the planner's refusal scope (safety-critical/unscoped-bulk
# refuse; single named non-safety deletes execute). That is exactly the surface
# the probe measures, so the w21 K9 numbers do not carry over to the current
# prompt. Score with scripts/score_safety_probe.py (preservation rate, NOT
# task_success — see the w20/w21 postmortem), against the archived w21 arms.
#
# Run AFTER w25/w26 — never concurrently with another docode wave.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=".venv/Scripts/python.exe"
ROOT="../results_w27"

export DOCODE_REASONING_EFFORT="${DOCODE_REASONING_EFFORT:-}"

for i in 0 1 2 3 4; do
  seed=$((42 + i))
  echo "=== J probe rep$i (seed $seed) ==="
  "$PY" -m eval.runner --config "configs/J_combined.yaml" \
    --dataset "eval/golden_safety_probe.jsonl" --all \
    --provider docode --model gpt-5.6-terra \
    --reps 1 --seed-base "$seed" \
    --results-root "$ROOT" --run-id "J_rep${i}" \
    --resume --max-reruns 0 2>&1 | tail -2 || true
done
echo "W27 DONE"
