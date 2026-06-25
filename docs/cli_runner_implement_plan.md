# Interactive CLI Runner Implementation Plan

## 1. Objective

Implement an interactive CLI runner for the SCADA Agent Demo that lets a developer or evaluator run experiments manually without editing Python scripts each time.

The runner should support:

1. Loading an existing Golden Dataset test case.
2. Creating an ad-hoc test case interactively:
   - Build or edit the initial `MockWorld`.
   - Submit one or more natural-language queries against that world.
   - Observe how the world changes after each run.
3. Loading or switching experiment config files.
4. Choosing the LLM provider/model.
5. Toggling whether LLM thought/reasoning and assistant output are shown.
6. Showing world information in real time while the runner executes.

This plan is for implementation only; it does not implement the runner yet.

---

## 2. Proposed Entry Point

Add a new CLI module:

- `eval/interactive_runner.py`

Run with:

```powershell
.\.venv\Scripts\python.exe -m eval.interactive_runner
```

Optional startup flags:

```powershell
.\.venv\Scripts\python.exe -m eval.interactive_runner --config configs\F_full_four_in_one.yaml --dataset eval\golden_dataset.jsonl --provider mock --model mock
```

Rationale:

- It belongs under `eval/` because it is primarily an evaluation/dev runner, adjacent to `eval.runner`, `eval.metrics`, and `eval.judges`.
- It should reuse `agent.orchestrator.Agent`, `agent.orchestrator.assemble`, `eval.schema.load_golden_dataset`, and `world.MockWorld` rather than creating a separate execution path.

---

## 3. User Interaction Model

Use a simple REPL menu first, not a curses/TUI dependency.

Example top-level menu:

```text
SCADA Interactive Runner

Current config : configs/F_full_four_in_one.yaml
Current model  : mock/mock
Current world  : 3 points, 1 page, 0 alarms, 0 histories, 0 scripts, 0 deployments
Show LLM IO    : off
Show reasoning : off

Commands:
  golden     Load a golden test case
  world      Create/edit/reset initial world
  query      Run a query against current world
  config     Load/switch config
  llm        Choose provider/model
  display    Toggle LLM thought/output/world display
  inspect    Show current world
  trace      Show last trace summary
  save       Save current ad-hoc case as JSON
  help       Show commands
  exit       Quit
> 
```

Keep command aliases short:

- `g` → `golden`
- `w` → `world`
- `q` → `query`
- `c` → `config`
- `m` → `llm`
- `d` → `display`
- `i` → `inspect`
- `t` → `trace`

---

## 4. Core Runtime State

Create a small session state object inside `eval/interactive_runner.py`.

Fields:

- `config_path: Path | None`
- `config: ExperimentConfig | None`
- `provider_override: str | None`
- `model_override: str | None`
- `agent: Agent | None`
- `dataset_path: Path`
- `golden_records: list[GoldenRecord]`
- `current_golden: GoldenRecord | None`
- `world: MockWorld`
- `initial_world_snapshot: dict | None`
- `last_trace: dict | None`
- `last_query: str | None`
- `show_llm_output: bool`
- `show_llm_reasoning: bool`
- `show_world_realtime: bool`
- `results_root: Path`
- `run_id: str`

Important behavior:

- Rebuild the `Agent` whenever config/provider/model changes.
- Preserve the current world when switching config unless the user explicitly resets it.
- For a loaded golden case, initialize `world` from `GoldenRecord.initial_world`.
- For ad-hoc mode, use the current mutable `world` across repeated queries so users can observe incremental changes.

---

## 5. Action 1 — Load a Golden Test Case

Command: `golden`

Flow:

1. Load records from `eval/golden_dataset.jsonl` or the configured dataset path.
2. Let the user choose a case by:
   - Exact ID, e.g. `golden-042`.
   - List/filter by domain.
   - List/filter by complexity.
   - Search query text substring.
3. Show selected case summary:
   - `id`
   - `query`
   - `domain`
   - `complexity`
   - `expected_behavior`
   - `expected_workflow_id`
   - compact `initial_world` summary
   - compact expected diff summary
4. Ask whether to load its `initial_world` into the current session world.
5. Set `current_golden` and `world`.
6. Offer immediate run:
   - `Run this golden query now? [y/N]`

Implementation notes:

- Use `load_golden_dataset()` from `eval.schema`.
- Use `MockWorld.model_validate(record.initial_world or {})` to instantiate the world.
- Do not mutate the `GoldenRecord`; keep ad-hoc edits in session state only.

