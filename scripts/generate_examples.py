"""LLM-driven Tool-example generator.

For each atomic tool in the registry, ask the real LLM (xiaomi-mimo by
default) to author 5~8 short natural-language examples in mixed Chinese /
English that would plausibly trigger *exactly that action*. Results are
written to a versionable JSON file that the Tool-RAG corpus loader can
optionally merge into the per-tool ``examples`` field.

This is the Phase-2 §2.4 deliverable "scripts/generate_examples.py: 用 LLM
为每个 Tool 生成 5~10 条自然语言示例".

Design notes
============

1. **Sidecar, not source-edit.** We do *not* rewrite tools/*.py — instead we
   produce ``indices/generated_examples.json`` keyed by atomic name. The
   loader (`tools.examples_loader.load_generated_examples`) merges them into
   ``ToolMeta.examples`` at registry-construction time when the file exists.

2. **Idempotent.** Re-running the script reads the existing JSON first and
   skips tools that already have entries unless ``--force`` is passed.
   ``--retry-empty`` re-queries only tools whose entry is an empty list.

3. **Deterministic-ish.** ``temperature=0.0`` for reproducibility. The
   sidecar JSON is sorted by tool name and pretty-printed so diffs stay
   small.

4. **Provider-agnostic.** Uses the OpenAI-compatible client builder so any
   ``XIAOMI-MIMO_API_URL`` / ``XIAOMI-MIMO_API_KEY``-shaped endpoint works.

Usage
=====

::

    venv/Scripts/python -m scripts.generate_examples            # full sweep
    venv/Scripts/python -m scripts.generate_examples --limit 3  # smoke-test 3 tools
    venv/Scripts/python -m scripts.generate_examples --force    # overwrite all
    venv/Scripts/python -m scripts.generate_examples --tool create_analog_alarm
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.llm import _env, _load_dotenv_into_environ
from agent.tool_registry import ToolMeta, build_default_registry


DEFAULT_OUT = Path("indices/generated_examples.json")
DEFAULT_N = 6
DEFAULT_MODEL_ENV = ("XIAOMI-MIMO_MODEL", "XIAOMI_MIMO_MODEL")
DEFAULT_KEY_ENV = ("XIAOMI-MIMO_API_KEY", "XIAOMI_MIMO_API_KEY")
DEFAULT_URL_ENV = ("XIAOMI-MIMO_API_URL", "XIAOMI_MIMO_API_URL")


# ============================================================ prompt
_SYSTEM_PROMPT = """\
你是一位工业 SCADA 自动化领域的资深工程师,正在为一个 Agent 测试数据集准备
"自然语言 → 工具调用" 的训练样例。请严格遵守:

1. 只针对给定的 *具体动作*(action)生成示例。不要为同一 domain 下的其它 action
   生成 —— 例如要求生成 create_analog_alarm 的示例时,绝不能写"删除报警"。
2. 中英混合,贴近真实现场用户口吻:既有标准术语(高温报警、点位绑定),也有
   口语化表达(温度高了报警一下、给它配个上限)、行业黑话、以及简短英文指令。
3. 每条示例 ≤ 35 字符(中英文均如此),避免长篇大论。
4. 表达多样:不要把同一句话换几个词反复出现;参数(阈值数值、tag 名等)可
   缺省、可具体,但不要造跨领域信息。
5. 输出**严格 JSON**:`{"examples": ["...", "...", ...]}`,不要任何解释文字。
"""


@dataclass
class _ToolPrompt:
    name: str
    domain: str
    action: str
    description: str
    schema_props: list[str]
    seed_examples: list[str]


def _build_tool_prompt(m: ToolMeta) -> _ToolPrompt:
    schema = m.args_model.model_json_schema()
    props = list((schema.get("properties") or {}).keys())
    return _ToolPrompt(
        name=m.name,
        domain=m.domain,
        action=m.action,
        description=m.description,
        schema_props=props,
        seed_examples=list(m.examples or []),
    )


def _user_message(tp: _ToolPrompt, n: int) -> str:
    seed_block = (
        "\n现有示例(避免重复):\n" + "\n".join(f"- {e}" for e in tp.seed_examples)
        if tp.seed_examples
        else ""
    )
    props_block = (
        "\n参数字段(供参考,不要把字段名直接抄进示例):\n- "
        + ", ".join(tp.schema_props)
        if tp.schema_props
        else ""
    )
    return (
        f"目标工具:`{tp.name}`(隶属于 `{tp.domain}`,action=`{tp.action}`)\n"
        f"功能描述:{tp.description}{props_block}{seed_block}\n\n"
        f"请生成 {n} 条多样化的自然语言指令,只输出 JSON。"
    )


# ============================================================ I/O
def _load_existing(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(
            f"[warn] {path} exists but is not valid JSON — starting fresh.",
            file=sys.stderr,
        )
        return {}


def _save(path: Path, data: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8")


# ============================================================ client
def _make_client() -> tuple[Any, str]:
    """Return (openai client, model_name)."""
    _load_dotenv_into_environ()
    api_key = _env(*DEFAULT_KEY_ENV)
    base_url = _env(*DEFAULT_URL_ENV)
    if not api_key or not base_url:
        raise SystemExit(
            "Missing XIAOMI-MIMO_API_KEY / XIAOMI-MIMO_API_URL in environment "
            "or .env — cannot call the real LLM."
        )
    model = _env(*DEFAULT_MODEL_ENV) or "mimo-v2.5-pro"
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model


def _extract_json_examples(text: str) -> list[str]:
    """Pull the ``examples`` list out of the model's reply.

    The system prompt asks for strict JSON, but real models sometimes wrap it
    in ``` fences or add a stray sentence — be lenient.
    """
    if not text:
        return []
    s = text.strip()
    if s.startswith("```"):
        # strip code fences
        s = s.strip("`")
        # If language tag like "json\n..." is present, drop the first line
        if "\n" in s:
            first, rest = s.split("\n", 1)
            if first.strip().lower() in {"json", ""}:
                s = rest
    # First try plain json.loads
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        # Fall back: find first { ... } block
        start = s.find("{")
        end = s.rfind("}")
        if start < 0 or end < 0 or end < start:
            return []
        try:
            obj = json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return []
    if not isinstance(obj, dict):
        return []
    examples = obj.get("examples")
    if not isinstance(examples, list):
        return []
    out: list[str] = []
    for e in examples:
        if isinstance(e, str):
            cleaned = e.strip()
            if cleaned:
                out.append(cleaned)
    return out


