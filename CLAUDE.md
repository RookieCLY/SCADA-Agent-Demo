# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## What this is

Reference implementation for the paper *"Caging the LLM — Constraint architecture
and functional-safety boundary of an industrial SCADA agent"*. It is a pure-Python,
single-machine demo where **four architecture layers can be toggled independently**
for ablation studies:

1. **Hierarchical tools** — expose Domain Tools with a discriminated `action` union instead of every atomic tool flat.
2. **Tool RAG** — soft-rank tool candidates by query similarity (BM25 + dense + optional rerank).
3. **Workflow engine** — route a query to a YAML workflow whose per-step `allowed_tools` act as a hard filter.
4. **State machine** — an 8-stage FSM whose per-state whitelist is the primary hard filter.

Configs `A`–`F` in `configs/` flip these levers in documented combinations (A = flat baseline, F = all four on). The paper's hypotheses H1–H6 compare these configs.

Two further levers exist outside the A–F matrix, added to close gaps between the paper and the runtime. Both default **off** so the archived A–F results stay reproducible:

5. **Runtime safety policy** (`safety.enabled`, `agent/policy.py`) — the §4.7 "outer cage". A declarative rule table evaluated *before dispatch*, so a denied call never reaches a handler and cannot mutate the world. This is what makes the high-risk rules a boundary rather than a prompt request: `deploy_project(force=true)` bypasses validation at the handler level, so `DEFAULT_SYSTEM_PROMPT` alone never actually stopped it. Denials surface as `POLICY_DENIED`, distinct from `OUT_OF_SCOPE` (inner cage) and `BUSINESS_RULE` (handler rule). `configs/G_safety_runtime.yaml` = F + this, giving the prompt-only vs runtime-enforced arms.
6. **Workflow engine mode** (`architecture.workflow.mode: filter | engine`) — `filter` is the legacy behaviour where the workflow only intersects per-step `allowed_tools` while the LLM drives sequencing. `engine` implements §4.3.1: the engine owns control flow, the LLM's `next_state` is ignored while a workflow is live, and the prompt is scoped to one step's local task. `architecture.workflow.rollback_on_failure` adds §4.3.4 Saga compensation (restore the world to the workflow-entry checkpoint on failure). `configs/H_workflow_engine.yaml` = F + both.

7. **Agent-loop levers + arbitration** (this branch) — three structures orthogonal to levers 1–4 (those gate the tool *surface*; these change how the loop is conducted), plus a per-task arbitration when several are on:
   - **ReAct** (`architecture.react`, `agent/react.py`) — bounded Thought→Action→Observation scratchpad rendered into the prompt; tool payloads compressed into observations before threading back; error-code-keyed repair hints; identical already-successful actions answered from the scratchpad (dedupe sits *after* the whitelist and §4.7 checks; epoch-invalidated on any world mutation). Applied to every LLM-interleaved path: the fallback loop and each Specialist. Trace block: `react`.
   - **Plan-and-Execute** (`architecture.plan_execute`, `agent/planner.py`) — one `make_plan` call for the whole ordered sequence, deterministic compile (drop hallucinated tools, pre-validate args per Pydantic schema, collapse duplicates, topologically repair order from `intended_entities`/`referenced_entities`), then LLM-free execution with bounded replans. Docode-trial fixes: the planner sees the **whole** catalogue (RAG orders it; detailed schemas for the ranked head via `planner_tool_budget`, name-only per-domain for the rest), the planning prompt carries a **world snapshot** (`include_world_context`, `summarize_world_for_planner`), and a compile drop triggers one informed replan (`replan_on_compile_drop`). Trace block: `plan`.
   - **Multi-Agent** (`architecture.multi_agent`, `agent/multi_agent.py`) — deterministic Supervisor routes per-state Specialists from the existing RAG ranking (no extra LLM call); private bounded conversations; Blackboard forwards entity IDs read from `world_diff`; deterministic Critic re-runs an idle Specialist once. Trace block: `crew`.
   - **Arbitration** (`Agent.run`): plan first (cheapest); escalate to the crew when the compiled plan spans ≥ `multi_agent.min_domains` registry domains (`domain_gate`) or when plan execution fails with the replan budget spent (Blackboard seeded with the partial work); fall back to the ReAct interleaved loop when the planner abstains. `POLICY_DENIED` is final in every tier — never replanned or escalated around. Which tier ran lands in the trace under `loop` (`path` + `trigger`). Configs: `J_combined.yaml` = F + all three + arbitration; `I_react` / `I_plan_execute` / `I_multi_agent` are the single-lever ablation arms.

