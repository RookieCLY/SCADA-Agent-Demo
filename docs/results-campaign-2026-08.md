# Results: the August 2026 J-vs-A campaign

*Final numbers, how to regenerate each one, and the caveat that belongs to it.
Every table reproduces from the archived traces with the command beneath it;
none of these figures is hand-maintained.*

Arms: **A** = flat baseline (all architecture levers off, native function
calling every turn). **J** = the shipping caged architecture
(`configs/J_combined.yaml`: four surface levers + plan-first arbitration +
§4.7 runtime cage). Dataset: 106-case golden set; golden-059/-074 excluded on
terra (upstream content filter refuses them at HTTP level — dropped for *both*
arms so comparisons stay paired).

---

## 1. Primary result (pre-specified)

`results_w25` — A × 5 + J × 5, seeds 42–46, both arms fresh on one tree, arms
interleaved per rep on one endpoint (gpt-5.6-terra via the wegoo relay,
default reasoning effort). The primary comparison, subsets, and efficiency
dimensions were fixed in `scripts/run_w25.sh`'s header before any data
existed.

| dimension | A | J | margin |
|---|---:|---:|---|
| task_success, all cases (**primary**) | 60.38 | **82.88** | **+22.50pp, CI [+15.58, +29.62], p = 0.0001**, ledger +125/−8 |
| capability subset (73 cases) | 47.95 | **76.16** | +28.22pp, p = 0.0001 |
| no-act subset (31 cases) | 89.68 | **98.71** | +9.03pp, p = 0.024 |
| input tokens / run | 96,048 | **11,331** | **8.5×** |
| output tokens / run | 554 | **228** | 2.4× |
| e2e latency / run | 31.6 s | **7.3 s** | **4.3×** |
| LLM calls / run | 3.9 | **1.1** | 3.5× |

```powershell
.venv\Scripts\python.exe scripts\compare_arms.py --ref A --arm A:results_w25:A --arm J:results_w25:J --subset all   # then capability, noact
.venv\Scripts\python.exe scripts\efficiency_table.py --ref A --arm A:results_w25:A --arm J:results_w25:J
```

**The latency row is claimable only because the arms ran interleaved in one
session on one endpoint** — the first wave in this project's history where
that held (see the w21 postmortem for why it matters).

## 2. The disclosed artifact and its sensitivity bound

**Report these together, always.** 73 of A's 520 runs (14%) are *tool-starved*:
the relay intermittently drops or mangles the function-calling payload, and
the model answers "当前会话未暴露…可调用接口". A depends on native tool calls
every turn; J's plan tier is plain-JSON text and is structurally untouched.

Under the maximally A-favorable correction — scoring **every** starved A run
as a pass — A rises to 70.77 and the margin is still
**+12.12pp, CI [+6.73, +17.50], p = 0.0001**.

The artifact is itself a finding: the caged architecture is robust to a
serving-stack failure mode the flat baseline structurally cannot tolerate.
Claim it as robustness; never fold it silently into capability.

## 3. Safety: the probe, and one flag worth +31.5pp

`results_w27` (rebuilt §4.7 probe, probe-101+, scored by **preservation
rate** — never `task_success`; see the w20/w21 instrument postmortem).

| arm (same model, same prompt) | preservation | destructive ops executed | verdict |
|---|---:|---:|---|
| J, `max_destructive_ops: 3` | 48.5% | 200 | the K11 refusal scope lets plausible single deletes plan, so the budget is the only line of defense — and at 3 it concedes the first three ops of every bulk sequence |
| J, `max_destructive_ops: 1` (K13) | **80.0%** | **69** | **+31.5pp, CI [+21.3, +40.8], p = 0.0001**, 14 cases better / 1 worse, identical tokens/latency |

Capability cost of the tighter budget: **measured zero** — no passing golden
run among w25's 520 J runs performs ≥2 destructive operations. Promoted into
`J_combined.yaml`. The division of labor is explicit: *the prompt decides what
is legitimate; the cage bounds how much of it can be destructive per task.*

```powershell
.venv\Scripts\python.exe scripts\score_safety_probe.py --ref J25 --arm J25:results_w27:J --arm K13:results_w27:K13 --group all
```

Do not compare w27 preservation levels against `results_w21` — those arms ran
LongCat-2.0; only the within-w27 single-flag comparison is same-model.

## 4. Robustness of the sign across serving contexts

| context | J − A (task_success) | status |
|---|---|---|
| LongCat-2.0 (w14–w16) | +8.33pp, p = 0.019 (pre-specified primary) | archived |
| docode-terra (w23/w24, 3 reps) | +6.41pp, p = 0.061 | archived; cross-tree caveat in CLAUDE.md |
| wegoo-terra (w25, 5 reps) | **+22.50pp, p = 0.0001**; floor +12.12 under the artifact bound | primary |

Same-named models on different relays are **different measurement contexts**
(w25-A scored 9pp below w23-A on identical cases and seeds). Label every
number with its serving context; the claim that generalizes is the *sign and
the mechanism*, not the size.

## 5. What did not ship, and why that is evidence

The K14 wave (`results_w28`, three iterations, each J×3 vs archived w25-J,
guard tripwire at case 40, rule: no ship without significance or with guard
regressions):

- Two prompt bundles measured **net negative** (−1.47pp each) and were
  reverted. The instructive failure: the dataset expects "全套搞定"
  (golden-026) to act but "你都看着办" (golden-090) to ask — no prompt rule
  can straddle that line.
- Five pure repairs (truthful page diffs, clone re-parenting, expressible
  batch ranges, HH-priority convention, colour-notation equivalence in the
  metric) measured **+1.41pp, CI [−0.38, +3.40], p = 0.17** — kept as defect
  fixes, **no accuracy claim made**. The headline J number was measured
  before them and stands unchanged.

After K14, J's residual failures are dominated by dataset naming-lottery
(expected ids no convention can derive), relay function-calling flakiness
hitting the fallback tier, and single-rep nondeterminism — not by fixable
architecture mechanisms.

## 6. Statistical discipline (methods paragraph, ready to adapt)

The sampling unit is the case, never the run: reps are repeated measures, so
per-case means are formed first and all tests operate over cases (n = 104).
Comparisons are paired per case (arms share seeds); intervals are 10k-resample
bootstrap CIs over cases; tests are two-sided paired permutation (10k sign
flips); families of comparisons are Holm-corrected. Primary and secondary
comparisons were pre-specified in the wave scripts before data collection;
everything else is labelled exploratory. Void runs (no terminal state) are
excluded and *counted* rather than silently dropped; provider-refused cases
are excluded explicitly and identically for every arm.
