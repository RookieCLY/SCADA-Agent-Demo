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
- `expected_trajectory` (optional): Path constraints like `min_steps`, `max_steps`, `required_tools`, `required_actions`, `forbidden_tools`, and `terminal_state`.
- `expected_error_code` (optional): The error code expected if the agent fails or rejects.
- `expected_workflow_id` (optional): The ID of the workflow this query should trigger.
- `expected_alternative` (optional): What the agent should have done instead.
- `rubric_hints`: Guidelines for the LLM-as-Judge to score the output.

See `schema.py` for exact type definitions and `golden_dataset.jsonl` for concrete examples.