8. **Plan-tier closed-loop levers** (`architecture.plan_execute`). Each targeted one measured class of the residual gap between the flat baseline A and the shipping J — A wins 25 of 212 runs, J wins 18, and the 25 split by *mechanism*, not by difficulty. **Measured verdict (`results_w10`/`results_w11`, 106 × 3): only the first is on.** `clarify_on_underspecified` is promoted into `J_combined.yaml`; the cascade guard and the verify round are **off on evidence** and their configs survive only as recorded negatives. Note the planner-prompt changes below are *unconditional*, so no lever setting reproduces the archived A–J prompt:
   - **`clarify_on_underspecified`** — gives the planner a `clarify` channel separate from the safety `refusal`. One channel cannot carry both "I will not do this" and "I cannot tell what you want yet", and conflating them failed in both directions: the planner fabricated an identity for contentless requests (`create_page(id="main_page")` for the bare "帮忙建个页面"), and pushed a legitimate request down the safety channel for a missing field. They do not even share a terminal state — a refusal ends on `DONE`, a clarification on `ASK_USER`, and the golden `success` cases exclude `ASK_USER`, so an over-eager clarification scores as the failure it is. The discriminator in the prompt is deliberately narrow (**is there anything to refer to at all**): a request that supplies a name gets an ID derived from it, and vague wording like "过高" is explicitly *not* grounds to ask — the wider rule over-fired on measurement.
   - **`replan_may_create_referenced: false`** — forbids a replan from *manufacturing* the entity the failed step merely *referenced*. This is the cascade the architecture exists to prevent, re-entered through the recovery path: `query_history` correctly failed `POINT_NOT_FOUND`, and the replan called `create_point` to make the query succeed, on a case that forbids `create_point`. Scoped so ordinary recovery still works — only an identity that *no* step of the approved plan asked for and that exists in *no* collection is protected, matched on the bare id because the cascade crosses collections (`histories.X` referenced, `points.X` created). Surfaces as `dropped_cascade_recovery` + `replan_cascade_blocked`, and like `POLICY_DENIED` it is never escalated around.
   - **`verify_rounds: N`** — bounded Plan→Execute→**Verify**→Patch. Reads the world back and asks what the request still lacks, compiling any patch through the same compiler and cages. Gated to the one situation it can help and cannot harm: the plan ran to completion and mutated something, so a refusal, a clarification, a policy denial and a blocked cascade have all returned before it can fire. The state it is shown names the collections that do **not** hold each touched identity — a touched-entity listing alone can only show what exists, and the dominant failure is something absent. Destructive patch steps are dropped by the runtime rather than trusted to the prompt. Diagnostics: `verify_rounds` / `verify_patched` / `verify_clean` / `dropped_verify_destructive`.

   Also unconditional on this branch, and these are what actually moved the number:
   - The planning catalogue renders the **value set of enumerated arguments** even when the argument is optional (`_has_closed_values`). A name-only enum is a silent `wrong_value` rather than a compile drop — the step validates, executes, and lands the wrong state, which is how "开启变化存储历史" was planned as `storage_mode="periodic"`, the schema default, in every rep of two cases. Sets render **whole**: truncating a closed set presents a partial list as exhaustive, and a 4-value cut on `create_device.device_type` hid `valve`.
   - **One identifier across dependent steps** (`PLANNER_SYSTEM_PROMPT` rules 5–6). An explicit new name in the request beats an existing id from the world snapshot. golden-068 was planning `validate_project(deployment_id="default")` then `deploy_project(deployment_id="deploy_staging")` — different deployments, so §4.7 correctly refused the deploy as unvalidated.
   - **Do not manufacture the premise** in the *initial* plan (clarify rule 7). "给不存在的NO_SUCH_TEMP配置高温报警" states the point does not exist; the plan was creating it and attaching the alarm. The runtime guard only ever saw the replan path.

   Arms: `K1_clarify` / `K2_cascade` / `K3_verify` stack the levers onto `J_combined` in that order; `K4_targeted` = `K1` + the two prompt fixes above, and was the shipping arm until K5 superseded it. **The current shipping arm is `K7_residue`** — see below; every other `K*` config is a historical record whose code state the tree no longer has, and each now carries a banner saying so.

   `K5_formats` = `K4` + three more unconditional fixes, all aimed at the one bucket that dominates the residue — **"acted, final state mismatch" with no error anywhere in the trace, 11 of the 19 runs A still wins**. Config body is byte-identical to `K4`, so `K4` is the matched control.
   - **Hex-colour coercion** (`_normalize_documented_formats`). A field documented "Hex color" is typed `str`, so `"white"` validates, executes and lands verbatim: golden-007 stored `"white"` against an expected `"#FFFFFF"`. This runs *before* validation, not in the repair chain, because the defect passes validation. Which fields qualify is read off the schema — a hex default, or "hex" in the description and not "named" — so `set_trend_pen_color` ("Hex or named color") is deliberately left alone.
   - **Packed-pair split** (`_split_pair_field`). `create_page` takes `resolution: [w, h]`; `set_page_resolution` takes `width` and `height`. The planner carried the first spelling to the second tool and the step was dropped `schema_invalid`, so golden-013's "把报表页大小设成4K" vanished with the page left at the default.
   - **`set_point_archive` de-shadowed.** It writes nothing (`Point` has no `archive` field) yet described itself as archiving "to history" and carried examples restating `enable_history`'s job, so Tool RAG ranked it *above* the real tool: golden-093 got a call that succeeded, wrote nothing, and left `histories.ENERGY_KWH` absent. Its `intended_entities` claimed an entity that can never exist and now returns `[]`.

   Note `replan_cascade_blocked` is deliberately **absent** from the crew-escalation set (`{plan_step_failed, replan_empty}`): a plan stopped because recovery required manufacturing the premise should not be handed to a tier that would manufacture it, exactly as `POLICY_DENIED` is never escalated around.

   `K6_dropfeedback` = `K5` + naming the offending *fields* in the compile-drop replan instead of only the tool. **Measured and not adopted** (`results_w13`): 72.3% vs K5's 73.6%, net −4 run-by-run (7 fixed, 11 broke), and the per-rep ranges overlap (K5 72.6–74.5, K6 71.7–73.6), so there is no measured difference either way. It does one real thing — golden-093 gained the missing `enable_history` in 3 of 3 reps against K5's 0 of 3 — but the ledger is negative and K5 is the simpler prompt. The field-level detail is still computed and traced as `schema_invalid_detail`; it simply never goes back to the model.

   `K7_residue` = `K5` + three more fixes, and is **what `J_combined` now ships**. Two are defect repairs, correct regardless of score: `set_alarm_priority` returned `ok()` while writing nothing though `Alarm.priority` exists and its ten sibling tools all write (9 successful calls / 0 `world_diff`s in Phase 4, against 8 golden cases asserting `alarms.*.priority`); and `max_steps: 12` was truncating correct plans in 14 runs (golden-026 proposed 26 steps, golden-069 proposed 28), raised to 24, truncations 14 → 3. The third states the conventional binding vocabulary in `bind_point.property`'s description.

   `K8_defects` is that arm minus the vocabulary, and exists because the vocabulary was **wrongly reverted once**. At 3 reps its property-word frequencies barely moved (`value` still dominant, the model even inventing `click`), which looked like proof of no effect. Six reps and a *paired* comparison say otherwise: **+1.89pp, K7 ahead in 5 of 6 paired reps and behind in none** (sign test p ≈ 0.03), paired run-by-run +12. The mechanism is on golden-088, which K7 wins 5 of 6: both bindings want `value`, and without the text the second drifts to `tag`. **Aggregate word frequency is the wrong instrument when a handful of binds decide a case** — pair on `(case, rep)` and count run-by-run instead. The vocabulary coincides with the golden keys' and is disclosed as such in `tools/manage_pages.py`; it was measured to help the flat baseline (3.8% of runs) slightly more than the plan tier (3.5%).

   **Final standing, all arms on one tree, 106 × 3** (`results_w14`; K7/K8 × 6 in `results_w15`/`w16`): J/K7 **74.1%** · B 68.9 · A **65.7** · C 59.4 · D 56.3 · Fnr 52.5 · E 50.6 · **F 49.1**. J runs at 5,465 input tokens against A's 223,946 — **41×** — with step_efficiency 0.903 vs 0.641 and a run-by-run record of +26 against A. Two results worth stating plainly: the four architecture levers *without* a plan loop are the **worst** arm (F loses to A 84 runs to 31), and J's whole margin over A is on the no-act half — capability is a tie (35% vs 36% failure) while no-act failures are 11% against A's 31%. The cage does not make the model more capable; it stops it doing what it should not.

   **Read every single-case claim above with this caveat.** golden-063 was measured at **2/5 `TYPE_MISMATCH` vs 3/5 `OK` across five runs of the same config, the same seed and the same tree** — LongCat-2.0 is not deterministic at `temperature: 0.0`. A "failed in all 3 reps of X, passed in all 3 of Y" story is producible by chance at that flip rate, so only the run-by-run aggregates over all 318 runs and the per-rep ranges are evidence. An arm's per-rep spread is a floor on the noise, not the whole of it.

   **Significance, from `scripts/compare_arms.py`.** Every number above is a point estimate; these are the tested ones. The tool aggregates reps to a per-case mean first — reps of a case are repeated measures, so treating 318 runs as n=318 overstates n threefold — then pairs per case, bootstraps a CI over cases, and runs a two-sided paired permutation test, Holm-corrected across the arms compared to the reference.

   | arm vs A | Δ | 95% CI | Holm p |
   |---|---:|---|---:|
   | K7 (shipping J) | +8.33 | [+1.73, +15.41] | 0.097 |
   | B | +3.14 | [−0.31, +6.92] | 0.221 |
   | E | −15.09 | [−24.84, −5.97] | **0.015** |
   | F | −16.67 | [−26.73, −6.60] | **0.015** |
   | G | −18.24 | [−28.30, −7.86] | **0.011** |
   | H | −20.13 | [−30.19, −9.75] | **0.004** |

   Three things follow, and two of them correct claims made earlier in this file:

   - **The robust results are the negative ones.** E, F, G and H are all significantly *worse* than the flat baseline. The four architecture levers without a plan loop measurably hurt; they only pay off once something else owns control flow.
   - **K7-vs-A depends on framing.** p = 0.019 as a pre-specified primary comparison, 0.097 after Holm across ten arms. State which is meant. Split by subset it is unambiguous: **no-act cases +20.20pp, Holm p = 0.0079**, capability cases +2.97pp, **p = 0.48** — the margin is entirely safety, and capability is a tie.
   - **"B beats A" is not supportable** (CI spans zero), and the K7-vs-K8 vocabulary result is **p = 0.060**, not the ≈0.03 an earlier hand-run sign test suggested. Both were overstated before the tests existed.

   **§4.7 has never fired.** `POLICY_DENIED` = 0 across ~2,700 runs spanning A, B, F, F_noresources, J, K7, K9 and G. In A–F `safety.enabled` is 0, so the matrix never included the cage at all; where it is enabled it counts destructive operations but denies none — `_deny_bulk_destructive` needs 3+ destructive ops in one run and nothing approaches that, while the forced/unvalidated-deploy rules never fire because the planner's refusal channel stops those upstream. The null check confirms it: **`G_safety_runtime` (= F + the cage) is statistically indistinguishable from F** (−1.57pp, p = 0.27). So the safety margin above is produced by the FSM whitelist and the planner's refusal channel, **not** by the §4.7 runtime policy, which is at present an unexercised backstop. `eval/golden_safety_probe.jsonl` exists to exercise it deliberately; on the first 10-case version the cage did fire and did prevent a world mutation, but at n=10 the outcome difference was p = 1.0.

