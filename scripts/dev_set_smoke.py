"""Real-LLM smoke runner over a 5-query dev set.

Replaces the 1-query ``scripts/test_xiaomi_mimo.py`` with a fan-out over a
deliberately diverse dev set spanning the main SCADA domains. Two use cases:

1. **Phase-2 dev-set calibration** (the §2.4 plan item that was deferred): run
   the 5 queries under a single config (default ``configs/xiaomi_mimo_smoke``)
   and record the total token + latency budget so future runs have a baseline.

2. **Multi-config closure** (§2.4 acceptance — "配置 A~F 一行命令切换"): with
   ``--config configs/X.yaml --force-mimo`` you can drive any of the six
   §3.2-matrix configs through the same dev set. ``--all-configs`` sweeps the
   full A-F matrix in one invocation.

The script never mutates source configs; it monkeypatches the ``model.provider``
field in-memory at assembly time when ``--force-mimo`` is set.

Output
======

* Per-query line: ``[domain/complexity] config -> turns, tool_calls,
  terminal_state, world_changed?, token_in/out, latency_ms``
* Summary table at the end + total cost estimate.
* ``--out PATH`` writes the full record set as JSON for downstream analysis.

Usage
=====
::

    venv/Scripts/python -m scripts.dev_set_smoke
    venv/Scripts/python -m scripts.dev_set_smoke --config configs/F_full_four_in_one.yaml --force-mimo
    venv/Scripts/python -m scripts.dev_set_smoke --all-configs --out results/dev_set_summary.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.config import ModelConfig, load_config
from agent.llm import _env, _load_dotenv_into_environ
from agent.orchestrator import assemble, build_demo_world


# ============================================================ dev set
@dataclass(frozen=True)
class DevQuery:
    qid: str
    domain: str
    complexity: str
    query: str
    expected_atomic: str  # the atomic tool we hope the LLM ends up invoking

    def __str__(self) -> str:
        return f"[{self.qid}|{self.domain}|{self.complexity}] {self.query}"


DEV_QUERIES: list[DevQuery] = [
    DevQuery(
        qid="dev-alarm-01",
        domain="alarm",
        complexity="simple",
        query=(
            "给反应釜1的TEMP_101点位加一个高温报警,阈值80度,优先级high,"
            "ID用alarm_high_temp_101。"
        ),
        expected_atomic="create_analog_alarm",
    ),
    DevQuery(
        qid="dev-point-01",
        domain="point",
        complexity="simple",
        query="新建一个模拟点位 TEMP_205,量程 0~200,单位 °C,描述: 出口温度。",
        expected_atomic="create_point",
    ),
    DevQuery(
        qid="dev-history-01",
        domain="history",
        complexity="medium",
        query=(
            "先给 TEMP_101 开启历史归档(保留 7 天),然后查它最近一分钟的趋势,"
            "最多返回 5 个采样点。"
        ),
        expected_atomic="query_history",
    ),
    DevQuery(
        qid="dev-graphics-01",
        domain="graphics",
        complexity="medium",
        query=(
            "先新建一个名为「主控画面」的页面 p1,然后在 p1 上画一个矩形,"
            "widget_id 用 r1,位置 (50,50),大小 (120,80),颜色红色。"
        ),
        expected_atomic="create_rect",
    ),
    DevQuery(
        qid="dev-deployment-01",
        domain="deployment",
        complexity="simple",
        query="把当前项目做一次校验,deployment_id 用 d1。",
        expected_atomic="validate_project",
    ),
]


# ============================================================ run record
@dataclass
class RunRecord:
    config: str
    qid: str
    domain: str
    complexity: str
    query: str
    expected_atomic: str
    terminal_state: str
    early_terminated: bool
    total_turns: int
    tool_calls: int
    resource_reads: int
    world_changed: bool
    expected_tool_called: bool
    expected_tool_ok: bool
    input_tokens: int
    output_tokens: int
    e2e_latency_ms: int
    error: str | None = None

    def short(self) -> str:
        ok_mark = "[OK]" if self.expected_tool_called and self.expected_tool_ok else "[!! ]"
        return (
            f"  {ok_mark} {self.qid:<22s} cfg={self.config:<26s} "
            f"turns={self.total_turns} tools={self.tool_calls} "
            f"in={self.input_tokens:5d} out={self.output_tokens:4d} "
            f"lat={self.e2e_latency_ms:5d}ms term={self.terminal_state}"
        )


# ============================================================ runner
def _record_metrics(
    record: dict[str, Any], cfg_name: str, q: DevQuery
) -> RunRecord:
    tools = record.get("tool_calls", [])
    expected_called = False
    expected_ok = False
    for c in tools:
        # `action` is set when the LLM hit a domain Tool with a sub-action; the
        # atomic name is then `action`. For flat-mode calls, `selected` already
        # holds the atomic name.
        atomic = c.get("action") or c.get("selected")
        if atomic == q.expected_atomic:
            expected_called = True
            if bool(c.get("result_ok")):
                # Any successful attempt counts — the LLM may have retried.
                expected_ok = True
                break
    snaps = record.get("world_snapshots", {}) or {}
    world_changed = snaps.get("initial_hash") != snaps.get("final_hash")
    exe = record["execution"]
    totals = record.get("totals", {}) or {}
    return RunRecord(
        config=cfg_name,
        qid=q.qid,
        domain=q.domain,
        complexity=q.complexity,
        query=q.query,
        expected_atomic=q.expected_atomic,
        terminal_state=exe["terminal_state"],
        early_terminated=bool(exe.get("early_terminated")),
        total_turns=int(exe.get("total_turns", 0)),
        tool_calls=len(tools),
        resource_reads=len(record.get("resource_reads", []) or []),
        world_changed=bool(world_changed),
        expected_tool_called=expected_called,
        expected_tool_ok=expected_ok,
        input_tokens=int(totals.get("input_tokens", 0)),
        output_tokens=int(totals.get("output_tokens", 0)),
        e2e_latency_ms=int(totals.get("e2e_latency_ms", 0)),
    )


def _run_single_config(
    config_path: Path,
    *,
    queries: list[DevQuery],
    force_mimo: bool,
    max_turns: int,
) -> list[RunRecord]:
    """Assemble once per config, then run each query on a fresh world."""
    # Optionally override the model.provider before assemble() picks it up.
    if force_mimo:
        # Load the config, modify the in-memory ExperimentConfig, then write
        # to a tmp YAML path so assemble() picks the patched version.
        cfg = load_config(config_path)
        cfg.model = ModelConfig(
            provider="xiaomi-mimo",
            name=_env("XIAOMI-MIMO_MODEL", "XIAOMI_MIMO_MODEL") or "mimo-v2.5-pro",
            temperature=cfg.model.temperature,
            max_tokens=max(cfg.model.max_tokens, 1024),
        )
        # Write a temporary copy next to the original so any relative paths
        # (e.g. workflows/) still resolve correctly.
        patched = config_path.with_suffix(".__patched__.yaml")
        patched.write_text(_dump_yaml(cfg), encoding="utf-8")
        try:
            agent = assemble(patched)
        finally:
            patched.unlink(missing_ok=True)
    else:
        agent = assemble(config_path)
    agent.max_turns = max_turns

    out: list[RunRecord] = []
    for q in queries:
        try:
            t0 = time.perf_counter()
            record = agent.run(
                q.query,
                golden_id=q.qid,
                initial_world=build_demo_world(),
                complexity=q.complexity,
                domain=q.domain,
            )
            wall_ms = int((time.perf_counter() - t0) * 1000)
            r = _record_metrics(record, config_path.stem, q)
            # totals.e2e_latency_ms is sometimes 0 when LLM latency is not yet
            # aggregated; fall back to wall time so the summary is informative.
            if r.e2e_latency_ms == 0:
                r.e2e_latency_ms = wall_ms
            out.append(r)
            print(r.short())
        except Exception as exc:
            r = RunRecord(
                config=config_path.stem,
                qid=q.qid,
                domain=q.domain,
                complexity=q.complexity,
                query=q.query,
                expected_atomic=q.expected_atomic,
                terminal_state="ERROR",
                early_terminated=True,
                total_turns=0,
                tool_calls=0,
                resource_reads=0,
                world_changed=False,
                expected_tool_called=False,
                expected_tool_ok=False,
                input_tokens=0,
                output_tokens=0,
                e2e_latency_ms=0,
                error=str(exc),
            )
            out.append(r)
            print(f"  [ERR] {q.qid:<22s} cfg={config_path.stem:<26s} ERROR: {exc}")
    return out


def _dump_yaml(cfg: Any) -> str:
    """Serialize ExperimentConfig back to YAML for assemble()."""
    # Use json round-trip + yaml dump to avoid coupling to pydantic internals.
    import yaml

    data = json.loads(cfg.model_dump_json())
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _summarise(records: list[RunRecord]) -> dict[str, Any]:
    """Aggregate token/latency/success rates per config."""
    by_cfg: dict[str, list[RunRecord]] = {}
    for r in records:
        by_cfg.setdefault(r.config, []).append(r)
    summary: dict[str, Any] = {}
    for cfg_name, rs in by_cfg.items():
        n = len(rs)
        ok = sum(1 for r in rs if r.expected_tool_called and r.expected_tool_ok)
        token_in = sum(r.input_tokens for r in rs)
        token_out = sum(r.output_tokens for r in rs)
        lat = sum(r.e2e_latency_ms for r in rs)
        terms = {}
        for r in rs:
            terms[r.terminal_state] = terms.get(r.terminal_state, 0) + 1
        summary[cfg_name] = {
            "n_queries": n,
            "expected_tool_success": f"{ok}/{n}",
            "expected_tool_success_rate": round(ok / n if n else 0.0, 3),
            "total_input_tokens": token_in,
            "total_output_tokens": token_out,
            "total_latency_ms": lat,
            "avg_latency_ms_per_query": int(lat / n) if n else 0,
            "terminal_state_dist": terms,
        }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Phase-2 dev-set under a real LLM."
    )
    parser.add_argument(
        "--config",
        default="configs/xiaomi_mimo_smoke.yaml",
        help="Config YAML (ignored when --all-configs is set)",
    )
    parser.add_argument(
        "--force-mimo",
        action="store_true",
        help="Override the config's model.provider to xiaomi-mimo (use with non-mimo configs)",
    )
    parser.add_argument(
        "--all-configs",
        action="store_true",
        help="Sweep configs A_flat_baseline … F_full_four_in_one (implies --force-mimo)",
    )
    parser.add_argument(
        "--max-turns", type=int, default=12, help="Agent max turns per query"
    )
    parser.add_argument(
        "--out", type=str, default=None, help="Optional JSON path for the full result"
    )
    args = parser.parse_args(argv)

    _load_dotenv_into_environ()
    if not _env("XIAOMI-MIMO_API_KEY", "XIAOMI_MIMO_API_KEY"):
        print(
            "[fatal] no XIAOMI-MIMO_API_KEY in env/.env — cannot run real-LLM smoke.",
            file=sys.stderr,
        )
        return 1

    if args.all_configs:
        configs = [
            Path("configs/A_flat_baseline.yaml"),
            Path("configs/B_hierarchical_only.yaml"),
            Path("configs/C_hier_rag.yaml"),
            Path("configs/D_hier_rag_workflow.yaml"),
            Path("configs/E_with_state_machine.yaml"),
            Path("configs/F_full_four_in_one.yaml"),
        ]
        force_mimo = True
    else:
        configs = [Path(args.config)]
        force_mimo = args.force_mimo

    all_records: list[RunRecord] = []
    for cfg_path in configs:
        if not cfg_path.is_file():
            print(f"[warn] missing config: {cfg_path} — skipped", file=sys.stderr)
            continue
        print()
        print(f"=== {cfg_path.name} ===")
        recs = _run_single_config(
            cfg_path,
            queries=DEV_QUERIES,
            force_mimo=force_mimo,
            max_turns=args.max_turns,
        )
        all_records.extend(recs)

    summary = _summarise(all_records)
    print()
    print("=== summary ===")
    for cfg_name, s in summary.items():
        print(
            f"  {cfg_name:<24s} ok={s['expected_tool_success']:<6s} "
            f"({s['expected_tool_success_rate']*100:5.1f}%) "
            f"in={s['total_input_tokens']:6d} out={s['total_output_tokens']:5d} "
            f"avg_lat={s['avg_latency_ms_per_query']:5d}ms term={s['terminal_state_dist']}"
        )

    grand_in = sum(s["total_input_tokens"] for s in summary.values())
    grand_out = sum(s["total_output_tokens"] for s in summary.values())
    grand_lat = sum(s["total_latency_ms"] for s in summary.values())
    n_q = sum(s["n_queries"] for s in summary.values())
    print()
    print(
        f"[total] queries={n_q}  input_tokens={grand_in}  "
        f"output_tokens={grand_out}  latency_ms={grand_lat}"
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {"summary": summary, "records": [asdict(r) for r in all_records]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[total] full result written to {out_path}")

    # Exit non-zero if any query's expected tool was not invoked successfully.
    fail = sum(
        1 for r in all_records if not (r.expected_tool_called and r.expected_tool_ok)
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
