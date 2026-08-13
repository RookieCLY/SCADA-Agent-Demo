#!/usr/bin/env bash
# results_w29 — the FULL ablation matrix on one modern context: 12 arms x 3
# reps, gpt-5.6-terra via the wegoo relay, default reasoning effort, seeds
# 42-44, one tree (post-K14 repairs). Everything reruns — including A and J —
# because the K14 tool/metric repairs changed the tree after results_w25, and
# a matrix is only a matrix if every cell shares tree+model+endpoint.
#
# Arms: A B C D E F Fnr G H Ir Ip Im J
#   A_flat_baseline, B_hierarchical_only, C_hier_rag, D_hier_rag_workflow,
#   E_with_state_machine, F_full_four_in_one, F_noresources, G_safety_runtime,
#   H_workflow_engine, I_react, I_plan_execute, I_multi_agent, J_combined.
#
# Arms are interleaved WITHIN each rep so endpoint drift lands on every arm
# equally; the per-rep order is fixed and arbitrary (declared before any data).
#
# PRE-SPECIFIED (fixed before any w29 data; do not change after looking):
#   The paper's PRIMARY claim remains results_w25 (J vs A, pre-specified
#   there). w29's role is the modern-model ablation. Within w29:
#     KEY COMPARISONS (Holm family of 4, ref A): J-A, F-A, I_plan_execute-A,
#       and J-F (the "levers alone vs levers+loop" contrast; J-F tested with
#       ref F in a second invocation, declared part of the same family).
#     Everything else (B/C/D/E/Fnr/G/H/Ir/Im vs A) is descriptive/exploratory:
#       reported with CIs, no significance claims.
#   METRIC task_success; subsets reported all/capability/noact.
#
# PROBE (same wave, same tree): A x3 + J x3 + K13 x3 on the rebuilt safety
# probe, so the preservation table is finally same-model AND same-tree
# (results_w27 predates the K14 repairs, whose diff-granularity changes what
# world_diffs record). Score with score_safety_probe.py, preservation rate.
#
# COST ESTIMATE at w25-measured rates: interleaved-family arms (A..H) are
# ~4-13M input tokens per rep; J-family ~1.2M. Whole wave very roughly
# 150-350M input tokens and ~a day of wall clock. Resumable at any point.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=".venv/Scripts/python.exe"
ROOT="../results_w29"

export DOCODE_REASONING_EFFORT="${DOCODE_REASONING_EFFORT:-}"

BLOCKED="golden-059,golden-074"

ARMS="A_flat_baseline:A B_hierarchical_only:B C_hier_rag:C D_hier_rag_workflow:D E_with_state_machine:E F_full_four_in_one:F F_noresources:Fnr G_safety_runtime:G H_workflow_engine:H I_react:Ir I_plan_execute:Ip I_multi_agent:Im J_combined:J"

for i in 0 1 2; do
  seed=$((42 + i))
  for arm in $ARMS; do
    cfg="${arm%%:*}"; tag="${arm##*:}"
    echo "=== $tag rep$i (seed $seed) ==="
    "$PY" -m eval.runner --config "configs/${cfg}.yaml" --all \
      --exclude-golden-ids "$BLOCKED" \
      --provider docode --model gpt-5.6-terra \
      --reps 1 --seed-base "$seed" \
      --results-root "$ROOT" --run-id "${tag}_rep${i}" \
      --resume --max-reruns 1 2>&1 | tail -1 || true
  done
done

# ---- probe wave, same tree, same session
for i in 0 1 2; do
  seed=$((42 + i))
  for arm in A_flat_baseline:probeA J_combined:probeJ K13_cage1:probeK13; do
    cfg="${arm%%:*}"; tag="${arm##*:}"
    echo "=== $tag rep$i (seed $seed) ==="
    "$PY" -m eval.runner --config "configs/${cfg}.yaml" \
      --dataset "eval/golden_safety_probe.jsonl" --all \
      --provider docode --model gpt-5.6-terra \
      --reps 1 --seed-base "$seed" \
      --results-root "$ROOT" --run-id "${tag}_rep${i}" \
      --resume --max-reruns 1 2>&1 | tail -1 || true
  done
done
echo "W29 DONE"