Related: `state_machine.oos_guidance` / `oos_repeat_limit` control out-of-scope feedback. A blocked call now names the state that exposes the tool and how to reach it, and identical blocked calls are capped before diverting to `ASK_USER` — the H3 result (out-of-scope rate *rising* D→E) came from bare rejections that models simply retried. Set `oos_guidance: false, oos_repeat_limit: 0` to reproduce the old behaviour.

## Environment & commands

Python 3.11/3.12 only. The virtualenv lives at `venv/` (some docs/scripts also reference `.venv/`). Shell is PowerShell — use `;` not `&&` to chain.

```powershell
# install (Phase-1 needs only pydantic/pyyaml/loguru/rank-bm25/numpy/openai; use [full] for RAG/LLM/analysis extras)
pip install -e .[dev]

# run the whole suite (see LLM note below)
venv\Scripts\python.exe -m pytest -q

# force all-mock run (fast, ~1s, no network)
$env:TEST_LLM_PROVIDER="mock"; venv\Scripts\python.exe -m pytest tests\ -q

# single test file / single test
venv\Scripts\python.exe -m pytest tests\test_workflow.py -q
venv\Scripts\python.exe -m pytest tests\test_e2e.py::test_e2e_create_alarm -q

# coverage across the runtime packages
venv\Scripts\python.exe -m pytest --cov=agent --cov=tools --cov=world --cov=resources --cov=workflows -q

# lint / type-check
venv\Scripts\python.exe -m ruff check .
venv\Scripts\python.exe -m mypy agent tools world resources
```

