# Golden Dataset Schema

The Golden Dataset Schema is defined and documented in this directory, specifically via Pydantic models in `schema.py`.

## Overview
The golden dataset validates the correctness of the SCADA agent. Each record represents a single evaluation scenario.

### Core Fields:
- `id`: Unique identifier for the golden record (e.g., "golden-042").
- `query`: The user's prompt or instruction.
- `domain`: The target SCADA domain (e.g., "binding", "alarm", "graphics").
- `complexity`: Complexity level: `simple`, `medium`, or `complex`.
- `initial_world`: The initial state of the Mock World (subset of Page/Widget/Point/Alarm/Device).
- `expected_behavior`: Expected outcome of the agent: `success`, `fail_or_clarify`, `ask_for_clarification`, `reject`.
- `expected_final_state_diff`: Expected changes to the world state. Contains `match_mode` (`strict`, `subset`, `key_fields`), `added_or_modified`, `removed`, and `unchanged_keys_must_remain`.
- `expected_trajectory` (optional in the schema; declared by **all 106** shipped cases): Path constraints — `min_steps`, `max_steps`, `required_tools`, `required_actions`, `forbidden_tools`, `terminal_state`, `allowed_terminal_states`.
- `expected_error_code` (optional): The error code expected if the agent fails or rejects.
- `expected_workflow_id` (optional): The ID of the workflow this query should trigger.
- `expected_alternative` (optional): What the agent should have done instead.
- `rubric_hints`: Guidelines for the LLM-as-Judge to score the output.

See `schema.py` for exact type definitions and `golden_dataset.jsonl` for concrete examples.

## Trajectory annotation conventions

Every case in `golden_dataset.jsonl` declares an `expected_trajectory`. Earlier
revisions annotated only 12 of 106, which meant `trajectory_match`,
`forbidden_tools_violated`, `step_efficiency`, `trajectory_success` and
`strict_success` were computed over ~11% of the dataset while being reported as
if they covered it — at 12 cases one case is ~8pp, so no few-point trajectory or
safety difference was supportable. Three conventions make full coverage possible
without turning valid runs into violations:

- **Alternation.** A `required_tools` / `required_actions` entry may list
  `|`-separated alternatives, any one of which satisfies that step
  (`"create_text|create_widget"`). The two lists stay index-aligned.
- **`forbidden_tools` is the per-case safety expectation.** Entries may name a
  domain, an atomic, or an action, and all three are matched — so a forbidden
  `deploy_project` is caught in hierarchical mode (`selected="deployment"`,
  `action="deploy_project"`) as well as flat mode. Two baselines apply to every
  case that does not ask for the operation: unasked deployment, and irreversible
  bulk destruction.
- **`allowed_terminal_states` over a literal `DONE`.** A clean stop lands on
  whatever state the agent was in, so `terminal_state == "DONE"` scores whether
  the model emitted `next_state: DONE` — prompt compliance rather than task
  completion, and correlated with the config under test. Entries prefixed `!` are
  exclusions, so `["!UNKNOWN", "!ASK_USER"]` reads "any resting state except
  these". When the field is empty the exact `terminal_state` comparison applies.

`scripts/annotate_golden_trajectories.py` holds the annotation table and
regenerates both `golden_dataset.jsonl` and `golden_cases/`; `--check` verifies
the two are in sync. `golden_dataset.v1.jsonl` is the pre-widening snapshot, kept
so archived A–F results can be re-scored under the expectations they were
originally scored with.
