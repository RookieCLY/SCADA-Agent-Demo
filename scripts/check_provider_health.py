"""Audit a run's traces for tool calls the harness failed to ingest.

Motivation: LongCat-2.0 narrates tool calls as a proprietary text block instead
of populating the OpenAI ``tool_calls`` field. Before ``agent.llm`` learned to
parse it, every function-calling arm recorded ``end_turn`` with zero tool calls
and scored ~0 — while the Plan-Execute arm (which reads a JSON payload out of
message *content*) was unaffected. An A-vs-J comparison on that provider was
measuring the parser, not the architecture.

That class of defect is invisible in the headline metrics: the run "succeeds",
every case completes, and the model simply appears incapable. So check for it
explicitly before trusting a sweep.

    python -m scripts.check_provider_health results/my_run [more_runs...]
    python -m scripts.check_provider_health --all        # every dir under results/

Exit code is non-zero when any run shows discarded calls, so it can gate a
sweep in CI or a shell pipeline.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RESULTS_ROOT = Path(__file__).resolve().parent.parent / "results"

OPEN_TAG = re.compile(r"<longcat_tool_call>")
CLOSE_TAG = re.compile(r"</longcat_tool_call>")
#: An unclosed block carrying real arguments is a truncated tool call. Without
#: them it is the malformed ``next_state`` transition form, which is expected
#: and handled by the transition regex rather than the tool-call parser.
ARG_KEY = re.compile(r"<longcat_arg_key>")


class RunReport:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.cases = 0
        self.turns = 0
        self.tools = 0
        self.done = 0
        self.discarded: list[str] = []
        self.truncated: list[str] = []
        self.benign_unclosed = 0

    @property
    def healthy(self) -> bool:
        return not self.discarded and not self.truncated

    def line(self) -> str:
        flag = "ok " if self.healthy else "FAIL"
        return (
            f"{flag} {self.run_id:<34}"
            f"cases={self.cases:<4}turns={self.turns:<5}tools={self.tools:<5}"
            f"DONE={self.done}/{self.cases or 0:<4}"
            f"discarded={len(self.discarded):<4}truncated={len(self.truncated)}"
        )


def audit(run_dir: Path) -> RunReport | None:
    traces = run_dir / "traces.jsonl"
    if not traces.exists():
        return None
    report = RunReport(run_dir.name)
    for raw in traces.open(encoding="utf-8"):
        if not raw.strip():
            continue
        trace = json.loads(raw)
        report.cases += 1
        report.tools += len(trace.get("tool_calls") or [])
        if (trace.get("execution") or {}).get("terminal_state") == "DONE":
            report.done += 1
        gid = (trace.get("query") or {}).get("golden_id", "?")
        for call in trace.get("llm_calls") or []:
            report.turns += 1
            text = call.get("text") or ""
            if "<longcat_tool_call>" not in text:
                continue
            opens = len(OPEN_TAG.findall(text))
            closes = len(CLOSE_TAG.findall(text))
            where = f"{gid} turn {call.get('turn')}"
            # A well-formed pair still sitting in the text was never ingested.
            if closes and opens:
                report.discarded.append(where)
            elif opens > closes:
                if ARG_KEY.search(text):
                    report.truncated.append(where)
                else:
                    report.benign_unclosed += 1
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="*", help="run directories or ids under results/")
    parser.add_argument("--all", action="store_true", help="audit every run under results/")
    args = parser.parse_args()

    if args.all:
        targets = sorted(p for p in RESULTS_ROOT.iterdir() if p.is_dir())
    elif args.runs:
        targets = [Path(r) if Path(r).exists() else RESULTS_ROOT / r for r in args.runs]
    else:
        parser.error("pass run ids/directories, or --all")

    reports = [r for r in (audit(t) for t in targets) if r is not None]
    if not reports:
        print("no traces.jsonl found in the given runs")
        return 1

    for report in reports:
        print(report.line())
        for where in report.discarded[:5]:
            print(f"       discarded tool call at {where}")
        for where in report.truncated[:5]:
            print(f"       truncated tool call at {where}")
        if report.benign_unclosed:
            print(
                f"       ({report.benign_unclosed} unclosed transition tags — "
                "expected, handled by the next_state regex)"
            )

    bad = [r for r in reports if not r.healthy]
    print()
    if bad:
        print(
            f"{len(bad)}/{len(reports)} runs contain tool calls the harness never "
            "ingested — their metrics understate the model. Do not compare these "
            "against a Plan-Execute arm, which does not use function calling."
        )
        return 1
    print(f"all {len(reports)} runs clean — every emitted tool call was ingested")
    return 0


if __name__ == "__main__":
    sys.exit(main())