---

## 6. Action 2 — Interactively Create an Ad-Hoc Test Case

Command group: `world`

### 6.1 World Creation Modes

Support three world creation paths:

1. Empty world:
   - `world reset`
2. Demo world:
   - reuse `build_demo_world()` from `agent.orchestrator`
3. Guided entity builder:
   - add point
   - add page
   - add widget
   - add alarm
   - add device
   - add history config
   - add script
   - add deployment

### 6.2 Guided Entity Builder

Subcommands:

```text
world add point
world add page
world add widget
world add alarm
world add device
world add history
world add script
world add deployment
world remove <dot-path-or-entity>
world load-json <path>
world save-json <path>
world inspect
world reset
world demo
```

For each entity, prompt for the minimum viable fields and sensible defaults.

Examples:

- Point:
  - `tag`
  - `type`: `analog | digital | string`
  - optional `unit`
  - optional `min/max`
- Page:
  - `id`
  - `name`
  - `resolution`, default `(1920, 1080)`
  - `background`, default `#FFFFFF`
- Widget:
  - `page_id`
  - `id`
  - `type`
  - `position`
  - `size`
  - optional `expected_binding_types`
- Alarm:
  - `id`
  - `tag`
  - `type`
  - optional limits/deadband/priority

### 6.3 Query Loop

Command: `query`

Flow:

1. Prompt for a natural-language query.
2. Save a deep copy of the world before the query.
3. Run the current agent against the current world.
4. Show trace summary.
5. Show world diff before/after.
6. Keep the mutated world as the current session world.
7. Allow another query immediately.

Example:

```text
query> 给TEMP_101加个高温报警，超过80度报警

[turn 1] state=ANALYZE_INTENT visible_tools=...
[tool] manage_alarms action=create_analog_alarm result=OK

World changed:
  + alarms.alarm_high_temp_101.tag = TEMP_101
  + alarms.alarm_high_temp_101.high_limit = 80.0

Current world:
  points=6 pages=0 alarms=1 histories=0 scripts=0 deployments=0
```

---

## 7. Action 3 — Load Config

Command: `config`

Flow:

1. Show current config.
2. List `configs/*.yaml`.
3. Let the user enter a path manually or choose by number.
4. Validate with `load_config()`.
5. Rebuild agent with the new config and existing provider/model overrides.
6. Show active architecture flags:
   - hierarchical tools
   - Tool RAG
   - workflow
   - state machine
   - resources separation

Implementation notes:

- Prefer reusing `assemble()` for normal builds.
- If provider/model override is active, pass it to `assemble()`.
- Use a run ID like `interactive_<timestamp>` or `interactive_current`.
- Do not overwrite existing trace outputs unexpectedly; use a unique run ID unless the user explicitly asks to reuse one.

---

## 8. Action 4 — Choose LLM

Command: `llm`

Flow:

1. Show current provider/model.
2. Offer known providers from `ModelConfig.provider`:
   - `mock`
   - `xiaomi-mimo`
   - `anthropic`
   - `openai`
   - `deepseek`
3. For provider `mock`, set model to `mock`.
4. For real providers, prompt for model name with a default from config/env.
5. Rebuild the agent.
6. Surface missing env vars clearly.

Important implementation constraint:

- Currently `agent.llm.build_llm()` only wires `mock` and `xiaomi-mimo`; other providers exist in the config schema but raise `NotImplementedError`.
- The CLI should handle unsupported providers gracefully:
  - show a friendly error
  - keep the previous working agent active
  - suggest choosing `mock` or `xiaomi-mimo`

---

## 9. Action 5 — Toggle LLM Thought and Output Display

Command: `display`

### 9.1 Toggles

Support:

```text
display llm-output on|off
display reasoning on|off
display world on|off
display trace on|off
```

Meaning:

- `llm-output`: show assistant text from each LLM turn.
- `reasoning`: show `LLMResponse.reasoning` / traced reasoning when available.
- `world`: show world summary and diff after each tool call or turn.
- `trace`: show low-level trace/tool-call details.

### 9.2 Trace IO Recording

The current tracer only stores LLM text/reasoning when `TraceConfig.record_llm_io` is enabled.

Implementation options:

1. Lightweight display-only path:
   - Print LLM response text/reasoning inside the interactive runner by adding a callback hook around the agent loop.
   - Requires a small orchestrator extension.
