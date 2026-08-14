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

## 6. The full ablation matrix on a modern model (`results_w29`)

13 arms × 3 reps × 104 cases = **4,056 runs**, one tree (post-K14),
gpt-5.6-terra via the wegoo relay at default effort, seeds 42–44, arms
interleaved *within* each rep so endpoint drift lands on every arm equally.
Everything reran, including A and J, because the K14 repairs changed the tree
after w25 — a matrix is only a matrix if every cell shares tree, model and
endpoint. Coverage is complete: 104/104 scoreable cases in all 39 runs.

**Key comparisons** — the family of four fixed in `scripts/run_w29.sh` before
any data, Holm-corrected across those four (J−F was run with `--ref F`, declared
part of the same family):

| comparison | Δ | 95% CI | perm p | Holm p (family of 4) |
|---|---:|---|---:|---:|
| J − F (levers **plus** loop vs levers alone) | **+26.28** | [+18.27, +34.29] | 0.0001 | **0.0004** |
| F − A (four levers, no plan loop) | **−15.71** | [−23.72, −7.69] | 0.0002 | **0.0006** |
| J − A (shipping vs flat baseline) | **+10.58** | [+5.13, +16.67] | 0.0007 | **0.0014** |
| Ip − A (plan tier alone) | +5.45 | [−0.32, +11.54] | 0.0856 | 0.0856 |

Means: **J 84.29 · Ip 79.17 · A 73.72 · C 64.10 · B 63.46 · Im 60.58 · F 58.01 ·
G 54.49 · Fnr 54.17 · D 55.45 · Ir 52.56 · E 50.96 · H 44.23.** Run ledger vs A:
J +33 (38 better / 5 worse), Ip +17, everything else negative, H worst at −92.
All non-key arms are descriptive only.

Three readings, and the third is the one that matters:

- **The negative result replicates on a modern model.** Every architecture arm
  without a plan loop is *worse* than the flat baseline — B, C, D, E, F, Fnr, G,
  H, Ir and Im all sit below A with CIs excluding zero. This is the w14 finding
  reproduced on a different model, tree and provider.
- **Neither half explains J alone.** The plan tier by itself (Ip, +5.45) does not
  separate from A, and the levers by themselves (F, −15.71) actively hurt. Their
  combination is +26.28 over F and +10.58 over A. The levers only pay off once
  something else owns control flow — that interaction, not either component, is
  the architecture's contribution.
- **The margin has moved from safety to capability.** Split: capability
  **+12.33pp** (Holm p = 0.0062), no-act **+6.45pp, p = 0.77** (A is already at
  90.32 there, n = 31). On LongCat the entire J-vs-A margin was no-act; on
  gpt-5.6-terra the stronger base model has closed the safety gap on its own and
  what remains is task capability. **Do not carry the "the cage does not make the
  model more capable" claim forward to this model** — here it is the only thing
  that does.

Efficiency (latency claimable: interleaved, one session, one endpoint):

| dimension | A | F | Ip | J |
|---|---:|---:|---:|---:|
| input tokens / run | 172,281 | 35,959 | 23,102 | **11,443** (15.1×) |
| output tokens / run | 363 | 841 | 415 | **222** |
| e2e latency / run | 40.7 s | 86.9 s | 26.3 s | **11.0 s** (3.7×) |
| LLM calls / run | 3.1 | 6.1 | 2.5 | **1.0** |
| tool calls / run | 2.4 | 2.5 | 2.3 | 2.3 |

**No starvation artifact this wave.** The w25 disclosure (§2) does not apply:
zero of A's 312 runs carry the "未暴露…可调用接口" marker, so w29's A = 73.72
needs no sensitivity bound. That absence is also the most likely explanation for
A scoring 13pp above w25-A on the same cases — further evidence that the w25
artifact was real and that same-named models on different days are different
measurement contexts.

**The probe leg is a null, and one arm of it was a null by construction.**
A × 3 + J × 3 + K13 × 3 on the rebuilt probe, same tree and session:

| arm | preservation | denials | destructive ops executed | Δ vs A |
|---|---:|---:|---:|---|
| A | 82.50% | 0 | 0 | (reference) |
| J | 85.00% | 34 | 35 | +2.50pp, CI [−8.33, +15.00], p = 0.77 |
| K13 | 84.58% | 35 | 36 | +2.08pp, p = 0.80 |

`K13_cage1` is byte-identical to `J_combined` apart from its name — the
`max_destructive_ops: 1` flag was promoted into J at w27 — so that arm was a
duplicate of J and cost 60 redundant runs. It does buy one thing by accident: two
independent runs of the *same* config landed 0.42pp apart, which is a direct
empirical noise floor for this metric. Fix the wave script before reusing it.

The substantive result is that **on gpt-5.6-terra the flat baseline already
preserves 82.5%**, and the architecture's preservation advantage is not
detectable. This does not overturn §3 — that was a within-arm single-flag
comparison and stands — but it does bound its scope: the budget flag is worth
+31.5pp *given* an architecture that plans destructive work, while the
architecture-vs-baseline safety gap that motivated the cage has largely closed on
this model.

