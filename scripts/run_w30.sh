#!/usr/bin/env bash
# results_w30 — SECOND-MODEL REPLICATION of the w29 ablation matrix.
#
# 13 arms x 3 reps, deepseek-v4-flash via api.deepseek.com, seeds 42-44, one
# tree (the same post-K14 tree w29 ran on). This is the wave `run_w26.sh` was
# supposed to be and could not be: w26 hardcoded gpt-5.6-terra, the same model
# as w25/w29, so it could not replicate anything. DeepSeek v4 Flash is a
# genuinely different base model from a different vendor, which is the first
# time this campaign has had one since LongCat's key died.
#
# Arms and per-rep order are IDENTICAL to run_w29.sh so the two waves pair
# case-for-case and arm-for-arm. Arms are interleaved within each rep so
# endpoint drift lands on every arm equally.
#
# PRE-SPECIFIED (fixed before any w30 data; do not change after looking):
#   PRIMARY: does the w29 *interaction* finding replicate on a second model?
#     That finding is: neither the four surface levers alone (F) nor the plan
#     loop alone (Ip) beats the flat baseline A, but their combination (J)
#     does. It predicts three signs, and the prediction is the claim:
#       H1  J - A  > 0
#       H2  F - A  < 0
#       H3  J - F  > 0
#       H4  Ip - A ~ 0   (no separation; stated as the weak leg, not a test)
#     KEY COMPARISONS (Holm family of 4, ref A except J-F which uses ref F,
#     declared part of the same family): J-A, F-A, Ip-A, J-F.
#   Everything else (B/C/D/E/Fnr/G/H/Ir/Im vs A) is descriptive/exploratory:
#     reported with CIs, no significance claims.
#   METRIC task_success; subsets reported all/capability/noact.
#   REASONING EFFORT: default (no reasoning_effort sent). The provider returns
#     reasoning tokens by default; no tier is requested, matching how every
#     archived terra run was made.
#
# golden-059/-074 are excluded for EVERY arm, as in w23-w29. They are excluded
# here to keep the case set paired with the terra waves, NOT because DeepSeek
# refuses them — it may well answer them. Do not re-add them to one wave only.
#
# PROBE (same wave, same tree): A x3 + J x3 on the rebuilt safety probe. K13 is
# deliberately ABSENT: `K13_cage1` has been byte-identical to `J_combined`
# since the max_destructive_ops:1 promotion at w27, so w29's probe leg spent 60
# runs measuring J twice. Score with score_safety_probe.py, preservation rate.
#
# COST/TIME: DeepSeek measured at ~3-8 s/case on the plan tier and A. Rough
# estimate ~10 s/run averaged over arms => ~11 h wall clock. Resumable at any
# point; re-run this script to continue.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=".venv/Scripts/python.exe"
ROOT="../results_w30"

PROVIDER="deepseek"
MODEL="deepseek-v4-flash"
BLOCKED="golden-059,golden-074"

ARMS="A_flat_baseline:A B_hierarchical_only:B C_hier_rag:C D_hier_rag_workflow:D E_with_state_machine:E F_full_four_in_one:F F_noresources:Fnr G_safety_runtime:G H_workflow_engine:H I_react:Ir I_plan_execute:Ip I_multi_agent:Im J_combined:J"

for i in 0 1 2; do
  seed=$((42 + i))
  for arm in $ARMS; do
    cfg="${arm%%:*}"; tag="${arm##*:}"
    echo "=== $tag rep$i (seed $seed) ==="
    "$PY" -m eval.runner --config "configs/${cfg}.yaml" --all \
      --exclude-golden-ids "$BLOCKED" \
      --provider "$PROVIDER" --model "$MODEL" \
      --reps 1 --seed-base "$seed" \
      --results-root "$ROOT" --run-id "${tag}_rep${i}" \
      --resume --max-reruns 1 2>&1 | tail -1 || true
  done
done

# ---- probe wave, same tree, same session
for i in 0 1 2; do
  seed=$((42 + i))
  for arm in A_flat_baseline:probeA J_combined:probeJ; do
    cfg="${arm%%:*}"; tag="${arm##*:}"
    echo "=== $tag rep$i (seed $seed) ==="
    "$PY" -m eval.runner --config "configs/${cfg}.yaml" \
      --dataset "eval/golden_safety_probe.jsonl" --all \
      --provider "$PROVIDER" --model "$MODEL" \
      --reps 1 --seed-base "$seed" \
      --results-root "$ROOT" --run-id "${tag}_rep${i}" \
      --resume --max-reruns 1 2>&1 | tail -1 || true
  done
done
echo "W30 DONE"
