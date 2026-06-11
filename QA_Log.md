## 2026/06/11 — context长度是否影响LLM调用速度

**My original question:** context长度影响llm调用速度吗

**Core answer:** 几乎不影响。用 mimo-v2.5-pro 在 55 次 LLM 调用上实测：
- **input_tokens vs latency 的 Pearson r = -0.158**（几乎零相关，甚至微负）
- **output_tokens vs latency 的 Pearson r = 0.755**（强正相关）

原因：Transformer 推理分两阶段——
1. **Prefill（处理输入）**：input tokens 并行计算，速度快，与长度弱相关
2. **Decode（生成输出）**：逐 token 串行生成，每生成一个 token 都依赖前一个，**output 长度直接线性驱动延迟**

在 1k~6k input tokens 范围内（本项目的典型 context 大小），context 长度对速度的影响可忽略。真正决定延迟的是 **输出 token 数** 和 **API 服务器负载波动**（实测同一 prompt 不同次调用延迟差异可达 2~3 倍）。

**Relevant citations:** 实测数据 `results/F_full_four_in_one/mimo-v2.5-pro/timing_test_3/traces.jsonl` + 多个历史 mimo run 的 55 条 LLM call 汇总分析

---

## 2026/06/05 — how task_success_rate is computed in eval metrics

**My original question:** task_success_rate是怎么算的

**Core answer:** `task_success_rate` is the fraction of metric rows whose boolean `task_success` is true. Per trace, `task_success` is computed deterministically from the golden record behavior: for `success`, final state must match, expected error-code condition must match, and trajectory must match if specified; for `reject` or `ask_for_clarification`, final state must match, no world mutation may occur, and trajectory must match if specified; for `fail_or_clarify`, final state must match, plus either expected error-code matches or no world mutation occurs, and trajectory must match if specified. In run `results/F_full_four_in_one/mock/000002`, there are 114 metric rows, 48 true and 66 false, so `task_success_rate = 48 / 114 = 0.42105263157894735`.

**Relevant citations:** `eval/metrics.py:665-686`; `eval/metrics.py:816-857`; `results/F_full_four_in_one/mock/000002/metrics_summary.json:1-25`

## 2026/06/05 — how to use project LLM-as-Judge

**My original question:** 这个项目的llm-as-judge怎么用

**Core answer:** LLM-as-Judge is the offline semantic scoring layer in `eval/judges.py`; it consumes `traces.jsonl` from `eval.runner`, optionally deterministic `metrics.jsonl` from `eval.metrics`, a rubric markdown file, and writes `judges.jsonl`. Recommended flow: first run `eval.runner` to generate traces, then run `eval.metrics` to compute deterministic evidence, then run `eval.judges` with `--provider xiaomi-mimo` or `--provider openai-compatible`. For xiaomi-mimo/openai-compatible, set `JUDGE_API_KEY`, usually `JUDGE_BASE_URL`, and optionally `JUDGE_MODEL`; `--provider xiaomi-mimo` is internally mapped to the OpenAI-compatible client. `--provider heuristic` is a no-external-LLM baseline, and `--dry-run` only validates/counts inputs without judging.

**Relevant citations:** `eval/judges.py:0-5`; `eval/judges.py:150-156`; `eval/judges.py:170-190`; `eval/judges.py:608-625`; `eval/judges.py:628-653`; `eval/RUNNER_USAGE.md:62-70`; `eval/rubrics/README.md:48-53`

## 2026/06/05 — whether golden test cases need key_fields to enable ID-tolerant matching

**My original question:** do i need to change golden test cases to enable matching

**Core answer:** Yes, for any golden case where the expected entity ID is only symbolic/generated and the user did not specify an exact ID, set `expected_final_state_diff.match_mode` to `"key_fields"`. The new matcher only performs generated-ID aliasing in `key_fields`; `subset` and `strict` intentionally remain exact-path modes. You do not need to change the expected semantic fields themselves, e.g. keep `alarms.alarm_PT101_hi.tag/high_limit/priority`, but change the mode so the evaluator may alias that symbolic prefix to the actual generated prefix when all fields match. Do not use `key_fields` for cases where the ID is user-specified, or for updates/deletes/renames/bindings that target an existing entity and should require exact identity.

**Relevant citations:** `eval/metrics.py:210`; `eval/metrics.py:229-251`; `eval/metrics.py:541-567`; `eval/golden_cases/golden-003.json:15-20`; `eval/rubrics/default.md:72-74`

## 2026/06/05 — whether eval/runner.py retries LLM errors

**My original question:** 当前的eval/runner.py支持llm出错自动重试吗

**Core answer:** Yes. `eval/runner.py` supports automatic reruns for technical failures: `--max-reruns` defaults to 3, and each golden record repetition can run up to `max_reruns + 1` attempts. It catches exceptions from `agent.run(...)` and also treats `terminal_state == "UNKNOWN"` or `early_terminated` as technical failures, retrying until attempts are exhausted. Exhausted failures are written to `_failures.jsonl`; successful completed pairs are skipped on `--resume`, while `UNKNOWN`/early-terminated traces are not counted as completed. This retry logic applies to experiment trace generation, not the offline LLM judge, because this runner deliberately does not run judges.

**Relevant citations:** `eval/runner.py:7-12`; `eval/runner.py:150-164`; `eval/runner.py:171-175`; `eval/runner.py:345-390`; `eval/runner.py:444-445`

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
