"""Tracer output schema sanity."""
from __future__ import annotations

import json
from pathlib import Path

from agent.tracer import LLMCallRecord, ToolCallRecord, Tracer


def test_tracer_emits_single_jsonl_line_per_query(tmp_path: Path):
    tr = Tracer(
        results_root=tmp_path,
        config_name="test",
        model_name="mock",
        run_id="r1",
    )
    with tr.trace(golden_id="g1", query_text="hello", complexity="simple", domain="x") as ctx:
        ctx.enter_state("ANALYZE_INTENT")
        ctx.log_llm(
            LLMCallRecord(
                turn=1, model="mock", input_tokens=100, output_tokens=20,
                latency_ms=1.0, stop_reason="end_turn",
            )
        )
        ctx.log_tool_call(
            ToolCallRecord(
                turn=1, state="ANALYZE_INTENT", visible_tools=["a", "b"], visible_count=2,
                selected="a", action="a", args={}, schema_valid=True, result_ok=True,
                error_code="OK", error_msg=None, result_data={}, world_diff=None, latency_ms=2.5,
                intended_entities=["alarms.x"], referenced_entities=[],
            )
        )
        ctx.finish(terminal_state="DONE")

    out = (tmp_path / "test" / "mock" / "r1" / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(out) == 1
    rec = json.loads(out[0])
    assert rec["execution"]["terminal_state"] == "DONE"
    assert rec["totals"]["input_tokens"] == 100
    assert rec["tool_calls"][0]["intended_entities"] == ["alarms.x"]


def test_tracer_finish_is_idempotent_via_context(tmp_path: Path):
    tr = Tracer(
        results_root=tmp_path, config_name="test", model_name="mock", run_id="r2"
    )
    # Forget to call .finish() — context manager must do so on exit
    with tr.trace(golden_id="g", query_text="q") as _:
        pass
    out = (tmp_path / "test" / "mock" / "r2" / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(out) == 1
    rec = json.loads(out[0])
    assert rec["execution"]["early_terminated"] is True


def test_tracer_meta_written(tmp_path: Path):
    Tracer(
        results_root=tmp_path,
        config_name="t",
        model_name="m",
        run_id="r3",
        config_hash="sha256:abc",
        code_commit="deadbeef",
    )
    meta = (tmp_path / "t" / "m" / "r3" / "_meta.json").read_text(encoding="utf-8")
    parsed = json.loads(meta)
    assert parsed["config_name"] == "t" and parsed["code_commit"] == "deadbeef"