```powershell
.venv\Scripts\python.exe scripts\compare_arms.py --ref A --arm A:results_w29:A --arm J:results_w29:J --arm F:results_w29:F --arm Ip:results_w29:Ip
.venv\Scripts\python.exe scripts\compare_arms.py --ref F --arm F:results_w29:F --arm J:results_w29:J
.venv\Scripts\python.exe scripts\efficiency_table.py --ref A --arm A:results_w29:A --arm F:results_w29:F --arm Ip:results_w29:Ip --arm J:results_w29:J
.venv\Scripts\python.exe scripts\score_safety_probe.py --ref A --arm A:results_w29:probeA --arm J:results_w29:probeJ --arm K13:results_w29:probeK13
```

## 7. Second-model replication (`results_w30`, DeepSeek v4 Flash)

The first genuinely different base model this campaign has had since LongCat's
key died — a different vendor, not another docode model. (`run_w26.sh`, listed
as the pending second-model replication for weeks, hardcoded `gpt-5.6-terra`
and could not have replicated anything.) Same tree, same 104 cases, same seeds
42–44, arms interleaved within each rep, mirroring `run_w29.sh` cell for cell.
Hypotheses were fixed in `scripts/run_w30.sh` before any data: the wave tests
the w29 **interaction** finding as three predicted signs, not just "J wins".

**Key family, 3 reps each, Holm across the pre-specified family of four:**

| hypothesis | prediction | w29 (terra) | w30 (DeepSeek) | Holm p | verdict |
|---|---|---:|---:|---:|---|
| H3: J − F > 0 | levers need a loop | +26.28 | **+25.64** | 0.0004 | **replicates** |
| H2: F − A < 0 | levers alone hurt | −15.71 | **−22.44** | 0.0004 | **replicates** |
| H4: Ip − A ≈ 0 | loop alone doesn't separate | +5.45 n.s. | +0.96 | 0.84 | **replicates** |
| H1: J − A > 0 | the architecture wins | +10.58 | +3.21 [−2.56, +9.29] | 0.71 | **not supported** |

Means: A 75.32 · J 78.53 · Ip 76.28 · F 52.88.

**Three of four replicate on a second vendor's model; the headline does not.**
Read the two halves separately, because they say different things:

- **The negative result is the robust one.** F sits 22.4pp below a flat
  baseline overall and 34.3pp below on capability alone (ledger −75 of 219),
  and the plan loop rescues it by ~25pp. That pattern now holds on LongCat,
  terra and DeepSeek. *The four surface levers without something owning
  control flow reliably make a model worse* — this is the most reproducible
  claim in the campaign, and it is a negative one.
- **"J beats A" is model-dependent and should stop being stated flatly.**
  Across three serving contexts the margin tracks the baseline's weakness:
  +22.50 (wegoo, A = 60.38) → +10.58 (terra, A = 73.72) → +3.21 (DeepSeek,
  A = 75.32, the strongest baseline measured). The defensible claim is that
  the architecture brings a model *up to* a strong baseline's accuracy at a
  fraction of the tokens, and adds restraint where the model lacks it.

**Where the margin lands is itself model-specific**, which retires the last
general version of that claim:

| subset | w29 terra | w30 DeepSeek |
|---|---|---|
| capability | **+12.33** (p = 0.0062) | −0.46 (p = 1.00) |
| no-act | +6.45 (p = 0.77) | **+11.83** (raw p = 0.097, ledger +11/93) |

DeepSeek's baseline is *more capable* than terra's (A capability 73.52 vs
66.67) but *less cautious* (A no-act 79.57 vs 90.32) — a stronger, more eager
model. The cage contributes on exactly the axis the base model is weak on and
contributes nothing on the axis it is already strong. So the LongCat-era line
("the cage does not make the model more capable; it stops it doing what it
should not") is right here and wrong on terra: neither is the general rule.

**Efficiency — the one dimension that does not degrade with a better model:**

| dimension | A | F | Ip | J |
|---|---:|---:|---:|---:|
| input tokens / run | 220,843 | 18,720 | 9,988 | **6,359** (**34.7×**) |
| output tokens / run | 1,510 | 3,422 | 1,755 | **1,113** |
| LLM calls / run | 3.2 | 5.0 | 2.4 | **1.5** |
| tool calls / run | 5.6 | 3.1 | 2.1 | **1.7** |
| e2e latency / run | **13.1 s** | 29.4 s | 20.8 s | 15.8 s |

34.7× on input tokens is the largest ratio measured in the campaign. **The
latency advantage, however, does not replicate**: J is 15.8 s against A's
13.1 s (CI spans zero, so a tie at best), where w25 and w29 both showed J
3–4× faster. DeepSeek is fast enough that A's 220k-token prompts cost little
wall clock, while J's single large planning call cannot go below one round
trip. Report the token ratio; do not carry the latency claim to this model.

```powershell
.venv\Scripts\python.exe scripts\compare_arms.py --ref A --arm A:results_w30:A --arm J:results_w30:J --arm F:results_w30:F --arm Ip:results_w30:Ip
.venv\Scripts\python.exe scripts\compare_arms.py --ref F --arm F:results_w30:F --arm J:results_w30:J
.venv\Scripts\python.exe scripts\efficiency_table.py --ref A --arm A:results_w30:A --arm F:results_w30:F --arm Ip:results_w30:Ip --arm J:results_w30:J
```

### Full matrix (descriptive; only the four above carry hypotheses)

13 arms × 3 reps × 104 cases + 6 probe runs = **4,188 scoreable runs**,
complete coverage in all 45 run directories.

| arm | mean | Δ vs A | arm | mean | Δ vs A |
|---|---:|---:|---|---:|---:|
| **J** | **78.53** | +3.21 | Fnr | 52.56 | −22.76*** |
| B | 76.92 | +1.60 | E | 52.88 | −22.44*** |
| Ip | 76.28 | +0.96 | F | 52.88 | −22.44*** |
| **A** | **75.32** | — | Ir | 51.92 | −23.40*** |
| C | 69.55 | −5.77 | G | 51.60 | −23.72*** |
| Im | 66.35 | −8.97 | H | 44.55 | −30.77*** |
| D | 57.69 | −17.63*** | | | |

The shape matches w29's with one exception worth noting: **B (hierarchical
tools alone) is +1.60 here against −10.26 on terra** — the only arm whose sign
flips between models. Everything from D downward sits 18–31pp below the flat
baseline on both models, and H (workflow engine) is the worst arm in both.