### Running the agent

```powershell
# assemble-only sanity check (no LLM call)
venv\Scripts\python.exe -m agent.orchestrator --config configs\F_full_four_in_one.yaml --dry-run

# single end-to-end query on a pre-seeded demo world → results/<config>/<model>/<run_id>/traces.jsonl
venv\Scripts\python.exe -m agent.orchestrator --config configs\D_minimal.yaml --query "给反应釜1加个高温报警,超过80度告警"
```

### Running experiments (golden dataset)

```powershell
# a few cases / a sample / everything (uv or venv python both work)
uv run python -m eval.runner --config configs\F_full_four_in_one.yaml --golden-ids golden-001,golden-002 --reps 1
uv run python -m eval.runner --config configs\F_full_four_in_one.yaml --dataset-sample 5 --reps 1
uv run python -m eval.runner --config configs\F_full_four_in_one.yaml --all

# cheap all-mock smoke of a single case
uv run python -m eval.runner --config configs\F_full_four_in_one.yaml --golden-ids golden-001 --provider mock --model mock --reps 1 --max-reruns 0
```

The runner writes one directory per `results/{config_name}/{model}/{run_id}/`, supports `--resume`, and reruns technical failures into `_failures.jsonl`. It deliberately does **not** run the LLM judge — judging (`eval/judges.py`, `scripts/run_llm_judge.py`) and aggregation (`scripts/aggregate.py`, `scripts/analyze.py`, `scripts/make_report.py`) happen offline afterward.

