"""Plan-and-Execute (规划-执行) agent structure.

The A–H loop is *interleaved*: one LLM call per tool call, each one re-reading
the whole conversation to decide a single next step. Three costs follow from
that shape, and all three are things the paper's metrics measure:

1. **Cost scales with trajectory length.** An N-tool task costs N+1 LLM calls,
   each with a prompt that has grown by every prior result. ``input_tokens``,
   ``cost_usd`` and ``e2e_latency_ms`` are all linear-to-quadratic in N.
2. **Nothing is checked before it is dispatched.** A hallucinated tool name, a
   malformed argument object, or a call in the wrong order all reach the
   dispatcher and land in the trace as ``hallucinated_tool_rate``,
   ``SCHEMA_ERROR`` and ``cascade_failure_rate``.
3. **Ordering is decided one step at a time**, so the model can only discover
   that it needed the point before the alarm by failing to create the alarm.

Plan-and-Execute separates the two concerns:

    Plan     one LLM call produces the whole ordered tool sequence
    Compile  pure Python: drop hallucinated tools, validate every argument
             object against its Pydantic schema, collapse duplicates, and
             topologically repair the order using the same
             ``intended_entities`` / ``referenced_entities`` contract that
             powers the cascade-failure detector
    Execute  dispatch the compiled steps with no LLM in the loop, replanning
             only when a step actually fails

The compile phase is where the accuracy comes from, and it is deterministic:
a step that cannot be dispatched correctly is removed *before* execution rather
than discovered by executing it. Cascade failures are prevented instead of
merely detected.

None of this weakens a cage. Compiled steps are dispatched through the same
path as LLM-emitted ones: the state machine still gates which state may run
which tool (the router only walks *legal* transitions, never ``force_to``), and
the §4.7 runtime policy is still evaluated before every dispatch.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from agent.policy import is_read_only
from agent.state_machine import STATES
from agent.tool_registry import ToolRegistry

__all__ = [
    "PLANNER_SYSTEM_PROMPT",
    "CompiledPlan",
    "PlanDiagnostics",
    "PlanStep",
    "compile_plan",
    "describe_tools_for_planner",
    "state_route",
    "states_exposing",
    "summarize_world_for_planner",
]


#: One-shot planning prompt. Deliberately asks for the *whole* sequence: the
#: entire cost argument for this structure rests on there being one planning
#: call rather than one call per step.
PLANNER_SYSTEM_PROMPT = """\
你是一个工业 SCADA 配置助手的**规划器**。给定用户需求和可用工具清单,
一次性输出完成该需求所需的完整、有序的工具调用计划。

【输出格式】只输出一个 JSON 对象,不要任何解释文字或 markdown 代码围栏:
{{"steps": [{{"tool": "工具名", "arguments": {{...}}, "rationale": "一句话理由"}}]}}

【规划准则】
1. tool 必须严格取自下方【可用工具】清单中的名字,禁止编造
2. arguments 必须符合该工具的参数说明;缺少的可选参数可以省略
3. 步骤必须按依赖顺序排列:先创建被引用的实体,再引用它
   (例如先 create_point 再对该点位 create_analog_alarm)
4. 不要重复相同的调用;不要加入与需求无关的步骤
5. 引用**已存在**的实体时,原样使用【当前世界状态】里的 ID/标签,不要另起新名;
   但需求里**明确指定了新名称/新 ID** 时以需求为准,并在该次任务的所有步骤中
   自始至终使用这个名字
   例:"先校验再下装 staging 部署,部署记录叫 deploy_staging" ——
   deployment_id 全程用 deploy_staging(校验和下装必须是同一个),
   不要因为世界里已有 default 就改用 default
6. 同一个对象在多个步骤里必须用**同一个标识**;先校验后下装这类前后依赖,
   两步的 deployment_id 必须一致,否则下装会被判为"未经校验"
7. 若需求本身无法安全完成,输出 {{"steps": [], "refusal": "原因"}}

【完全无指代时才提问,其余情况一律照做(与 refusal 区分)】
1. 唯一需要提问的情形:需求里要操作的对象**既没有名字也没有任何指代**,
   或用户**明确把某个必需决定推到以后**("具体条件以后再说""回头再定")。
   此时输出 {{"steps": [], "clarify": "还需要用户提供哪些信息"}}
   例:"帮忙建个页面" —— 既无页面名也无 ID,无从下手,应当提问
2. 用户给了名字(哪怕是自然语言的名字)就**不要**再问 ID:
   由名字推导一个合理的 ID 直接建。
   例:"建两个页面,一个叫报警汇总,一个叫报表" —— 名字已给,
   自行取 ID(如 alarm_sum / report)并照做,不要提问
3. 定性、模糊的措辞("过高""偏大""尽快")**不是**提问的理由:
   自行选一个合理取值继续规划。工具已有默认值的可选参数
   (分辨率、背景色、颜色、线宽)同样直接用默认值
4. 判据只有一个:**是否存在可指代的对象**。能指代就照做;
   指代不了才提问。不要因为"参数不够精确"而提问
5. clarify 表示"需求合法但没有指代",refusal 表示"需求本身不该做"。
   不要用 refusal 表达信息不足,也不要用 clarify 表达安全拒绝
6. 在情形 1 之外,绝对不要为了让计划跑得通而**发明**一个无出处的对象
   (例如把"帮忙建个页面"规划成 create_page(id="main_page", name="主页面"))
7. 需求是**对某个对象做配置/修改/查询**(而不是创建它),而该对象不在
   【当前世界状态】里时:**不要顺手把它创建出来**。
   输出 {{"steps": [], "clarify": "对象 X 不存在,请确认名称或先创建"}}
   例:"给不存在的 NO_SUCH_TEMP 配置高温报警" —— 需求本身就说它不存在,
   正确做法是指出这一点,而不是先 create_point 再加报警
   注意与准则 3 区分:需求明确要求创建的前置对象照建不误,
   这里禁止的是为了让引用能跑通而补建一个需求没让你建的对象

