## 2026/06/04 — advice on tolerating generated alarm ID mismatch in judges

**My original question:** in golden-003 i noticed that both llm judge and naive judge detects alarm id mismatch while they means the same semantically. since name is not specified in the query, i don't think this is a fail. i think the judge should be tolerate this, but the current logic cannot achieve this. do you have any advice

**Core answer:** The fix should be in the deterministic matcher/schema, not only the LLM prompt. Current metrics compare flattened expected paths literally, so `alarms.alarm_PT101_hi.*` is considered missing when the trace creates semantically equivalent `alarms.alarm_pt101_high.*`; `parameter_match` also uses exact path equality. The LLM judge is told deterministic metrics are stronger evidence, so it naturally repeats the false failure. Add a real semantic/`key_fields` match mode for generated entity IDs: group expected and actual paths by collection/entity, allow ID aliasing for creation cases where the query did not specify an ID, require critical fields like `tag`, `high_limit`, and `priority`, and report the alias instead of a miss. Keep exact IDs for user-specified IDs, existing-entity updates, deletes, and safety-sensitive operations.

**Relevant citations:** `eval/metrics.py:126-155`; `eval/metrics.py:434-438`; `eval/judges.py:306-324`; `eval/rubrics/default.md:16-20`; `eval/schema.py:11-17`

## 2026/06/04 — how do i test evel/judges in the project

**My original question:** how do i test evel/judges in the project

**Core answer:** Test `eval/judges.py` in layers: run the focused unit tests with `uv run python -m pytest tests\test_eval_judges.py -q`; optionally run related metric/schema tests; smoke the CLI with `python -m eval.judges --traces <traces.jsonl> --dry-run`; then run offline heuristic scoring with `--provider heuristic` and an output `judges.jsonl`; for a real LLM-as-Judge pass, set `JUDGE_API_KEY`, optionally `JUDGE_BASE_URL` and `JUDGE_MODEL`, and run `python -m eval.judges --provider openai-compatible` (or `xiaomi-mimo`) with optional deterministic metrics from `eval.metrics`.

**Relevant citations:** `tests/test_eval_judges.py:4-10`; `tests/test_eval_judges.py:86-183`; `eval/judges.py:608-625`; `eval/judges.py:628-653`; `eval/RUNNER_USAGE.md:62-70`; `eval/rubrics/README.md:48-54`

## 2026/06/03 — analyze golden-97. does script support hourly action?

**My original question:** analyze golden-97. does script support hourly action?

**Core answer:** The script model does not have an explicit `hourly` action or `event_name` field. It supports trigger values `on_change`, `on_alarm`, `periodic`, and `on_event`. An hourly script should be represented as `trigger: "periodic"` with `period_s: 3600.0`; an `on_event` script can be stored, but the current schema cannot store the event name `hourly_report`.

**Relevant citations:** `world/models.py:91-109`; `tools/manage_scripts.py:22-37`; `tools/manage_scripts.py:74-81`

## 2026/06/03 — does point object have description

**My original question:** does point object have description

**Core answer:** Yes. The MockWorld `Point` model includes an optional `description: str | None = None` field, so a point can carry a description, but it is not required.

**Relevant citations:** `world/models.py:19-27`