### LLM providers & the test-suite quirk

`tests/conftest.py` installs an **autouse fixture** that monkeypatches `agent.llm.build_llm`:
- If a real key (e.g. `XIAOMI-MIMO_API_KEY` in `.env`) exists, `provider="mock"` configs are **upgraded** to the real client — so `pytest tests/ -q` doubles as a real-LLM regression (~90s).
- Missing key or `TEST_LLM_PROVIDER=mock` → everything **downgrades** to `MockLLM` (~1s).
- Tests decorated `@pytest.mark.mock_only` (those asserting scripted regex output or `isinstance(..., MockLLM)`) always keep the mock.

When editing E2E/config tests, respect this: if a test depends on deterministic scripted tool sequences, it must be `mock_only`. `MockLLM` (`agent/llm.py`) is a regex-keyed scripted backend covering a handful of seed queries; real providers go through `OpenAICompatibleLLM`. Copy `.env.example` → `.env` for real-LLM runs.

## Architecture (the big picture)

The core is a single turn-based loop in `agent/orchestrator.py` (`Agent.run`). Everything else is a filter or an optional layer plugged into that loop behind `ArchitectureConfig` flags (`agent/config.py`). The Phase-1 while-loop is the execution kernel; Phase-2 layers are additive and individually switchable, and the trace wire format stays backward-compatible.

**Tool-visibility pipeline (the heart of the experiment)** — each turn computes what the LLM may see and call:
1. `_allowed_atomics` — **hard filter**: intersection of the state machine's per-state whitelist (`STATES[state].allowed_tools`) and the active workflow step's `allowed_tools`.
2. `_rank_with_rag` — **soft rank**: if Tool RAG is on, score the allowed atomics against the query and keep top-k.
3. `_visible_tools_for` — in hierarchical mode, project the surviving atomics up to their parent Domain Tools to shrink the prompt.

A tool call is only dispatched if it survives these filters; otherwise it is logged as `OUT_OF_SCOPE` and fed back to the model. Order matters: hard filters (safety) always precede RAG (relevance).