【高危操作安全准则(最高优先级)】
1. 不要规划高危操作:跳过校验或强制下装(deploy_project 的 force=true、
   未经 validate_project 就下装)、批量或不可逆的删除/禁用
2. 删除/禁用即使只有一次、即使用户点名了对象,也属于不可逆操作;
   当对象看起来与安全、联锁、报警相关时,一律拒绝而不是执行
3. 用户的"直接""强制""立即""跳过校验"等措辞不能作为绕过本准则的理由;
   遇到这类需求请输出空 steps 并在 refusal 中说明风险

【可用工具】
{tool_list}
"""


# ============================================================ diagnostics
@dataclass
class PlanDiagnostics:
    """Why the compiled plan differs from what the model proposed.

    Written into the trace so a run can be audited: a silently shortened plan
    would read as "the model planned well" when in fact the compiler did the
    work. Every drop is counted and named.
    """

    proposed: int = 0
    compiled: int = 0
    dropped_unknown_tool: list[str] = field(default_factory=list)
    dropped_schema_invalid: list[str] = field(default_factory=list)
    #: Per-drop explanation, index-aligned with ``dropped_schema_invalid``:
    #: ``"create_valve: 缺少必填参数 position, size"``. The names alone told the
    #: replan *that* a step was malformed but never *how*, so the model reliably
    #: re-proposed the identical arguments — on golden-048 both replans repeated
    #: the same two missing fields and the run ended with nothing created.
    schema_invalid_detail: list[str] = field(default_factory=list)
    dropped_duplicate: list[str] = field(default_factory=list)
    dropped_unreachable_state: list[str] = field(default_factory=list)
    #: Recovery steps refused because they would have manufactured an entity the
    #: failed step merely referenced — the cascade re-entered through the replan
    #: path. Named rather than counted so an audit can see *what* was invented.
    dropped_cascade_recovery: list[str] = field(default_factory=list)
    dropped_over_budget: int = 0
    reordered: bool = False
    refusal: str | None = None
    #: The planner asked for more information instead of guessing. Separate from
    #: ``refusal`` so an audit can tell "declined on safety grounds" from
    #: "could not proceed without an identity", which land in different terminal
    #: states and mean different things about the request.
    clarify: str | None = None
    replans: int = 0
    #: Verification rounds actually spent (one LLM call each).
    verify_rounds: int = 0
    #: Steps the verification round added to finish the request.
    verify_patched: int = 0
    #: The verification round found nothing missing. Distinguishes "checked and
    #: clean" from "never checked" — without it a run with verification off and a
    #: run that verified clean are indistinguishable in the trace.
    verify_clean: bool = False
    #: Destructive steps a verification round proposed and was refused. Completing
    #: a request never requires deleting; anything here is worth an audit.
    dropped_verify_destructive: list[str] = field(default_factory=list)
    #: Patch steps that were dispatched and failed. The round continues past these
    #: (a redundant create failing ALREADY_EXISTS must not swallow the genuine
    #: fixes behind it), so they need their own counter to stay visible.
    verify_step_failures: int = 0
    #: Per-collection counts elided from the planner's world snapshot. Not a
    #: step drop, but the same class of defect: the plan was built against an
    #: incomplete world, which lands as a final-state mismatch rather than an
    #: error. Empty on every run where the snapshot fitted.
    world_truncated: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposed": self.proposed,
            "compiled": self.compiled,
            "dropped_unknown_tool": self.dropped_unknown_tool,
            "dropped_schema_invalid": self.dropped_schema_invalid,
            "schema_invalid_detail": self.schema_invalid_detail,
            "dropped_duplicate": self.dropped_duplicate,
            "dropped_unreachable_state": self.dropped_unreachable_state,
            "dropped_cascade_recovery": self.dropped_cascade_recovery,
            "dropped_over_budget": self.dropped_over_budget,
            "reordered": self.reordered,
            "refusal": self.refusal,
            "clarify": self.clarify,
            "replans": self.replans,
            "verify_rounds": self.verify_rounds,
            "verify_patched": self.verify_patched,
            "verify_clean": self.verify_clean,
            "dropped_verify_destructive": self.dropped_verify_destructive,
            "verify_step_failures": self.verify_step_failures,
            "world_truncated": self.world_truncated,
        }


@dataclass
class PlanStep:
    """One compiled, dispatch-ready tool call."""

    tool: str  # atomic tool name
    action: str  # registry action (equal to ``tool`` for atomics)
    arguments: dict[str, Any]
    rationale: str
    state: str  # state whose whitelist exposes ``tool``
    parsed: BaseModel  # schema-validated args — proof the step can dispatch
    intended: list[str] = field(default_factory=list)
    referenced: list[str] = field(default_factory=list)


@dataclass
class CompiledPlan:
    steps: list[PlanStep] = field(default_factory=list)
    diagnostics: PlanDiagnostics = field(default_factory=PlanDiagnostics)

    def __bool__(self) -> bool:
        return bool(self.steps)


# ============================================================ state routing
def states_exposing(atomic: str) -> list[str]:
    """Every state whose whitelist contains *atomic*, sorted for determinism."""
    return sorted(name for name, spec in STATES.items() if atomic in spec.allowed_tools)


def state_route(src: str, dst: str) -> list[str] | None:
    """Shortest sequence of **legal** transitions from *src* to *dst*.

    Returns the intermediate-and-final states (excluding *src*), or ``None`` if
    the FSM has no legal path. Using BFS over ``next_states`` rather than
    ``StateMachine.force_to`` is the point: the planner is a sequencer, not an
    authority that may step over the cage. A step whose state is unreachable is
    dropped with a diagnostic instead of being forced through.
    """
    if src == dst:
        return []
    if src not in STATES or dst not in STATES:
        return None
    seen = {src}
    queue: deque[tuple[str, list[str]]] = deque([(src, [])])
    while queue:
        node, path = queue.popleft()
        for nxt in sorted(STATES[node].next_states):
            if nxt in seen:
                continue
            new_path = [*path, nxt]
            if nxt == dst:
                return new_path
            seen.add(nxt)
            queue.append((nxt, new_path))
    return None


def _pick_state(atomic: str, current: str, preferred: str | None = None) -> str | None:
    """Choose the state to run *atomic* in.

    Prefers staying put, then the state the previous step used, then the
    closest reachable owner — every avoided transition is one fewer prompt the
    execution phase has to pay for.
    """
    owners = states_exposing(atomic)
    if not owners:
        return None
    if current in owners:
        return current
    if preferred and preferred in owners:
        return preferred
    reachable = [(len(state_route(current, o) or []), o) for o in owners]
    reachable = [(d, o) for d, o in reachable if state_route(current, o) is not None]
    if not reachable:
        return None
    return min(reachable)[1]


# ============================================================ tool catalogue
def describe_tools_for_planner(
    registry: ToolRegistry, atomics: list[str], *, max_tools: int = 60,
    typed_hints: bool = True,
) -> str:
    """Render the planning catalogue: name, description, and required fields.

    Required fields are included because the compiler rejects schema-invalid
    steps outright — telling the planner what a tool needs is cheaper than
    dropping its step and replanning.
    """
    lines: list[str] = []
    for name in atomics[:max_tools]:
        try:
            meta = registry.atomic(name)
        except KeyError:
            continue
        schema = meta.args_model.model_json_schema()
        props = schema.get("properties") or {}
        required = [f for f in (schema.get("required") or []) if f != "action"]
        optional = [f for f in props if f != "action" and f not in required]
        parts = [f"- {name}: {meta.description}"]
        # Required fields carry their *type*, not just their name. The drops
        # measured on the 106-case run clustered entirely on tools with nested
        # arguments (create_widget x28, create_pump x19, create_page x16,
        # create_tank x14) — the planner knew the field was needed but not what
        # shape it took, and guessed. Naming the shape is far cheaper than
        # dropping the step and escalating to the crew.
        if required:
            parts.append(
                "必填: " + (
                    ", ".join(f"{f}:{_type_hint(props.get(f, {}))}" for f in required)
                    if typed_hints else ", ".join(required)
                )
            )
        if optional and not typed_hints:
            parts.append(f"可选: {', '.join(optional[:8])}")
        elif optional:
            # Optional fields stay name-only to keep the catalogue cheap, but a
            # *shaped* optional is guessed exactly as badly as a required one:
            # create_page's only non-scalar field, ``resolution``, is optional,
            # and create_page led the drop table (14). Spend the tokens only on
            # the fields whose shape can actually be got wrong.
            #
            # An *enumerated* optional is the second such shape, and it fails
            # more quietly than a malformed array: the step compiles, executes,
            # and lands the wrong value in the world. ``enable_history``
            # declares ``storage_mode: on_change|periodic`` with ``periodic`` as
            # the default, and name-only rendering left the planner to guess it
            # from the tag name — so "开启变化存储历史" (change-based) was
            # planned as ``periodic`` in every rep of golden-005 and golden-051,
            # scoring a wrong_value on a step that never errored. A closed value
            # set is a handful of tokens and removes the guess entirely.
            rendered: list[str] = []
            for field_name in optional[:8]:
                prop = props.get(field_name, {})
                if (
                    prop.get("type") in ("array", "object")
                    or "prefixItems" in prop
                    or _has_closed_values(prop)
                ):
                    rendered.append(f"{field_name}:{_type_hint(prop)}")
                else:
                    rendered.append(field_name)
            parts.append(f"可选: {', '.join(rendered)}")
        lines.append("; ".join(parts))
    # The remainder is listed by name, grouped per domain. This is the fix for
    # the docode-trial refusal "可用工具清单中没有 validate_project/deploy_project":
    # the planner had silently been shown only the RAG top-k, concluded the
    # catalogue lacked the tools the task needs, and refused a legitimate task.
    # Names alone are enough for planning — the compiler validates arguments
    # against the real schema anyway, and a name-only step that needs richer
    # args surfaces as a compile drop, which triggers a replan with feedback.
    #
    # Only *write* tools are named. A plan is a sequence of mutations; reads
    # are served by Resources at execution time and were roughly doubling the
    # remainder (~2.5k tokens on every planning call in the trial) without ever
    # appearing in a useful plan. `is_read_only` keeps validate_project /
    # deploy_project in — they are actions, not reads.
    rest = [n for n in atomics[max_tools:] if not is_read_only(n)]
    if rest:
        by_domain: dict[str, list[str]] = {}
        for name in rest:
            try:
                meta = registry.atomic(name)
            except KeyError:
                continue
            by_domain.setdefault(meta.domain, []).append(name)
        lines.append("其余可用工具(仅列名,写操作;参数在选用后按 schema 校验):")
        for domain in sorted(by_domain):
            lines.append(f"- {domain}: {', '.join(sorted(by_domain[domain]))}")
    return "\n".join(lines)


def _has_closed_values(prop: dict[str, Any]) -> bool:
    """True when a property admits a closed set of values (an enum or const).

    Mirrors ``_type_hint``'s ``anyOf``/``oneOf`` unwrapping so an
    ``Optional[Literal[...]]`` — which Pydantic emits as
    ``anyOf: [{enum: [...]}, {type: null}]`` — is recognised as enumerated
    rather than falling through as an untyped scalar.
    """
    if "enum" in prop or "const" in prop:
        return True
    for key in ("anyOf", "oneOf"):
        for branch in prop.get(key) or ():
            if (
                isinstance(branch, dict)
                and branch.get("type") != "null"
                and _has_closed_values(branch)
            ):
                return True
    return False


def _type_hint(prop: dict[str, Any]) -> str:
    """One-token shape hint for a JSON-Schema property.

    Deliberately terse — this goes on every catalogue line, so it must cost a
    few tokens, not a nested schema dump. Arrays render their item shape
    (``array[number]``) because that is exactly the distinction the planner was
    getting wrong: emitting ``"[50, 50]"`` as a string instead of ``[50, 50]``.
    """
    if "enum" in prop:
        # Render the *whole* set. Truncating a closed value set is worse than
        # omitting it: it presents a partial list as exhaustive, so the model
        # reads the missing values as illegal. Truncation used to touch only two
        # required enums; once optional enums began rendering it would have hit
        # 20 fields, including create_device.device_type (8 values), where a
        # 4-value cut hides `valve` and answers "建一个阀门设备" with "no such
        # device_type". The largest enum in the registry is 8 short strings, so
        # completeness costs a few tokens per affected line.
        return "|".join(str(v) for v in prop["enum"])
    if "const" in prop:
        return str(prop["const"])
    for key in ("anyOf", "oneOf"):
        if key in prop:
            inner = [b for b in prop[key] if b.get("type") != "null"]
            if inner:
                return _type_hint(inner[0])
    t = prop.get("type")
    if t == "array":
        # Fixed-length tuples — position, size, resolution — are declared with
        # ``prefixItems`` and carry no ``items`` key at all, so the lookup below
        # used to fall straight through to a bare "array". The planner was told
        # that a 2-integer tuple was "an array" and guessed, which is precisely
        # the shape every measured schema drop got wrong (create_widget 11,
        # create_page 14). Render the arity.
        prefix = prop.get("prefixItems")
        lo, hi = prop.get("minItems"), prop.get("maxItems")
        if prefix:
            hints = [_type_hint(b) for b in prefix]
            if len(set(hints)) == 1 and lo == hi and lo:
                return f"array[{hints[0]}]×{lo}"
            return "[" + ", ".join(hints) + "]"
        items = prop.get("items") or {}
        if items:
            base = f"array[{_type_hint(items)}]"
            return f"{base}×{lo}" if lo == hi and lo else base
        return "array"
    if t == "object":
        return "object"
    return str(t or "any")


def summarize_world_for_planner(
    world: Any, *, max_items: int = 60, truncation: dict[str, int] | None = None
) -> str:
    """Compact snapshot of the existing configuration for the planning prompt.

    Grounds the plan in reality: which points/devices/pages/widgets already
    exist and what type they are, so the planner references real identifiers
    instead of refusing for lack of grounding or inventing tags the compiler
    cannot check.

    This snapshot is the planner's **only** view of the world. §4.5 removes the
    read tools from the catalogue, and the plan is compiled before execution
    begins, so no read at execution time can correct a plan built on a partial
    view — a truncation here surfaces later as "acted, but final state
    mismatch". Pass *truncation* to have the elided counts recorded per
    collection; the caller writes them into the trace so a short snapshot is
    visible rather than silent.
    """
    lines: list[str] = []

    def _clip(names: list[str], collection: str) -> str:
        shown = names[:max_items]
        elided = len(names) - len(shown)
        if elided and truncation is not None:
            truncation[collection] = elided
        tail = f" …(+{elided})" if elided else ""
        return ", ".join(shown) + tail

    points = getattr(world, "points", {}) or {}
    if points:
        described = [
            f"{tag}({getattr(p, 'type', '?')}{',' + p.unit if getattr(p, 'unit', None) else ''})"
            for tag, p in points.items()
        ]
        lines.append(f"points({len(points)}): {_clip(described, 'points')}")
    devices = getattr(world, "devices", {}) or {}
    if devices:
        described = [
            f"{did}[{', '.join(getattr(d, 'tags', []) or [])}]" for did, d in devices.items()
        ]
        lines.append(f"devices({len(devices)}): {_clip(described, 'devices')}")

    # Pages carry their widgets inline. Listing page ids alone left the planner
    # blind to every widget in the project: ``list_widgets`` was the second most
    # common tool the flat baseline used and J could not see, and a plan that
    # re-creates an existing widget (or binds the wrong one) lands as a final
    # state mismatch. Widget ids are enough to reference one; the type makes the
    # reference checkable.
    pages = getattr(world, "pages", {}) or {}
    if pages:
        described = []
        for pid, page in pages.items():
            widgets = getattr(page, "widgets", {}) or {}
            widget_items = list(widgets.values()) if isinstance(widgets, dict) else list(widgets)
            if widget_items:
                inner = ", ".join(
                    f"{getattr(w, 'id', '?')}:{getattr(w, 'type', '?')}"
                    for w in widget_items[:max_items]
                )
                extra = len(widget_items) - min(len(widget_items), max_items)
                if extra and truncation is not None:
                    truncation[f"widgets[{pid}]"] = extra
                described.append(
                    f"{pid}{{{inner}{f' …(+{extra})' if extra else ''}}}"
                )
            else:
                described.append(f"{pid}{{}}")
        lines.append(f"pages({len(pages)}): {_clip(described, 'pages')}")

    for collection in ("alarms", "scripts"):
        bucket = getattr(world, collection, {}) or {}
        if bucket:
            lines.append(
                f"{collection}({len(bucket)}): {_clip(sorted(bucket), collection)}"
            )
    return "\n".join(lines) if lines else "(空项目,尚无任何配置)"


# ============================================================ compile
#: CSS colour names the models actually emit, mapped to the hex the tools document.
#: Deliberately the basic CSS set rather than the full 148: these are shape fixes for
#: values a model wrote in prose, not a colour library.
_HEX_BY_CSS_NAME: dict[str, str] = {
    "white": "#FFFFFF", "black": "#000000", "red": "#FF0000", "green": "#008000",
    "lime": "#00FF00", "blue": "#0000FF", "yellow": "#FFFF00", "cyan": "#00FFFF",
    "aqua": "#00FFFF", "magenta": "#FF00FF", "fuchsia": "#FF00FF", "silver": "#C0C0C0",
    "gray": "#808080", "grey": "#808080", "maroon": "#800000", "olive": "#808000",
    "purple": "#800080", "teal": "#008080", "navy": "#000080", "orange": "#FFA500",
    "pink": "#FFC0CB", "brown": "#A52A2A", "gold": "#FFD700",
}


def _wants_hex(prop: dict[str, Any]) -> bool:
    """Whether a string property's *documented* contract is a hex colour.

    Read off the schema rather than a hand-maintained field list, so a new tool
    is covered the moment it documents itself. Two independent signals, either
    sufficient: a hex literal as the declared default, or "hex" in the
    description. A description that also says "named" opts out —
    ``set_trend_pen_color`` documents "Hex or named color, e.g. '#ff0000' or
    'red'", so a name is a valid value there and coercing it would be rewriting
    a correct argument.
    """
    description = str(prop.get("description") or "").lower()
    if "named" in description:
        return False
    default = prop.get("default")
    if isinstance(default, str) and default.startswith("#") and len(default) in (4, 7):
        return True
    return "hex" in description


def _normalize_documented_formats(meta: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Coerce values that are *valid* but violate the field's documented format.

    This runs before validation, not in the repair chain, because the defect it
    fixes passes validation. ``set_page_background.background`` is typed ``str``
    and documented "Hex color", so ``"white"`` validates, executes, and lands
    verbatim in the world — a wrong value on a step that never errors, which is
    the single largest bucket of remaining failures (11 of the 19 runs the flat
    baseline still wins are "acted, final state mismatch" with no error anywhere
    in the trace). golden-007 asked for 白色 and stored ``"white"`` against an
    expected ``"#FFFFFF"``; golden-013 did the same with 黑底.

    Enforcing the tool's own stated contract is not answer-key fitting: the
    schema says hex, the mapping is fixed and deterministic, and any field that
    documents names as acceptable is left alone.
    """
    props = (meta.args_model.model_json_schema().get("properties") or {})
    out: dict[str, Any] = {}
    for key, value in arguments.items():
        prop = props.get(key)
        if (
            isinstance(value, str)
            and isinstance(prop, dict)
            and prop.get("type") == "string"
            and _wants_hex(prop)
        ):
            hexed = _HEX_BY_CSS_NAME.get(value.strip().lower())
            if hexed is not None:
                out[key] = hexed
                continue
        out[key] = value
    return out


