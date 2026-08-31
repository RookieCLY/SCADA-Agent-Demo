#!/usr/bin/env python
"""Supervisor for the results_w30 DeepSeek wave.

Why this exists rather than a bash loop. The w30 wave died twice, of two
unrelated causes, and a naive restart loop mishandles both:

  1. ``402 Insufficient Balance`` — 1,786 of them. A restart here is not
     merely useless, it is *invisible*: 402s are rejected unbilled, so the
     loop spins happily writing two void trace lines per case forever and the
     archive fills with junk that looks like data. This supervisor probes the
     balance endpoint *before* each cell and sleeps instead of spinning.
  2. ``fork: Resource temporarily unavailable`` (0xC000026B) when the parent
     shell's session tore down. A bash supervisor is vulnerable to exactly the
     same teardown; a detached Python process is not.

Cells are ordered by scientific value per token, because the balance may not
cover everything: the pre-specified key family (A, F, Ip, J) completes first,
cheapest cell first, so that a mid-run exhaustion costs descriptive arms
rather than the primary claim. Arm A costs ~220k input tokens per run against
J's ~6k, so A's cells are deliberately last within the key family.

Idempotent: every cell is skipped when already complete, so this may be
re-run, killed, and re-run again at any point.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO.parent / "results_w30"
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
LOG = ROOT / "_supervisor.log"          # lives with the traces it describes
STOP_FLAG = REPO.parent / "results_w30.STOP"

PROVIDER = "deepseek"
MODEL = "deepseek-v4-flash"
BLOCKED = "golden-059,golden-074"
GOLDEN_N = 104
PROBE_N = 22

CONFIG = {
    "A": "A_flat_baseline", "B": "B_hierarchical_only", "C": "C_hier_rag",
    "D": "D_hier_rag_workflow", "E": "E_with_state_machine",
    "F": "F_full_four_in_one", "Fnr": "F_noresources", "G": "G_safety_runtime",
    "H": "H_workflow_engine", "Ir": "I_react", "Ip": "I_plan_execute",
    "Im": "I_multi_agent", "J": "J_combined",
}
# measured mean input tokens/run from the complete rep0
COST = {
    "A": 219_924, "B": 210_065, "C": 8_935, "D": 9_655, "E": 10_762,
    "F": 18_561, "Fnr": 9_513, "G": 20_961, "H": 9_129, "Ir": 18_580,
    "Ip": 9_084, "Im": 20_264, "J": 6_181,
}
KEY_ARMS = ("F", "Ip", "J", "A")  # pre-specified family; A last (token hog)

MIN_BALANCE_CNY = 0.50
BALANCE_SLEEP = 600      # balance too low / 402: wait this long, then re-probe
CRASH_SLEEP = 30         # process died for a non-billing reason


def log(msg: str) -> None:
    line = f"{datetime.now(UTC).isoformat(timespec='seconds')}  {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_key() -> str:
    sys.path.insert(0, str(REPO))
    from agent.llm import _load_dotenv_into_environ

    _load_dotenv_into_environ()
    return os.environ.get("DEEPSEEK_API_KEY", "")


def balance_cny(key: str) -> float | None:
    """Return the topped-up balance, or None if the endpoint is unreachable."""
    req = urllib.request.Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001 - any failure means "unknown"
        log(f"    balance probe failed: {type(exc).__name__} {exc}")
        return None
    if not data.get("is_available", False):
        return 0.0
    for info in data.get("balance_infos", []):
        if info.get("currency") == "CNY":
            return float(info.get("total_balance", 0.0))
    return None


def done_cases(run_id: str) -> int:
    """Cases that reached a terminal state.

    Emphatically NOT "cases with an llm_calls entry": a run killed by a 402
    records the rejected call, so that test counted provider outages as
    finished work. It marked J_rep1 and Ip_rep1 complete when both held 104
    cases x 2 failed attempts and zero usable data, and the supervisor would
    never have re-run them.
    """
    t = ROOT / run_id / "traces.jsonl"
    if not t.exists():
        return 0
    good: set[str] = set()
    with t.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                tr = json.loads(line)
            except Exception:  # noqa: BLE001 - a torn last line is not fatal
                continue
            execution = tr.get("execution") or {}
            if execution.get("terminal_state") in (None, "UNKNOWN"):
                continue
            good.add(tr["query"]["golden_id"])
    return len(good)


def build_cells() -> list[dict]:
    cells = []
    for rep in range(3):
        for arm, cfg in CONFIG.items():
            run_id = f"{arm}_rep{rep}"
            missing = GOLDEN_N - done_cases(run_id)
            if missing <= 0:
                continue
            cells.append({
                "run_id": run_id, "cfg": cfg, "rep": rep, "arm": arm,
                "missing": missing, "tokens": missing * COST[arm],
                "key": arm in KEY_ARMS, "probe": False,
            })
    for rep in range(3):
        for arm, cfg in (("probeA", "A_flat_baseline"), ("probeJ", "J_combined")):
            run_id = f"{arm}_rep{rep}"
            missing = PROBE_N - done_cases(run_id)
            if missing <= 0:
                continue
            cells.append({
                "run_id": run_id, "cfg": cfg, "rep": rep, "arm": arm,
                "missing": missing, "tokens": missing * 20_000,
                "key": False, "probe": True,
            })
    # key family first, then cheapest-first within each band
    cells.sort(key=lambda c: (not c["key"], c["tokens"]))
    return cells


def run_cell(cell: dict) -> int:
    cmd = [
        PY, "-m", "eval.runner",
        "--config", f"configs/{cell['cfg']}.yaml", "--all",
        "--provider", PROVIDER, "--model", MODEL,
        "--reps", "1", "--seed-base", str(42 + cell["rep"]),
        "--results-root", str(ROOT), "--run-id", cell["run_id"],
        "--resume", "--max-reruns", "1",
    ]
    if cell["probe"]:
        # Append, never splice. The first version inserted at a hardcoded index
        # that landed between "--config" and its value, so argparse consumed
        # "--dataset" as the config path and every probe cell exited without
        # running a single case.
        cmd += ["--dataset", "eval/golden_safety_probe.jsonl"]
    else:
        cmd += ["--exclude-golden-ids", BLOCKED]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    if proc.returncode != 0:
        log(f"    runner exit {proc.returncode}: {proc.stderr.strip()[-300:]}")
    return proc.returncode


def main() -> int:
    key = load_key()
    if not key:
        log("FATAL: DEEPSEEK_API_KEY not set")
        return 1
    log("supervisor started")

    # A cell that keeps returning without doing anything is a bug in the cell,
    # not an outage: with the balance healthy, retrying it forever starves
    # every cell behind it. Park it after MAX_STALLS and move on.
    stalls: dict[str, int] = {}
    MAX_STALLS = 3

    while True:
        if STOP_FLAG.exists():
            log(f"stop flag present ({STOP_FLAG}); exiting")
            return 0

        cells = [c for c in build_cells()
                 if stalls.get(c["run_id"], 0) < MAX_STALLS]
        if not cells:
            parked = [k for k, v in stalls.items() if v >= MAX_STALLS]
            if parked:
                log(f"ALL REMAINING CELLS PARKED after {MAX_STALLS} stalls: "
                    f"{sorted(parked)} — investigate, then restart")
                return 1
            log("ALL CELLS COMPLETE — w30 finished")
            return 0

        total_tok = sum(c["tokens"] for c in cells)
        log(f"{len(cells)} cells remaining, ~{total_tok/1e6:.1f}M input tokens")

        bal = balance_cny(key)
        if bal is not None and bal < MIN_BALANCE_CNY:
            log(f"BALANCE EXHAUSTED (CNY {bal:.2f}) — sleeping {BALANCE_SLEEP}s, "
                f"will resume automatically when topped up")
            time.sleep(BALANCE_SLEEP)
            continue

        cell = cells[0]
        log(f"-> {cell['run_id']} ({cell['missing']} cases, "
            f"~{cell['tokens']/1e6:.1f}M tok, key={cell['key']}, "
            f"balance CNY {bal if bal is not None else '?'})")
        before = done_cases(cell["run_id"])
        run_cell(cell)
        after = done_cases(cell["run_id"])
        log(f"   {cell['run_id']}: {before} -> {after} / "
            f"{PROBE_N if cell['probe'] else GOLDEN_N}")

        if after > before:
            stalls.pop(cell["run_id"], None)
        else:
            # no forward progress: billing, the endpoint, or a broken cell
            bal = balance_cny(key)
            if bal is not None and bal < MIN_BALANCE_CNY:
                log(f"   no progress and balance CNY {bal:.2f} — "
                    f"sleeping {BALANCE_SLEEP}s")
                time.sleep(BALANCE_SLEEP)
            else:
                stalls[cell["run_id"]] = stalls.get(cell["run_id"], 0) + 1
                log(f"   stall {stalls[cell['run_id']]}/{MAX_STALLS} "
                    f"on {cell['run_id']} with balance CNY {bal}")
                log(f"   no progress with balance CNY {bal} — "
                    f"sleeping {CRASH_SLEEP}s and retrying")
                time.sleep(CRASH_SLEEP)


if __name__ == "__main__":
    raise SystemExit(main())
