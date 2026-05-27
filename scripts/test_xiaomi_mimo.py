"""Real-LLM smoke test for the xiaomi-mimo provider.

Boots the orchestrator under ``configs/xiaomi_mimo_smoke.yaml`` (flat tools,
state machine OFF) and runs a single Chinese-language query that the model is
expected to translate into a ``create_analog_alarm`` tool call. Prints the
trace summary so we can eyeball whether the end-to-end loop closed.

Run from the repo root:

    venv/Scripts/python scripts/test_xiaomi_mimo.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the repo root importable so we can run this script directly via
# `python scripts/test_xiaomi_mimo.py` without an `-e .` install.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.orchestrator import assemble, build_demo_world


CONFIG_PATH = "configs/xiaomi_mimo_smoke.yaml"
QUERY = "给反应釜1的TEMP_101点位加一个高温报警,阈值80度,优先级high,ID用alarm_high_temp_101"


def main() -> int:
    print(f"[setup] assembling agent from {CONFIG_PATH}")
    agent = assemble(CONFIG_PATH)
    print(f"[setup] model = {agent.config.model.provider} / {agent.config.model.name}")
    print(f"[setup] atomic tools registered = {len(agent.registry.all_atomics())}")
    print(f"[setup] state machine = {agent.config.architecture.state_machine.enabled}")
    print(f"[setup] hierarchical = {agent.config.architecture.hierarchical_tools}")
    print()
    print(f"[query] {QUERY}")
    print()

    world = build_demo_world()
    record = agent.run(
        QUERY,
        golden_id="xiaomi-mimo-smoke-001",
        initial_world=world,
        complexity="simple",
        domain="alarm",
    )

    exe = record["execution"]
    print("[result] ---- summary ----")
    print(f"  trace_id       : {record['trace_id']}")
    print(f"  total_turns    : {exe['total_turns']}")
    print(f"  terminal_state : {exe['terminal_state']}")
    print(f"  early_terminated: {exe['early_terminated']}")
    print(f"  termination    : {exe.get('termination_reason')}")
    print(f"  tool_calls     : {len(record['tool_calls'])}")
    print(f"  trace_jsonl    : {agent.tracer.traces_path}")
    print()

    if record["tool_calls"]:
        print("[result] ---- tool calls ----")
        for c in record["tool_calls"]:
            print(
                f"  turn={c['turn']} selected={c['selected']} action={c.get('action')} "
                f"ok={c['result_ok']} err={c.get('error_code')}"
            )
            if c.get("args"):
                print(f"           args={json.dumps(c['args'], ensure_ascii=False)}")
            if c.get("error_msg"):
                print(f"           msg={c['error_msg']}")

    print()
    print("[result] ---- llm calls ----")
    for c in record.get("llm_calls", []):
        print(
            f"  turn={c['turn']} model={c['model']} "
            f"in={c['input_tokens']} out={c['output_tokens']} "
            f"latency_ms={c['latency_ms']:.0f} stop={c['stop_reason']}"
        )

    hashes = record.get("world_snapshots", {})
    print()
    print(f"[result] world_hash {hashes.get('initial_hash')} → {hashes.get('final_hash')}")
    changed = hashes.get("initial_hash") != hashes.get("final_hash")
    print(f"[result] world mutated? {changed}")

    ok = (
        not exe["early_terminated"]
        and any(c["result_ok"] for c in record["tool_calls"])
    )
    print()
    print(f"[verdict] {'PASS — real LLM produced a successful tool call' if ok else 'FAIL — see above'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