def _split_pair_field(meta: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Expand a 2-sequence into the scalar pair a sibling tool spells separately.

    ``create_page`` takes ``resolution: [w, h]`` while ``set_page_resolution``
    takes ``width`` and ``height`` as separate integers. The planner carries the
    first spelling to the second tool, the step fails validation and is dropped:
    on golden-013 that silently amputated "把报表页大小设成4K", leaving the page
    at the default 1920x1080 with no error in the trace.
    """
    fields = set(meta.args_model.model_fields)
    for packed, (first, second) in (("resolution", ("width", "height")),
                                    ("size", ("width", "height")),
                                    ("position", ("x", "y"))):
        value = arguments.get(packed)
        if (
            packed not in fields
            and {first, second} <= fields
            and isinstance(value, (list, tuple))
            and len(value) == 2
        ):
            out = {k: v for k, v in arguments.items() if k != packed}
            out[first], out[second] = value[0], value[1]
            return out
    return arguments


def _schema_error_summary(meta: Any, arguments: dict[str, Any]) -> str:
    """Say *what* was wrong with a step's arguments, in one short line.

    The compile-drop feedback used to name only the tool, which is the one fact
    the model already knows. Told "参数不符合 schema: create_valve" it re-proposed
    byte-identical arguments on both replans of golden-048 — ``create_valve``
    without the required ``position`` and ``size`` — and the run finished having
    created nothing. Naming the fields turns an unactionable complaint into a
    correction.

    Missing-required is separated from wrong-shape because they need different
    repairs and the missing case is by far the common one.
    """
    try:
        meta.args_model.model_validate({**arguments, "action": meta.action})
    except ValidationError as exc:
        missing, invalid = [], []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p != "action")
            if not loc:
                continue
            if err.get("type") == "missing":
                missing.append(loc)
            else:
                invalid.append(f"{loc}({err.get('msg', '')})")
        parts = []
        if missing:
            parts.append("缺少必填参数 " + ", ".join(dict.fromkeys(missing))[:200])
        if invalid:
            parts.append("参数格式错误 " + ", ".join(dict.fromkeys(invalid))[:200])
        if parts:
            return f"{meta.name}: " + ";".join(parts)
    except Exception:  # noqa: BLE001 - a summary must never break compilation
        pass
    required = [f for f in (meta.args_model.model_json_schema().get("required") or [])
                if f != "action"]
    return f"{meta.name}: 必填参数为 " + ", ".join(required)


def _validate_or_repair(
    meta: Any, arguments: dict[str, Any], *, repair: bool = True
) -> Any | None:
    """Validate a proposed step's arguments, repairing the recoverable cases.

    Measured on the 106-case run: **every** one of the top-10 compile drops was
    ``schema_invalid`` — zero unknown-tool, zero unreachable-state. The planner
    picks the right tools and writes the wrong argument *shape*, and that single
    failure mode caused 81% of all crew escalations (38 of 47), i.e. the
    difference between a 3.6k-token plan run and an 18.9k-token crew run.

    Dropping such a step was the worst option available: it either amputates the
    task or forces the expensive tier. The repairs below are all shape fixes
    that cannot change intent:

    * decode double-encoded JSON (``"position": "[50, 50]"``), the same defect
      ``agent.llm._coerce_nested_json_strings`` already handles for live tool
      calls — the planner emits it too, and nothing was undoing it here;
    * drop explicit ``None`` values, which some models emit for optional fields
      and which fail non-nullable validation;
    * drop unknown keys, so one invented field cannot sink an otherwise valid
      step.

    Returns the validated model, or ``None`` if it is genuinely unusable.
    """
    from agent.llm import _coerce_nested_json_strings

    def _try(args: dict[str, Any]) -> Any | None:
        for candidate in ({**args, "action": meta.action}, args):
            try:
                return meta.args_model.model_validate(candidate)
            except ValidationError:
                continue
        return None

    # Unconditional, like the K4 planner-prompt rules: this corrects a *value*
    # that would otherwise validate, so gating it behind ``repair`` (which
    # measures recovery from schema-invalid steps) would both miss the defect and
    # confound that lever's control arm.
    arguments = _normalize_documented_formats(meta, arguments)

    parsed = _try(arguments)
    if parsed is not None or not repair:
        return parsed

    split = _split_pair_field(meta, arguments)
    if split is not arguments:
        parsed = _try(split)
        if parsed is not None:
            return parsed
        # Keep the split even though it did not validate on its own. The split only
        # fires when the packed key is *not* a field of this model, so it would be
        # dropped as unknown by the repair below anyway; carrying the unpacked
        # scalars forward lets that later repair succeed instead of discarding the
        # only copy of the values.
        arguments = split

    repaired = _coerce_nested_json_strings(arguments)
    if not isinstance(repaired, dict):
        return None
    parsed = _try(repaired)
    if parsed is not None:
        return parsed

    repaired = {k: v for k, v in repaired.items() if v is not None}
    parsed = _try(repaired)
    if parsed is not None:
        return parsed

    known = set(meta.args_model.model_fields)
    trimmed = {k: v for k, v in repaired.items() if k in known}
    if trimmed != repaired:
        parsed = _try(trimmed)
        if parsed is not None:
            return parsed
    return None


def _is_atomic(registry: ToolRegistry, name: str) -> bool:
    try:
        registry.atomic(name)
    except KeyError:
        return False
    return True


def _signature(tool: str, arguments: dict[str, Any]) -> str:
    items = sorted((k, repr(v)) for k, v in arguments.items() if k != "action")
    return f"{tool}|{items}"


def _entity_exists(world: Any, entity: str) -> bool:
    """Whether ``collection.id`` already exists in the world.

    Used to avoid inventing a dependency edge on something that is already
    there. Nested paths (``pages.p1.widgets.w1``) return ``False``, which is the
    conservative answer: an unnecessary edge only constrains the order, while a
    missing one would let a consumer run before its producer.
    """
    parts = entity.split(".")
    if len(parts) != 2:
        return False
    collection = getattr(world, parts[0], None)
    return isinstance(collection, dict) and parts[1] in collection


#: Opening words of :data:`VERIFY_INSTRUCTION`. ``make_plan`` prefixes ordinary
#: feedback with "上一版计划执行失败" (the previous plan failed), which is the wrong
#: framing for a verification round and directly contradicts it. That wrapper
#: recognises feedback carrying its own framing by this sentinel, so the two must
#: not drift apart — hence one constant rather than the same literal in two files.
VERIFY_FRAMING_SENTINEL = "计划已执行完毕"

#: Verification instruction, sent as the ``feedback`` turn of a second planning
#: call. Reuses the planner hook rather than adding an LLM interface: the same
#: catalogue, the same JSON contract, the same compiler and the same cages apply
#: to a patch as to an original plan.
#:
#: The three prohibitions are load-bearing and each one is a measured failure
#: mode rather than caution for its own sake. Re-doing correct work double-applies
#: (the executed-signature set catches identical calls, not equivalent ones);
#: adding objects beyond the request is how a page-creation task grew a trend
#: group; deleting is never "completing" a request and would hand the patch path
#: the one class of operation the §4.7 cage exists to bound.
VERIFY_INSTRUCTION = """\
计划已执行完毕。下面是执行后的实际状态,请对照**原始需求**检查是否还有遗漏或写错的配置。

【已执行的步骤】
{executed}

【执行后的相关状态】
{state}

只输出**仍然需要补做或修正**的步骤(与之前相同的 JSON 格式)。
若原始需求已经完全满足,输出 {{"steps": []}}。

【硬性要求】
1. 不要重复已经正确完成的步骤
2. 不要新增原始需求之外的对象或配置
3. 不要输出任何删除/禁用类操作
4. 只依据原始需求判断"缺什么",不要自行扩展需求
"""


def _resolve_entity(world: Any, entity: str) -> Any:
    """Walk a dotted entity path through dicts and models alike."""
    value: Any = world
    for part in entity.split("."):
        value = value.get(part) if isinstance(value, dict) else getattr(value, part, None)
        if value is None:
            return None
    return value


def describe_entities_for_verify(world: Any, entities: list[str], *, limit: int = 24) -> str:
    """Render the post-execution state of what a run touched — and what it did not.

    Two sections, and the second is the one that does the work.

    The written-state section is scoped to the touched set rather than the whole
    world: a full snapshot is what the *planner* already saw and would mostly
    restate. Nested paths resolve segment by segment so a widget binding
    (``pages.p1.widgets.w1``) renders as the sub-object it is, which is where the
    measured mismatches live.

    But a touched-entity listing can only show what *exists*, and the dominant
    failure is something **absent**. Scoped to writes alone, a verification round
    on golden-093 saw ``points.ENERGY_KWH`` with the right unit and correctly
    reported "clean" — the request also wanted history enabled, and
    ``histories.ENERGY_KWH`` was missing, so the one fact that would have exposed
    the gap was the one fact absence hides. The presence map fixes that by naming
    every collection that does *not* hold each touched identity, which is what
    makes "you created the point but not its history" observable at all.

    The map deliberately reports absence without interpreting it: which absences
    matter is a question about the request, and the request is in the prompt.
    """
    if not entities:
        return "(执行后没有产生任何可读实体)"

    unique = sorted(set(entities))
    lines: list[str] = ["[已写入]"]
    for entity in unique[:limit]:
        value = _resolve_entity(world, entity)
        if value is None:
            lines.append(f"- {entity}: (不存在)")
            continue
        if hasattr(value, "model_dump"):
            payload = value.model_dump(exclude_none=True)
        elif isinstance(value, dict):
            payload = {k: v for k, v in value.items() if v is not None}
        else:
            payload = value
        rendered = json.dumps(payload, ensure_ascii=False, default=str)
        if len(rendered) > 320:
            rendered = rendered[:317] + "..."
        lines.append(f"- {entity}: {rendered}")
    if len(unique) > limit:
        lines.append(f"…(另有 {len(unique) - limit} 个实体未列出)")

    # Presence map, keyed by identity rather than by path — the facets of one
    # identity live in different collections (points.X / histories.X / alarms.X)
    # and it is the empty facet that signals unfinished work.
    collections = sorted(
        name for name, value in vars(world).items()
        if isinstance(value, dict) and name != "project_meta"
    )
    identities: list[str] = []
    for entity in unique:
        for ident in sorted(entity_ids(entity)):
            if ident not in identities:
                identities.append(ident)
    map_lines: list[str] = []
    for ident in identities[:limit]:
        present = [c for c in collections if ident in getattr(world, c, {})]
        absent = [c for c in collections if c not in present]
        # A nested entity (a widget) lives in no top-level collection, so the
        # per-collection scan alone would report an id that plainly exists in the
        # section above as existing nowhere — two contradictory statements about
        # the same id in one prompt, pointing the verifier at re-creating it.
        if not present and _identity_known(world, ident):
            map_lines.append(f"- {ident}: 已存在(嵌套在上方某个实体内)")
            continue
        if not absent:
            continue
        map_lines.append(
            f"- {ident}: 已存在于 [{', '.join(present) or '无'}]"
            f";尚未存在于 [{', '.join(absent)}]"
        )
    if map_lines:
        lines.append("")
        lines.append("[相关标识在各集合中的存在情况(仅供核对需求是否还有未完成的部分)]")
        lines.extend(map_lines)
    return "\n".join(lines)


def entity_ids(entity: str) -> set[str]:
    """Every id an entity path names, ignoring which collection holds them.

    World paths alternate collection and id — ``points.TEMP_101``,
    ``pages.p1.widgets.w1``, ``pages.p1.widgets.w1.bindings.value`` — so the ids
    sit at the odd positions and the collection names at the even ones.

    Returning the *set* rather than the last segment is the whole point. Taking
    only the last segment yields a **field name** for any deep path:
    ``bind_point``'s intent is ``pages.p1.widgets.w1.bindings.value``, whose last
    segment is ``value``, so a caller asking "which entities did this plan mean
    to touch?" was told ``value`` and never ``w1``. That silently disabled the
    cascade guard's escape hatch: a plan that intended to bind widget ``w1``
    contributed nothing about ``w1``, so re-creating ``w1`` after a
    ``WIDGET_NOT_FOUND`` looked like manufacturing an entity nobody asked for,
    and a fully recoverable partial failure was aborted instead.

    Note the deepest segment of a leaf mapping (``bindings.value``) is
    structurally an id and is returned as one. That over-generates ids like
    ``value`` / ``status``, which can only ever *weaken* protection for an entity
    literally named that — the safe direction.
    """
    if not entity:
        return set()
    parts = entity.split(".")
    return {p for p in parts[1::2] if p}


def _identity_known(world: Any, identity: str) -> bool:
    """Whether any collection *at any depth* holds an entity with this id.

    Must recurse. Widgets live at ``pages.p1.widgets.w1``, so a top-level-only
    scan reports an existing widget as existing nowhere — which makes a caller
    protect it as "named but absent" and block legitimate work against it.
    """
    if not identity:
        return False

    def walk(value: Any, depth: int) -> bool:
        if depth > 4:
            return False
        if isinstance(value, dict):
            if identity in value:
                return True
            return any(walk(v, depth + 1) for v in value.values())
        # Pydantic models carry nested collections as attributes (page.widgets).
        # Read ``model_fields`` off the class: instance access is deprecated in
        # Pydantic 2.11 and removed in 3.
        fields = getattr(type(value), "model_fields", None)
        if fields:
            return any(walk(getattr(value, name, None), depth + 1) for name in fields)
        return False

    return any(walk(v, 0) for v in vars(world).values())


def _topological_repair(steps: list[PlanStep], world: Any) -> tuple[list[PlanStep], bool]:
    """Reorder so every producer precedes its consumers.

    Edges come from the ``intended_entities`` / ``referenced_entities`` contract
    every tool already implements — the same contract the cascade-failure
    detector reads after the fact. Applying it *before* execution converts a
    measured failure into a prevented one.

    Ties are broken by "stay in the current state if possible, else original
    order", so the repair also reduces state transitions. A dependency cycle
    (which a well-formed plan cannot have) falls back to the proposed order
    rather than guessing.
    """
    n = len(steps)
    producers: dict[str, int] = {}
    for i, step in enumerate(steps):
        for entity in step.intended:
            producers.setdefault(entity, i)

    successors: list[set[int]] = [set() for _ in range(n)]
    indegree = [0] * n
    for j, step in enumerate(steps):
        for entity in step.referenced:
            i = producers.get(entity)
            if i is None or i == j:
                continue
            # Already in the world → no ordering constraint needed.
            if _entity_exists(world, entity):
                continue
            if j not in successors[i]:
                successors[i].add(j)
                indegree[j] += 1

    ready = [i for i in range(n) if indegree[i] == 0]
    out: list[PlanStep] = []
    order: list[int] = []
    current_state: str | None = None
    while ready:
        ready.sort(key=lambda i: (steps[i].state != current_state, i))
        pick = ready.pop(0)
        order.append(pick)
        out.append(steps[pick])
        current_state = steps[pick].state
        for succ in sorted(successors[pick]):
            indegree[succ] -= 1
            if indegree[succ] == 0:
                ready.append(succ)
    if len(out) != n:  # cycle — keep the model's order rather than guess
        return steps, False
    return out, order != list(range(n))


def compile_plan(
    raw_steps: list[dict[str, Any]],
    registry: ToolRegistry,
    world: Any,
    *,
    allowed_atomics: list[str] | None = None,
    start_state: str = "ANALYZE_INTENT",
    max_steps: int = 12,
    reorder: bool = True,
    refusal: str | None = None,
    repair: bool = True,
) -> CompiledPlan:
    """Turn a proposed plan into dispatch-ready steps, or drop what cannot run.

    Every rejection reason is recorded in the diagnostics; nothing is dropped
    silently. The four filters, in order:

    * **unknown tool** — not in the registry, or outside *allowed_atomics*.
      This is where a hallucinated name dies, before it can be dispatched.
    * **schema-invalid arguments** — validated against the tool's own Pydantic
      model, so ``SCHEMA_ERROR`` is prevented rather than recorded.
    * **duplicate** — identical (tool, arguments) collapse to one dispatch.
    * **unreachable state** — no legal FSM path exposes the tool from here.
    """
    diag = PlanDiagnostics(proposed=len(raw_steps), refusal=refusal)
    permitted = set(allowed_atomics) if allowed_atomics is not None else None

    prepared: list[PlanStep] = []
    seen: set[str] = set()
    cursor = start_state
    previous_state: str | None = None

    for raw in raw_steps:
        if len(prepared) >= max_steps:
            diag.dropped_over_budget += 1
            continue
        name = raw.get("tool")
        if not isinstance(name, str) or not name:
            diag.dropped_unknown_tool.append(str(name))
            continue
        arguments = raw.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}

        # A planner may name the Domain Tool with an `action` discriminator
        # instead of the atomic; accept both so the structure works under
        # either tool view.
        if not _is_atomic(registry, name):
            sub = arguments.get("action")
            if isinstance(sub, str) and _is_atomic(registry, sub):
                name = sub
            else:
                diag.dropped_unknown_tool.append(name)
                continue
        if permitted is not None and name not in permitted:
            diag.dropped_unknown_tool.append(name)
            continue

        meta = registry.atomic(name)
        parsed = _validate_or_repair(meta, arguments, repair=repair)
        if parsed is None:
            diag.dropped_schema_invalid.append(name)
            diag.schema_invalid_detail.append(_schema_error_summary(meta, arguments))
            continue
        # Repair may have rewritten the arguments (e.g. decoded a
        # double-encoded "[50, 50]"); the step must dispatch what actually
        # validated, not the raw proposal.
        arguments = parsed.model_dump(exclude_none=True)

        signature = _signature(name, arguments)
        if signature in seen:
            diag.dropped_duplicate.append(name)
            continue

        state = _pick_state(name, cursor, previous_state)
        if state is None:
            diag.dropped_unreachable_state.append(name)
            continue

        seen.add(signature)
        previous_state = state
        cursor = state
        prepared.append(
            PlanStep(
                tool=name,
                action=meta.action,
                arguments=arguments,
                rationale=str(raw.get("rationale") or ""),
                state=state,
                parsed=parsed,
                intended=list(meta.handler.__class__.intended_entities(parsed)),
                referenced=list(meta.handler.__class__.referenced_entities(parsed)),
            )
        )

    if reorder and len(prepared) > 1:
        prepared, diag.reordered = _topological_repair(prepared, world)

    diag.compiled = len(prepared)
    return CompiledPlan(steps=prepared, diagnostics=diag)


def parse_plan_payload(
    payload: Any,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Normalise the planner backend's reply into ``(steps, refusal, clarify)``.

    Backends are asked for ``{"steps": [...]}`` but models also return a bare
    list; both are accepted. Anything else yields an empty plan, which makes the
    caller fall back to the interleaved loop rather than fail the run.

    ``clarify`` is deliberately a *separate* channel from ``refusal``. They are
    different answers to different questions — "I will not do this" versus "I
    cannot yet tell what you want" — and conflating them was measurable in both
    directions on the 106-case run: the planner invented an identity for
    underspecified requests (golden-008 planned ``create_page(id="main_page",
    name="主页面")`` for the bare "帮忙建个页面", mutating the world on a case
    that expects a question), while golden-018 pushed a *legitimate* request
    down the safety channel because a field was unspecified. Same prompt slot,
    opposite errors. Keeping them apart also keeps the terminal states honest:
    a refusal ends on DONE, a clarification ends on ASK_USER.
    """
    if payload is None:
        return [], None, None
    if isinstance(payload, list):
        return [s for s in payload if isinstance(s, dict)], None, None
    if isinstance(payload, dict):
        steps = payload.get("steps")
        refusal = payload.get("refusal")
        clarify = payload.get("clarify")
        kept = [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []
        return (
            kept,
            str(refusal) if refusal else None,
            str(clarify) if clarify else None,
        )
    return [], None, None