def _query_one(
    client: Any,
    model: str,
    tp: _ToolPrompt,
    *,
    n: int,
    temperature: float,
    max_tokens: int,
) -> tuple[list[str], dict[str, int]]:
    """Return (examples, usage_dict)."""
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _user_message(tp, n)},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    latency = (time.perf_counter() - t0) * 1000
    msg = resp.choices[0].message
    text = msg.content or ""
    examples = _extract_json_examples(text)
    usage = getattr(resp, "usage", None)
    return examples, {
        "in": getattr(usage, "prompt_tokens", 0) or 0,
        "out": getattr(usage, "completion_tokens", 0) or 0,
        "latency_ms": int(latency),
    }


# ============================================================ main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate natural-language examples for each atomic tool."
    )
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT), help=f"Output JSON path (default: {DEFAULT_OUT})"
    )
    parser.add_argument(
        "--n", type=int, default=DEFAULT_N, help=f"Examples per tool (default: {DEFAULT_N})"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Only process the first N tools (0 = all)"
    )
    parser.add_argument(
        "--tool", action="append", default=[], help="Restrict to specific atomic names (repeatable)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing entries"
    )
    parser.add_argument(
        "--retry-empty",
        action="store_true",
        help="Re-query tools whose existing entry is an empty list",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be queried; do not call the LLM",
    )
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    existing = _load_existing(out_path)

    registry = build_default_registry()
    atomics = registry.all_atomics()
    if args.tool:
        wanted = {t.strip() for t in args.tool}
        atomics = [a for a in atomics if a.name in wanted]
        missing = wanted - {a.name for a in atomics}
        if missing:
            print(f"[warn] unknown atomic names ignored: {sorted(missing)}", file=sys.stderr)
    if args.limit > 0:
        atomics = atomics[: args.limit]

    if not atomics:
        print("[done] nothing to do.")
        return 0

    queue: list[ToolMeta] = []
    for a in atomics:
        prev = existing.get(a.name)
        if args.force:
            queue.append(a)
        elif prev is None:
            queue.append(a)
        elif args.retry_empty and isinstance(prev, list) and len(prev) == 0:
            queue.append(a)

    print(
        f"[setup] target={len(atomics)} atomics  "
        f"queue={len(queue)} (force={args.force}, retry_empty={args.retry_empty})"
    )
    if args.dry_run:
        for a in queue:
            print(f"  · would query {a.name}  ({a.domain}/{a.action})")
        print("[dry-run] no API calls made.")
        return 0
    if not queue:
        print("[done] all targeted tools already have entries; nothing to do.")
        return 0

    client, model = _make_client()
    print(f"[setup] model={model}  out={out_path}")
    print()

    total_in = 0
    total_out = 0
    total_latency = 0
    failures: list[tuple[str, str]] = []
    for i, m in enumerate(queue, start=1):
        tp = _build_tool_prompt(m)
        try:
            examples, usage = _query_one(
                client,
                model,
                tp,
                n=args.n,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            )
        except Exception as exc:
            failures.append((m.name, str(exc)))
            print(f"  [{i:2d}/{len(queue)}] {m.name:<28s} ERROR: {exc}")
            continue
        total_in += usage["in"]
        total_out += usage["out"]
        total_latency += usage["latency_ms"]
        existing[m.name] = examples
        # Persist after every tool so a mid-run crash doesn't lose progress.
        _save(out_path, existing)
        head = examples[0] if examples else "<empty>"
        print(
            f"  [{i:2d}/{len(queue)}] {m.name:<28s} "
            f"got={len(examples):2d}  in={usage['in']:5d} out={usage['out']:4d} "
            f"lat_ms={usage['latency_ms']:5d}  e.g. {head!r}"
        )

    print()
    print(
        f"[done] {len(queue) - len(failures)}/{len(queue)} succeeded  "
        f"total_in={total_in}  total_out={total_out}  "
        f"total_latency_ms={total_latency}  saved → {out_path}"
    )
    if failures:
        print("[done] failures:")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