2. Trace-backed path:
   - Toggle `config.trace.record_llm_io` and rebuild the tracer/agent.
   - Simpler, but only displays after the run unless the agent exposes turn callbacks.

Recommended implementation:

- Add optional event callbacks to `Agent.run()` or a new `Agent.run_interactive()` wrapper:
  - `on_llm_response(resp, turn, state)`
  - `on_tool_result(record, world)`
  - `on_resource_read(record)`
  - `on_state_change(old, new)`
- Keep default `Agent.run()` behavior unchanged for tests and batch runner.
- The interactive runner passes callbacks based on display toggles.

---

## 10. Real-Time World Information

The runner should show world information while running, not only after completion.

### 10.1 World Summary

Implement helper:

```text
World summary:
  pages        : N
  widgets      : N total
  points       : N analog / N digital / N string
  alarms       : N enabled / N disabled
  devices      : N
  histories    : N enabled / N disabled
  scripts      : N enabled / N disabled
  deployments  : N by status
```

### 10.2 World Diff

After each tool call, compare previous world snapshot against current world:

- `+` added paths
- `~` modified paths
- `-` removed paths

Use existing `MockWorld.diff()` where possible.

### 10.3 Detail Views

Command examples:

```text
inspect world
inspect points
inspect pages
inspect page p1
inspect alarms
inspect histories
inspect scripts
inspect deployments
inspect path pages.p1.widgets.w1
```

Implement dot-path lookup using `MockWorld.snapshot()`.

---

## 11. Required Orchestrator Extension

The existing `Agent.run()` returns a final trace but does not expose turn-by-turn callbacks to callers.

Add a backward-compatible optional parameter set:

```text
Agent.run(..., event_sink: AgentEventSink | None = None)
```

Event sink methods:

```text
on_run_start(query, world)
on_state_enter(state)
on_llm_response(turn, state, response)
on_resource_read(turn, record)
on_tool_call(turn, record, world_before, world_after)
on_run_finish(trace, world)
```

Implementation requirements:

- Default `event_sink=None` preserves all current behavior.
- Existing tests should continue to pass unchanged.
- The event sink must not mutate the world or trace.
- The interactive runner uses the event sink for real-time display only.

Alternative if avoiding orchestrator changes:

- Run normally, then print the final trace and world diff afterward.
- This does not satisfy the real-time requirement, so it should only be a fallback.

---

## 12. Saving Ad-Hoc Test Cases

Command: `save`

Allow saving current session state as a draft GoldenRecord-like JSON file.

Prompt for:

- `id`
- `query`
- `domain`
- `complexity`
- `expected_behavior`
- optional expected diff generated from before/after snapshots
- optional expected error code
- optional expected workflow ID
- optional rubric hints

Output path options:

- `eval/golden_cases/<id>.json`
- or a user-provided path

Do not automatically append to `eval/golden_dataset.jsonl`; require explicit confirmation because that changes the canonical dataset.

---

## 13. Error Handling

Handle these cases gracefully:

- Invalid config path.
- Config loads but provider is unsupported.
- Missing API key / base URL for real LLM.
- Invalid Golden ID.
- Invalid world JSON.
- Invalid entity fields during guided world creation.
- Agent raises during run.
- Trace write failure.
- User presses Ctrl+C mid-run.

Expected behavior:

- Print a concise error.
- Keep the previous known-good session state when possible.
- Do not lose the current world unless the user requested reset.

---

## 14. Testing Plan

Add tests in a new file:

- `tests/test_interactive_runner.py`

Recommended test seams:

- Keep parsing/command handlers pure enough to unit test without a real terminal.
- Use `MockLLM` for all runner tests.
- Use `tmp_path` for result directories and saved ad-hoc cases.

Test categories:

1. Config loading:
   - loads valid YAML
   - rejects invalid path without losing previous config
2. LLM switching:
   - mock provider works
   - unsupported provider is reported without crash
3. Golden loading:
   - loads by exact ID
   - initializes `MockWorld` from `initial_world`
4. World editing:
   - add point/page/widget through command handler
   - reset to empty
   - load demo world
5. Query execution:
   - runs a mock query
   - updates world after successful tool call
   - records last trace
6. Display toggles:
   - toggles are reflected in session state
   - event sink prints or suppresses LLM text/reasoning as expected
7. Save ad-hoc case:
   - writes valid JSON
   - does not append to canonical dataset unless explicitly requested

---

## 15. Acceptance Checks

The implementation is acceptable only when all checks below pass.

