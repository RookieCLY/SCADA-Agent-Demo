# Metrics

Deterministic evaluation metrics for SCADA Agent traces against the Golden Dataset. No LLM required — all scoring is rule-based.

## Entry Point

```bash
python -m eval.metrics --traces traces.jsonl --output metrics.jsonl --summary-output summary.json
```

## Core Function

`evaluate_trace(trace, golden, registry)` → one metrics row per trace.

## Metric Groups

### 1. Final State Match
Compares the actual world diff (aggregated from all tool calls) against `expected_final_state_diff`.

- `final_state_match` (bool) — whether all expected changes are present
- `match_mode` — `strict` (no extras allowed) or `subset` (extras OK)
- `final_state_report` — detailed breakdown: `missing`, `wrong_value`, `unexpected`

### 2. Tool Selection
Scores whether the agent used the correct logical tools (domain + action pairs).

- `tool_selection_precision` — fraction of actual tools that were expected
- `tool_selection_recall` — fraction of expected tools that were used
- `tool_selection_f1` — harmonic mean of precision and recall
- `hallucinated_tool_rate` — fraction of calls referencing nonexistent tools
- `domain_match_accuracy` — correct domain selection rate
- `action_match_accuracy` — correct action selection rate (given correct domain)
- `order_correctness` — 1.0 - normalized edit distance between expected and actual action sequences

### 3. Error Metrics
Tracks error codes across tool calls.

- `expected_error_code_match` — whether the expected error code appeared (or none appeared for success cases)
- `schema_error_rate` — fraction of calls with SCHEMA_ERROR
- `reference_error_rate` — fraction with *_NOT_FOUND errors
- `type_mismatch_rate` — fraction with TYPE_MISMATCH or ALREADY_BOUND

### 4. Trajectory Metrics
Validates path constraints from `expected_trajectory`.

- `trajectory_match` — composite: terminal state + required tools/actions + no forbidden tools + step bounds
- `terminal_state_match` — reached expected terminal state
- `required_tools_match` / `required_actions_match` — coverage of required tools/actions
- `forbidden_tools_violated` — whether forbidden tools were used
- `step_efficiency` — ideal steps / actual steps
- `loop_stuck` — early termination or UNKNOWN state

### 5. Parameter Metrics
Validates tool arguments against golden case.

- `parameter_validity` — fraction of calls with valid schema
- `parameter_match` — fraction of expected key-value pairs present in actual diff
- `schema_violation_rate` — fraction with schema errors
- `final_state_missing_count` / `wrong_value_count` / `unexpected_count` — detailed diff counts

### 6. Cascade Failure Detection
Counts cases where a tool fails because a previous tool failed (e.g., binding to a point that wasn't created).

- `cascade_failure_count` — number of cascading reference errors
- `cascade_failure_rate` — fraction of total tool calls

### 7. Resource Query Before Action
Checks whether the agent read resources before writing.

- `resource_query_before_action` — `true` if a resource read occurred before the first write, `false` if not, `null` if no writes

### 8. Task Success (Deterministic)
Binary pass/fail based on behavior type:

| expected_behavior | Criteria |
|---|---|
| `success` | final_state_match AND expected_error_code_match AND trajectory_match |
| `reject` / `ask_for_clarification` | final_state_match AND no_world_mutation AND trajectory_match |
| `fail_or_clarify` | final_state_match AND (expected_error_code_match OR no_world_mutation) AND trajectory_match |

### 9. Cost and Latency
- `input_tokens` / `output_tokens` — token counts
- `cost_usd` — estimated cost
- `e2e_latency_ms` — end-to-end latency
- `latency_per_turn_ms` — latency divided by turn count

## Output Fields

Each metrics row includes:
- Trace metadata: `trace_id`, `config_name`, `model`, `golden_id`, `rep`, `seed`
- All metric scores listed above
- Debug info: `actual_logical_tools`, `expected_logical_tools`, `actual_world_diff`
- Judge placeholders: `judge_completion`, `judge_tool`, `judge_param`, `judge_efficiency` (all `null` until LLM-as-Judge is implemented)

## Aggregation

`aggregate_summary(rows)` computes means and rates across all rows for dashboards and reporting.
