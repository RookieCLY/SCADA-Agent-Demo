"""Audit which tools can actually change the world, by running them.

Motivation: `forbidden_tools` in the golden dataset, and the §4.7 safety probe,
both rest on the assumption that the tools they name can do harm. That
assumption was never checked, and it was wrong often enough to matter —
`manage_devices` shipped 20 tools and a real `devices` collection with no writes
between them, and `purge_history` validated its config and returned `ok`.

Why behavioural and not static: the first version of this check read `run`'s
source for a ``world_diff=`` keyword. That is wrong in both directions. It misses
delegation — `create_motor` calls `_place_symbol`, which calls `_place_widget`,
which writes — and it would count a tool that merely mentions the keyword on a
branch it never takes. Reading source tells you what a tool says; running it
tells you what it does, and this whole audit exists because those differed.

So: seed a world, synthesize plausible arguments from each tool's own Pydantic
schema, dispatch, and record whether a ``world_diff`` came back. Arguments are
built from field *names* (a field called `page_id` gets the seeded page, one
called `new_page_id` gets a fresh name) because that is the only signal the
schema carries about which entity a field refers to.

Verdicts:

* ``MUTATES``      — returned a ``world_diff``. It can do harm; forbidding it means something.
* ``NO_DIFF``      — succeeded and wrote nothing. Either honest (a read, an export,
                     a runtime command) or the silent-no-op defect. The `claims`
                     column separates them: a tool whose `intended_entities` names
                     an entity but which writes nothing is making a false claim.
* ``BLOCKED:<code>`` — never reached L4. Usually this harness failing to synthesize
                     args the tool accepts, not a defect; re-check by hand.
* ``UNSYNTHESIZABLE`` — could not build arguments at all.

Usage:

    python scripts/audit_tool_mutations.py                 # whole catalogue
    python scripts/audit_tool_mutations.py --forbidden     # only tools the dataset forbids
    python scripts/audit_tool_mutations.py --defects       # only false claims
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import sys
import typing
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _force_utf8_stdout() -> None:
    """Only when run as a script — the tests import this module, and rebinding
    ``sys.stdout`` at import time detaches pytest's capture and errors out every
    test in the file that imports it."""
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from pydantic import BaseModel  # noqa: E402

from agent.tool_registry import build_default_registry  # noqa: E402
from world import (  # noqa: E402
    Alarm,
    Device,
    MockWorld,
    Page,
    Point,
    Widget,
)
from world.models import Deployment, HistoryConfig, Script  # noqa: E402

# Seeded ids. Field names are matched against these, longest suffix first, so
# `source_device_id` and `device_id` both resolve to the seeded device while
# `new_device_id` falls through to the fresh-name branch.
SEED_POINT = "SEED_T1"
SEED_PAGE = "seed_page"
SEED_WIDGET = "seed_widget"
SEED_ALARM = "seed_alarm"
SEED_DEVICE = "seed_device"
SEED_SCRIPT = "seed_script"
SEED_DEPLOY = "seed_deploy"
SEED_GROUP = "seed_group"


def seed_world() -> MockWorld:
    return MockWorld(
        points={
            SEED_POINT: Point(tag=SEED_POINT, type="analog", unit="C", min=0.0, max=100.0),
            "SEED_T2": Point(tag="SEED_T2", type="analog", unit="bar"),
            "SEED_D1": Point(tag="SEED_D1", type="digital"),
        },
        pages={
            SEED_PAGE: Page(
                id=SEED_PAGE,
                name="种子页",
                widgets={
                    SEED_WIDGET: Widget(
                        id=SEED_WIDGET, page_id=SEED_PAGE, type="label",
                        position=(10, 10), size=(100, 40),
                        bindings={"value": SEED_POINT},
                    )
                },
            )
        },
        alarms={SEED_ALARM: Alarm(id=SEED_ALARM, tag="SEED_T2", type="analog",
                                  high_limit=80.0, low_limit=0.0)},
        devices={SEED_DEVICE: Device(id=SEED_DEVICE, name="种子设备", type="pump",
                                     tags=[SEED_POINT])},
        histories={SEED_POINT: HistoryConfig(tag=SEED_POINT, stored_days=365,
                                             stored_samples=100_000)},
        scripts={SEED_SCRIPT: Script(id=SEED_SCRIPT, name="种子脚本",
                                     trigger="on_change", bound_tag=SEED_POINT)},
        deployments={
            SEED_DEPLOY: Deployment(id=SEED_DEPLOY, status="validated"),
            # promote_to_environment reads the *source* environment by name, so
            # a world keyed only by the generic seed id blocks at L3 and the
            # audit learns nothing about whether it can write.
            "dev": Deployment(id="dev", target="dev", status="deployed"),
            "staging": Deployment(id="staging", target="staging", status="validated"),
        },
    )


#: field-name suffix -> seeded value. Order matters: first match wins.
NAME_HINTS: list[tuple[tuple[str, ...], object]] = [
    (("page_id", "page"), SEED_PAGE),
    (("widget_id", "widget"), SEED_WIDGET),
    (("alarm_id", "alarm"), SEED_ALARM),
    (("device_id", "device"), SEED_DEVICE),
    (("script_id", "script"), SEED_SCRIPT),
    (("deployment_id",), SEED_DEPLOY),
    (("group_name", "group_id", "group"), SEED_GROUP),
    (("tags",), [SEED_POINT]),
    (("tag", "point", "source_tag", "bound_tag"), SEED_POINT),
]

#: A field whose name starts with one of these refers to something that must NOT
#: already exist, so it gets a fresh id instead of a seeded one.
FRESH_PREFIXES = ("new_", "target_", "dest_", "clone_")


def _fresh(counter: collections.Counter, stem: str) -> str:
    counter[stem] += 1
    return f"fresh_{stem}_{counter[stem]}"


def _literal_values(annotation: object) -> list | None:
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return list(typing.get_args(annotation))
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)):
        for arg in typing.get_args(annotation):
            values = _literal_values(arg)
            if values:
                return values
    return None


def _scalar_for(annotation: object) -> object:
    """A plausible value for a field with no name hint and no default.

    Containers are tested *before* scalars: ``str(tuple[int, int])`` contains
    "int", so a scalar-first order silently handed ``1`` to every position and
    size field and every graphics tool came back UNSYNTHESIZABLE.
    """
    values = _literal_values(annotation)
    if values:
        return values[0]
    text = str(annotation)
    if "tuple" in text or "Tuple" in text:
        return (10, 10)
    if "dict" in text or "Dict" in text:
        return {"stub": "1"}
    if "list" in text or "List" in text:
        return [SEED_POINT] if "str" in text else [1]
    if "bool" in text:
        return True
    if "int" in text:
        return 1
    if "float" in text:
        return 1.0
    return "x"


#: A bare ``id`` field means "the entity of this tool's own domain".
DOMAIN_IDS: dict[str, str] = {
    "manage_pages": SEED_PAGE,
    "manage_graphics": SEED_PAGE,
    "manage_alarms": SEED_ALARM,
    "manage_scripts": SEED_SCRIPT,
    "manage_devices": SEED_DEVICE,
    "manage_points": SEED_POINT,
    "manage_history": SEED_POINT,
    "deployment": SEED_DEPLOY,
}

#: Verbs whose target must *not* already exist, or the tool answers ALREADY_EXISTS
#: instead of reaching L4.
CREATING = ("create_", "add_", "batch_create_", "clone_", "duplicate_")


def synthesize(model: type[BaseModel], counter: collections.Counter,
               *, tool: str = "", domain: str = "", fill_optional: bool = False) -> dict:
    """Build arguments that reach L4 for *tool*.

    The distinction that matters is between a field naming the entity the tool
    *creates* and one naming an entity it *reads*. ``create_analog_alarm`` takes
    both: ``id`` must be new, ``tag`` must already exist. Getting this wrong does
    not show up as a bad audit row, it shows up as ``ALREADY_EXISTS`` — a verdict
    that says nothing about whether the tool can mutate.
    """
    creating = tool.startswith(CREATING)
    args: dict = {}
    for name, field in model.model_fields.items():
        if name == "action":
            continue
        if name.startswith(FRESH_PREFIXES):
            args[name] = _fresh(counter, name)
            continue
        # The entity this tool brings into being.
        if creating and (
            name == "id"
            or (name == "tag" and domain == "manage_points")
            or (name == "widget_id" and domain in ("manage_pages", "manage_graphics"))
        ):
            args[name] = _fresh(counter, name)
            continue
        if name == "id":
            args[name] = DOMAIN_IDS.get(domain, "x")
            continue
        # A digital alarm needs a digital point; the analog seed is a TYPE_MISMATCH.
        if name == "tag" and "digital" in tool:
            args[name] = "SEED_D1"
            continue
        hinted = False
        for suffixes, value in NAME_HINTS:
            if name in suffixes or name.endswith(suffixes):
                args[name] = value
                hinted = True
                break
        if hinted:
            continue
        if field.is_required() or fill_optional:
            args[name] = _scalar_for(field.annotation)
        # optional-with-default fields are left alone on the first pass: the
        # default is what the planner would send, and overriding it would test a
        # path nobody takes
    return args


def audit() -> list[dict]:
    registry = build_default_registry()
    counter: collections.Counter = collections.Counter()
    rows: list[dict] = []
    for meta in sorted(registry.all_atomics(), key=lambda m: m.name):
        world = seed_world()
        row = {"tool": meta.name, "domain": meta.domain,
               "synthesized": type(meta.handler).__name__.startswith("Dynamic")}
        parsed = None
        detail = ""
        # Second pass fills the optional fields too. Some models carry a
        # validator that needs one of a set of nominally-optional arguments —
        # create_analog_alarm wants a limit — and skipping them reads as an
        # unsynthesizable schema when it is just an under-specified call.
        for fill_optional in (False, True):
            try:
                raw = synthesize(meta.args_model, counter, tool=meta.name,
                                 domain=meta.domain, fill_optional=fill_optional)
                parsed = meta.args_model(**raw)
                break
            except Exception as exc:  # noqa: BLE001 - any schema shape may fail here
                detail = type(exc).__name__
        if parsed is None:
            row["verdict"] = "UNSYNTHESIZABLE"
            row["detail"] = detail
            row["claims"] = None
            rows.append(row)
            continue
        try:
            claims = bool(meta.handler.intended_entities(parsed))
        except Exception:  # noqa: BLE001
            claims = None
        row["claims"] = claims
        try:
            result = meta.handler.run(parsed, world)
        except Exception as exc:  # noqa: BLE001
            row["verdict"] = "RAISED"
            row["detail"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
            continue
        if not result.ok:
            row["verdict"] = f"BLOCKED:{result.error_code}"
        elif result.world_diff:
            row["verdict"] = "MUTATES"
        else:
            row["verdict"] = "NO_DIFF"
        rows.append(row)
    return rows


def forbidden_counts() -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    path = REPO / "eval" / "golden_dataset.jsonl"
    for line in path.open(encoding="utf-8"):
        for tool in json.loads(line)["expected_trajectory"].get("forbidden_tools") or []:
            counts[tool] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forbidden", action="store_true",
                    help="only tools the golden dataset names in forbidden_tools")
    ap.add_argument("--defects", action="store_true",
                    help="only tools that claim an entity but write nothing")
    args = ap.parse_args()

    rows = audit()
    forbidden = forbidden_counts()

    if args.forbidden:
        rows = [r for r in rows if r["tool"] in forbidden]
    if args.defects:
        rows = [r for r in rows if r["verdict"] == "NO_DIFF" and r["claims"]]

    rows.sort(key=lambda r: (-forbidden.get(r["tool"], 0), r["tool"]))

    print(f"{'tool':30} {'domain':22} {'verdict':22} {'claims':>7} {'forbidden in':>12}")
    for r in rows:
        n = forbidden.get(r["tool"], 0)
        print(f"{r['tool']:30} {r['domain']:22} {r['verdict']:22} "
              f"{str(r['claims']):>7} {(str(n) + ' cases') if n else '':>12}")

    verdicts = collections.Counter(r["verdict"].split(":")[0] for r in rows)
    print(f"\n{len(rows)} tools: " + ", ".join(f"{k}={v}" for k, v in verdicts.most_common()))

    if not (args.forbidden or args.defects):
        all_rows = audit()
        harmful = {r["tool"] for r in all_rows if r["verdict"] == "MUTATES"}
        inert_forbidden = [(t, n) for t, n in forbidden.most_common() if t not in harmful]
        print(f"\nForbidden by the golden dataset but not observed to mutate "
              f"({len(inert_forbidden)} of {len(forbidden)}):")
        for tool, n in inert_forbidden:
            verdict = next((r["verdict"] for r in all_rows if r["tool"] == tool), "?")
            print(f"  {tool:28} {n:3} cases   {verdict}")
    return 0


if __name__ == "__main__":
    _force_utf8_stdout()
    raise SystemExit(main())