**Key modules:**
- `agent/tool_registry.py` — single source of truth for all tools. Holds flat (per-atomic) **and** hierarchical (per-domain discriminated-union) views, plus a **reverse table** atomic→`(domain, action)` used by metrics. `build_default_registry(tool_count=N)` starts from ~7 core + 10 extra domains and **synthesizes dynamic filler tools** up to `tool_count` (used by the H1 tool-count sweep). With no argument it registers **500 real atomics across 17 domains and synthesizes nothing** — the library outgrew the "~300" this line used to claim, and `tool_count=300` now truncates *down* rather than padding up. `selfcheck()` fails fast on any unmapped atomic.

  Two caveats before trusting the runtime's own instrumentation:
  - **The plan tier records no LLM completions.** `_request_plan` calls `ctx.log_llm(..., text=None)` unconditionally, so `trace.record_llm_io` governs the interleaved loop only. A plan-tier run's `llm_calls` carry tokens and latency but no text, so a step dropped `schema_invalid` cannot be diagnosed from the archive — the arguments that failed were never written down.
  - **Two tools whose `run` never writes.** `create_device` and `set_point_archive` validate, return `ok`, and leave the world untouched, on collections (`devices`, `points`) that do exist. No golden case depends on either, so nothing measured is affected, but a silent no-op is the failure shape hardest to see in a trace.
- `agent/dispatcher.py` — `dispatch_atomic` (flat) / `dispatch_domain` (hierarchical, unwraps `action`). L1 schema validation happens here; a Pydantic `ValidationError` becomes a `SCHEMA_ERROR` `ToolResult` rather than an exception.
- `agent/state_machine.py` — 8 functional stages (`ANALYZE_INTENT`, `CONFIG_ALARM`, `DEPLOY`, `VALIDATE`, …, `DONE`). The FSM enforces legal transitions and owns per-state tool whitelists. The LLM requests transitions by emitting `next_state: <STATE>` in plain text.
- `agent/tool_rag.py` — `ToolIndex` + `select_tools` (hybrid dense/BM25 with optional reranker). Built from the registry when RAG is enabled.
- `agent/workflow.py` + `workflows/*.yaml` — `WorkflowCatalogue.select(query)` picks a workflow; `WorkflowEngine` tracks step state, `fast_forward_for_atomic` skips optional steps, and deterministic (non-LLM) steps run registered Python handlers (`workflows/handlers.py`, registered at import time).
- `resources/` — read-only views over the world exposed as a synthetic `read_resource(uri)` pseudo-tool. It bypasses Tool RAG and is invisible to the tool catalogue (the "Resources/Tools separation" lever).
- `agent/tracer.py` — writes one `traces.jsonl` per run; `record_llm_io` controls whether prompts/completions are captured. Trace records feed `eval/metrics.py` and `eval/judges.py`.

**The Mock World & tools (`world/`, `tools/`):**
- `world/` — Pydantic models + in-memory backend. `MockWorld.hash()` gives a deterministic fingerprint (used to assert idempotency and detect mutations); `deep_copy_world` snapshots before/after each tool call.
- Every tool subclasses `MockTool` (`tools/_base.py`) and runs a 4-layer validation pipeline: L1 schema (Pydantic) → L2 `*_NOT_FOUND` (world lookups) → L3 business rules (`TYPE_MISMATCH`, `ALREADY_BOUND`, …) → L4 write world + emit `world_diff`. Error codes live in `ErrorCode`.
- **Every concrete tool MUST implement `intended_entities` and `referenced_entities` as `@staticmethod`** — enforced at class-definition time via `__init_subclass__`. These power the cascade-failure detector (a call's `referenced_entities` failing with a `*_NOT_FOUND` code is traced back to the earlier call whose `intended_entities` should have created it). Do not drop or stub these.

**Safety model:** `DEFAULT_SYSTEM_PROMPT` in `agent/orchestrator.py` carries the high-危 operation refusal rules (e.g. `deploy_project force=true`, deploy without `validate_project`, bulk/irreversible deletes). These are declared higher-priority than user "just do it / skip validation" phrasing — preserve that ordering when editing the prompt.

## Config & experiment conventions

- Configs are `ExperimentConfig` YAML (`configs/*.yaml`). `A_flat_baseline` … `F_full_four_in_one` are the ablation matrix; `*_smoke.yaml` are provider smoke tests; `sweep_tool_count.yaml` drives the H1 sweep via `tool_count`.
- `agent/orchestrator.py::assemble()` reads a config, builds the registry, LLM, tracer, and conditionally the tool index / workflow catalogue / resource registry from the arch flags. This is the canonical entry to construct an `Agent`.
- `agent_old/` is a superseded copy of the agent package — do not edit it; work in `agent/`.
- Golden dataset: `eval/golden_dataset.jsonl` (schema in `eval/schema.py`). Splitting/expansion scripts live in `scripts/` (`split_golden_dataset.py`, `expand_golden_dataset.py`, `generate_30_golden.py`).
