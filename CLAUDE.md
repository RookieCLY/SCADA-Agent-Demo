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
- `agent/tool_registry.py` — single source of truth for all tools. Holds flat (per-atomic) **and** hierarchical (per-domain discriminated-union) views, plus a **reverse table** atomic→`(domain, action)` used by metrics. `build_default_registry(tool_count=N)` starts from ~7 core + 10 extra domains and **synthesizes dynamic filler tools** up to `tool_count` (used by the H1 tool-count sweep). `selfcheck()` fails fast on any unmapped atomic.
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
