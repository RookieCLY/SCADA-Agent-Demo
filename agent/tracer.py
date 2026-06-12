"""Trace recorder — JSONL primary, Langfuse optional.

Phase-1 ships the JSONL writer that matches the schema in §4.1 of the
development plan. Langfuse is only initialised when both ``LANGFUSE_PUBLIC_KEY``
and ``LANGFUSE_SECRET_KEY`` are present in the environment; otherwise we no-op.

A ``Tracer`` instance corresponds to a single experiment *run* (one
``config × model × seed`` tuple). Inside the run, every query opens a
``TraceContext`` that accumulates tool / LLM / resource events and flushes
exactly one JSONL line on close.

Thread safety: pass ``write_lock=threading.Lock()`` to serialise writes across
multiple tracer instances that share the same output files.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ============================================================ helpers
def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _new_trace_id() -> str:
    return str(uuid.uuid4())


# ============================================================ event dataclasses
@dataclass
class ToolCallRecord:
    turn: int
    state: str
    visible_tools: list[str]
    visible_count: int
    selected: str
    action: str | None
    args: dict[str, Any]
    schema_valid: bool
    result_ok: bool
    error_code: str
    error_msg: str | None
    result_data: dict[str, Any]
    world_diff: dict[str, Any] | None
    latency_ms: float
    intended_entities: list[str] = field(default_factory=list)
    referenced_entities: list[str] = field(default_factory=list)


@dataclass
class LLMCallRecord:
    turn: int
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    stop_reason: str
    text: str | None = None
    reasoning: str | None = None


@dataclass
class StateRecord:
    name: str
    entered_at: str
    exited_at: str | None = None


# ============================================================ TraceContext
class TraceContext:
    def __init__(
        self,
        tracer: "Tracer",
        golden_id: str,
        query_text: str,
        complexity: str = "unknown",
        domain: str = "unknown",
        rep_index: int = 0,
        seed: int = 42,
        record_llm_io: bool = False,
    ) -> None:
        self.tracer = tracer
        self.record_llm_io = record_llm_io
        self.trace_id = _new_trace_id()
        self.golden_id = golden_id
        self.query_text = query_text
        self.complexity = complexity
        self.domain = domain
        self.rep_index = rep_index
        self.seed = seed

        self.started_at = _utc_iso()
        self.t0 = time.perf_counter()
        self.terminal_state: str | None = None
        self.early_terminated = False
        self.termination_reason: str | None = None

        self.states: list[StateRecord] = []
        self.tool_calls: list[ToolCallRecord] = []
        self.llm_calls: list[LLMCallRecord] = []
        self.resource_reads: list[dict[str, Any]] = []
        self.initial_world_hash: str | None = None
        self.final_world_hash: str | None = None
        self.final_state_match: bool | None = None
        self.match_mode: str | None = None
        self.diff_against_expected: dict[str, Any] = {}
        # Phase-2 architecture metadata (populated by orchestrator)
        self.rag_summary: dict[str, Any] = {"enabled": False}
        self.workflow_summary: dict[str, Any] = {"enabled": False, "selected_workflow": None}

    # ---------- state events
    def enter_state(self, name: str) -> None:
        if self.states and self.states[-1].exited_at is None:
            self.states[-1].exited_at = _utc_iso()
        self.states.append(StateRecord(name=name, entered_at=_utc_iso()))

    def exit_state(self) -> None:
        if self.states and self.states[-1].exited_at is None:
            self.states[-1].exited_at = _utc_iso()

    # ---------- llm event
    def log_llm(
        self,
        rec: LLMCallRecord,
        text: str | None = None,
        reasoning: str | None = None,
    ) -> None:
        if self.record_llm_io:
            rec.text = text
            rec.reasoning = reasoning
        self.llm_calls.append(rec)

    # ---------- tool event
    def log_tool_call(self, rec: ToolCallRecord) -> None:
        self.tool_calls.append(rec)

    # ---------- finish
    def finish(
        self,
        terminal_state: str,
        early_terminated: bool = False,
        termination_reason: str | None = None,
    ) -> dict[str, Any]:
        self.exit_state()
        self.terminal_state = terminal_state
        self.early_terminated = early_terminated
        self.termination_reason = termination_reason
        completed_at = _utc_iso()
        total_input = sum(c.input_tokens for c in self.llm_calls)
        total_output = sum(c.output_tokens for c in self.llm_calls)
        e2e_ms = (time.perf_counter() - self.t0) * 1000
        record = {
            "trace_id": self.trace_id,
            "experiment": {
                "config_name": self.tracer.config_name,
                "config_hash": self.tracer.config_hash,
                "code_commit": self.tracer.code_commit,
                "model": self.tracer.model_name,
                "dataset_version": self.tracer.dataset_version,
                "rep_index": self.rep_index,
                "seed": self.seed,
            },
            "query": {
                "golden_id": self.golden_id,
                "text": self.query_text,
                "complexity": self.complexity,
                "domain": self.domain,
            },
            "execution": {
                "started_at": self.started_at,
                "completed_at": completed_at,
                "total_turns": len(self.llm_calls),
                "terminal_state": self.terminal_state,
                "early_terminated": self.early_terminated,
                "termination_reason": self.termination_reason,
            },
            "states": [asdict(s) for s in self.states],
            "tool_calls": [asdict(c) for c in self.tool_calls],
            "resource_reads": self.resource_reads,
            "world_snapshots": {
                "initial_hash": self.initial_world_hash,
                "final_hash": self.final_world_hash,
                "final_state_match": self.final_state_match,
                "match_mode": self.match_mode,
                "diff_against_expected": self.diff_against_expected,
            },
            "llm_calls": [asdict(c) for c in self.llm_calls],
            "rag": self.rag_summary,
            "workflow": self.workflow_summary,
            "totals": {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cost_usd": 0.0,
                "e2e_latency_ms": e2e_ms,
            },
            "judge": None,
        }
        self.tracer._write(record)
        return record


# ============================================================ Tracer
class Tracer:
    def __init__(
        self,
        results_root: Path | str,
        config_name: str,
        model_name: str,
        config_hash: str = "",
        code_commit: str = "",
        dataset_version: str = "dev",
        run_id: str | None = None,
        record_llm_io: bool = False,
        write_lock: threading.Lock | None = None,
    ) -> None:
        self.config_name = config_name
        self.model_name = model_name
        self.record_llm_io = record_llm_io
        self.config_hash = config_hash
        self.code_commit = code_commit
        self.dataset_version = dataset_version
        self.run_id = run_id or _new_trace_id()[:8]
        self._write_lock = write_lock

        root = Path(results_root) / self.run_id
        root.mkdir(parents=True, exist_ok=True)
        self.run_dir = root
        self.traces_path = root / "traces.jsonl"
        self.meta_path = root / "_meta.json"

        # write metadata stub
        if not self.meta_path.exists():
            self.meta_path.write_text(
                json.dumps(
                    {
                        "config_name": config_name,
                        "model": model_name,
                        "config_hash": config_hash,
                        "code_commit": code_commit,
                        "dataset_version": dataset_version,
                        "started_at": _utc_iso(),
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        # Langfuse — optional / silently disabled without keys
        self._langfuse = self._maybe_langfuse()

    def _maybe_langfuse(self) -> Any:
        if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
            return None
        try:  # pragma: no cover — only exercised when langfuse is installed
            from langfuse import Langfuse  # type: ignore

            return Langfuse()
        except Exception:
            return None

    @contextmanager
    def trace(
        self,
        *,
        golden_id: str,
        query_text: str,
        complexity: str = "unknown",
        domain: str = "unknown",
        rep_index: int = 0,
        seed: int = 42,
    ):
        ctx = TraceContext(
            tracer=self,
            golden_id=golden_id,
            query_text=query_text,
            complexity=complexity,
            domain=domain,
            rep_index=rep_index,
            seed=seed,
            record_llm_io=self.record_llm_io,
        )
        try:
            yield ctx
        finally:
            # If user code didn't call .finish(), do so with a best-effort terminal state.
            if ctx.terminal_state is None:
                ctx.finish(
                    terminal_state="UNKNOWN", early_terminated=True, termination_reason="no finish() call"
                )

    def _write(self, record: dict[str, Any]) -> None:
        if self._write_lock is not None:
            self._write_lock.acquire()
        try:
            with self.traces_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        finally:
            if self._write_lock is not None:
                self._write_lock.release()
        if self._langfuse:  # pragma: no cover
            try:
                self._langfuse.trace(
                    name=self.config_name, id=record["trace_id"], metadata=record
                )
            except Exception:
                pass


__all__ = ["LLMCallRecord", "StateRecord", "ToolCallRecord", "TraceContext", "Tracer"]
