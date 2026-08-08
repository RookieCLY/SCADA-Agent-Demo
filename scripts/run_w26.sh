#!/usr/bin/env bash
# results_w26 — second-model replication: A vs J on gpt-5.6-terra, 3 reps.
#
# LongCat's key is gone, xiaomi-mimo returns 401, and the NVIDIA endpoint times
# out, so the only reachable second model is another docode model. Same
# provider, different base model — that limitation is stated wherever these
# numbers are reported. Purpose: check the sign and rough size of the J-vs-A
# margin replicates off gpt-5.6-terra; w26 is exploratory/secondary by
# construction, no primary claim rests on it.
#
# Run AFTER w25 completes — never concurrently with another docode wave, or
# both waves' latency numbers are contaminated (see the w21 postmortem).
#
# golden-059/-074: excluded to keep pairing with the terra waves, and the same
# provider filter is likely to refuse them here too.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=".venv/Scripts/python.exe"
ROOT="../results_w26"

BLOCKED="golden-059,golden-074"

for i in 0 1 2; do
  seed=$((42 + i))
  for arm in J_combined:J A_flat_baseline:A; do
    cfg="${arm%%:*}"; tag="${arm##*:}"
    echo "=== $tag rep$i (seed $seed) ==="
    "$PY" -m eval.runner --config "configs/${cfg}.yaml" --all \
      --exclude-golden-ids "$BLOCKED" \
      --provider docode --model gpt-5.6-terra \
      --reps 1 --seed-base "$seed" \
      --results-root "$ROOT" --run-id "${tag}_rep${i}" \
      --resume --max-reruns 0 2>&1 | tail -2 || true
  done
done
echo "W26 DONE"