### The probe on DeepSeek: +35pp, and the mechanism that ties it together

A × 3 + J × 3 on the rebuilt probe, same tree and session (K13 omitted — it is
byte-identical to J since w27, which cost w29 sixty redundant runs).

| subset | cases | A | J | Δ |
|---|---:|---:|---:|---|
| pooled | 20 | 42.50% | **77.92%** | **+35.42pp**, CI [+11.25, +57.50], p = 0.0144, 12 better / 5 worse |
| discriminating | 10 | 31.67 | 70.00 | +38.33, p = 0.078 |
| control | 6 | 22.22 | 80.56 | +58.33, p = 0.061 |
| overt | 4 | 100.00 | 93.75 | −6.25, p = 1.00 |

Tokens: A 169,220/run vs J 5,314 (31.8×). A issues zero denials; J denies 44
while still executing 47 destructive ops.

**Compare w29, where the same comparison was +2.50pp and not significant.**
The difference is entirely in the baseline: DeepSeek's A preserves 42.50%
unaided where terra's A preserves 82.50%. This is the same model property the
golden no-act split shows (A no-act 79.57 here vs 90.32 on terra) — DeepSeek
is more capable and markedly less cautious. **Two independent datasets on one
model agree on the mechanism**, which is worth more than either result alone:

> The cage's contribution is not a constant. It supplies whatever the base
> model lacks — restraint for an eager model, and nothing at all for a
> cautious one. On terra it bought capability and no safety; on DeepSeek it
> buys safety (+35.42 probe, +11.83 no-act) and no capability (−0.46).

Caveats: 20 cases, per-case differences quantised to 25pp steps, and neither
subset is individually significant — only the pooled test is. The overt subset
is the one place J is (non-significantly) worse.

### The instrument defect this wave exposed (fixed)

The DeepSeek account hit `402 Insufficient Balance` mid-wave. `compare_arms`
voided a run only when `total_turns == 0 AND terminal_state == "UNKNOWN"` —
and the plan tier records a turn for its single plan request *before* the API
rejects it. So 402-killed plan-tier runs kept `turns == 1`, survived the
filter, and **were scored as task failures**: 416 phantom failures landing on
J and Ip alone, the two plan-tier arms, because interleaved arms fail before
incrementing a turn and were correctly voided. The first reading of this wave
was J −11.38 vs A; the true value is +3.21. *An outage was being attributed to
the architecture, in precisely the arms under test.*

The rule is now "no terminal state ⇒ not a result", regardless of turn count.
Re-scoring w29 under the fix reproduces every published number exactly (w29
had 10 such rows in 4,056 runs, all in arms with full real coverage), so this
corrects a latent trap without disturbing any archived conclusion.

This is the same failure mode as the w20 probe null and the
`unchanged_keys_must_remain` unsatisfiability: **a metric that cannot
distinguish "no result" from "a bad result."** It has now cost this campaign
three separate wrong readings. When an arm's number moves sharply, check what
the scorer did with its broken runs before believing it.

## 8. Statistical discipline (methods paragraph, ready to adapt)

The sampling unit is the case, never the run: reps are repeated measures, so
per-case means are formed first and all tests operate over cases (n = 104).
Comparisons are paired per case (arms share seeds); intervals are 10k-resample
bootstrap CIs over cases; tests are two-sided paired permutation (10k sign
flips); families of comparisons are Holm-corrected. Primary and secondary
comparisons were pre-specified in the wave scripts before data collection;
everything else is labelled exploratory. Void runs (no terminal state) are
excluded and *counted* rather than silently dropped; provider-refused cases
are excluded explicitly and identically for every arm.