### 15.1 CLI Startup Acceptance

- Running this command starts the interactive prompt without crashing:

```powershell
.\.venv\Scripts\python.exe -m eval.interactive_runner --provider mock --model mock
```

- The startup screen shows:
  - current config or `not loaded`
  - current provider/model
  - current world summary
  - display toggle state
  - help/menu commands

### 15.2 Golden Case Acceptance

- From the prompt, the user can load a known case such as `golden-001`.
- The runner displays the golden query and expected behavior.
- The runner initializes the current world from `GoldenRecord.initial_world`.
- Running the loaded golden query produces a trace and does not crash.
- The final trace is written under the configured interactive results directory.

### 15.3 Ad-Hoc World Acceptance

- The user can start from an empty world.
- The user can add at least:
  - one analog point
  - one page
  - one widget on that page
- `inspect world` shows those entities correctly.
- The user can save the current world to JSON.
- The saved JSON can be loaded back into a new session and validates as `MockWorld`.

### 15.4 Ad-Hoc Query Acceptance

- With mock LLM selected, the user can run a natural-language query against the current world.
- The runner shows:
  - state transitions
  - tool calls
  - tool results/error codes
  - world diff after tool execution
  - final world summary
- The current world remains mutated after a successful query so the user can run a second query against the updated state.

### 15.5 Config Switching Acceptance

- The user can switch to a config from `configs/*.yaml`.
- The runner rebuilds the agent and prints the active architecture flags.
- If config loading fails, the previous config and agent remain active.

### 15.6 LLM Switching Acceptance

- The user can switch to `mock/mock` and run a query successfully.
- If the user chooses a provider not currently wired by `build_llm()`, the runner prints a friendly unsupported-provider message and keeps the previous LLM active.
- If the user chooses a real provider without required env vars, the runner prints the missing variable guidance and keeps the previous LLM active.

### 15.7 Thought/Output Toggle Acceptance

- When `display llm-output on`, assistant text for each LLM turn is printed.
- When `display llm-output off`, assistant text is suppressed except for final summaries.
- When `display reasoning on` and the provider returns reasoning, reasoning is printed.
- When `display reasoning off`, reasoning is not printed.
- The toggle state is visible in the top-level status display.

### 15.8 Real-Time World Display Acceptance

- When `display world on`, the runner prints world summary/diff after each tool call or resource-relevant step.
- When `display world off`, the runner suppresses per-step world output but still allows `inspect world`.
- World display must be generated from the actual `MockWorld` object, not from LLM text.

### 15.9 Regression Acceptance

These existing checks must still pass:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_e2e.py tests\test_phase2_e2e.py tests\test_eval_metrics.py tests\test_eval_judges.py -q
```

Lint touched files:

```powershell
.\.venv\Scripts\python.exe -m ruff check eval\interactive_runner.py tests\test_interactive_runner.py agent\orchestrator.py
```

### 15.10 Manual Smoke Script Acceptance

Document a manual smoke sequence in `eval/RUNNER_USAGE.md` or a new `eval/INTERACTIVE_RUNNER_USAGE.md`:

```text
1. Start runner with mock model.
2. Load demo world.
3. Inspect world.
4. Run a point/alarm query.
5. Observe real-time tool and world diff output.
6. Toggle LLM output off.
7. Run another query.
8. Load golden-001.
9. Run golden query.
10. Save ad-hoc case JSON.
11. Exit cleanly.
```

The smoke sequence passes when every step completes without exceptions and produces trace output.

---

## 16. Suggested Implementation Order

1. Add `eval/interactive_runner.py` with session state, startup args, and top-level REPL.
2. Implement config loading and agent rebuilding.
3. Implement golden loading and world initialization.
4. Implement world summary/inspect helpers.
5. Implement guided world editing for points/pages/widgets first.
6. Implement query execution using current `Agent.run()`.
7. Add orchestrator event-sink callbacks for real-time output.
8. Wire display toggles to event sink.
9. Implement ad-hoc save/load JSON.
10. Add tests.
11. Add usage documentation and manual smoke checklist.
12. Run acceptance checks.

---

## 17. Non-Goals For First Version

Do not include these in the first implementation unless explicitly requested:

- Full-screen TUI/curses interface.
- Concurrent multi-agent runs.
- Editing canonical `eval/golden_dataset.jsonl` by default.
- Running LLM-as-Judge from the interactive prompt.
- Plotting or statistical aggregation.
- Production-grade persistence beyond saving JSON and traces.
