"""State machine — Phase 1 baseline.

LangGraph is wired in Phase 2; for now the state machine is a plain Python
object with three responsibilities:

1. Hold the catalogue of states + per-state allowed-tool whitelists.
2. Enforce legal transitions.
3. Filter a candidate tool list down to the current state's whitelist
   (the *hard filter* prepended to Tool RAG per §1.4.4).

The state set is the 8 functional stages from §1.4.5 / §1.4.6, adapted to the
Phase-1 alarm-creation E2E path: ANALYZE_INTENT → CONFIG_ALARM → DONE is the
happy-path slice exercised by ``configs/D_minimal.yaml``.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

# Per-domain action dicts (name -> tool class). Imported so the per-state
# whitelists below can be derived from the *live* tool library instead of
# hand-maintained name lists — as the library grows (now ~300 tools), each
# state automatically exposes its whole domain. tools/* never import agent/*,
# so this introduces no import cycle.
from tools.deployment import DEPLOYMENT_ACTIONS
from tools.manage_alarms import ALARM_ACTIONS
from tools.manage_communication import COMM_ACTIONS
from tools.manage_databases import DATABASE_ACTIONS
from tools.manage_devices import DEVICE_ACTIONS
from tools.manage_graphics import GRAPHICS_ACTIONS
from tools.manage_history import HISTORY_ACTIONS
from tools.manage_notifications import NOTIFICATION_ACTIONS
from tools.manage_pages import PAGE_ACTIONS
from tools.manage_points import POINT_ACTIONS
from tools.manage_recipes import RECIPE_ACTIONS
from tools.manage_reports import REPORT_ACTIONS
from tools.manage_schedules import SCHEDULE_ACTIONS
from tools.manage_scripts import SCRIPT_ACTIONS
from tools.manage_security import SECURITY_ACTIONS
from tools.manage_trends import TREND_ACTIONS
from tools.manage_users import USER_ACTIONS


@dataclass(frozen=True)
class StateSpec:
    name: str
    description: str
    allowed_tools: frozenset[str]
    next_states: frozenset[str]
    terminal: bool = False


# ============================================================ state catalogue
STATES: dict[str, StateSpec] = {
    "ANALYZE_INTENT": StateSpec(
        name="ANALYZE_INTENT",
        description="Parse the user query into a high-level intent / target domain.",
        allowed_tools=frozenset(
            {
                "list_pages",
                "list_points",
                "list_history",
                "list_scripts",
                "show_deployment_status",
            }
        ),
        next_states=frozenset(
            {
                "CONFIG_ALARM",
                "CONFIG_POINT",
                "MANAGE_PAGES",
                "GENERATE_LAYOUT",
                "BIND_POINTS",
                "CONFIG_HISTORY",
                "CONFIG_SCRIPT",
                "DEPLOY",
                "VALIDATE",
                "ASK_USER",
                "DONE",
            }
        ),
    ),
    "CONFIG_POINT": StateSpec(
        name="CONFIG_POINT",
        description="Create / update / delete SCADA points.",
        allowed_tools=frozenset(
            {"create_point", "update_point", "delete_point", "list_points"}
        ),
        next_states=frozenset(
            {
                "MANAGE_PAGES",
                "GENERATE_LAYOUT",
                "CONFIG_ALARM",
                "BIND_POINTS",
                "CONFIG_HISTORY",
                "CONFIG_SCRIPT",
                "VALIDATE",
                "DEPLOY",
                "DONE",
            }
        ),
    ),
    "MANAGE_PAGES": StateSpec(
        name="MANAGE_PAGES",
        description="Create / rename / delete HMI pages and place widgets.",
        allowed_tools=frozenset(
            {"create_page", "rename_page", "delete_page", "create_widget", "list_pages"}
        ),
        next_states=frozenset(
            {
                "GENERATE_LAYOUT",
                "BIND_POINTS",
                "CONFIG_ALARM",
                "CONFIG_HISTORY",
                "CONFIG_SCRIPT",
                "VALIDATE",
                "DEPLOY",
                "DONE",
            }
        ),
    ),
    "GENERATE_LAYOUT": StateSpec(
        name="GENERATE_LAYOUT",
        description="Draw graphical primitives, apply layouts, group widgets, restyle.",
        allowed_tools=frozenset(
            {
                "create_rect",
                "create_circle",
                "create_line",
                "create_text",
                "apply_flow_layout",
                "group_widgets",
                "set_widget_style",
                "delete_widget",
                "list_pages",
            }
        ),
        next_states=frozenset(
            {"MANAGE_PAGES", "BIND_POINTS", "VALIDATE", "DEPLOY", "DONE"}
        ),
    ),
    "BIND_POINTS": StateSpec(
        name="BIND_POINTS",
        description="Bind SCADA points to widget properties.",
        allowed_tools=frozenset({"bind_point", "list_points", "list_pages"}),
        next_states=frozenset(
            {"CONFIG_ALARM", "CONFIG_HISTORY", "CONFIG_SCRIPT", "VALIDATE", "DEPLOY", "DONE"}
        ),
    ),
    "CONFIG_ALARM": StateSpec(
        name="CONFIG_ALARM",
        description="Create / enable / disable / delete alarms.",
        allowed_tools=frozenset(
            {
                "create_analog_alarm",
                "create_digital_alarm",
                "set_threshold",
                "enable_alarm",
                "disable_alarm",
                "delete_alarm",
                "list_points",
            }
        ),
        next_states=frozenset(
            {
                "BIND_POINTS",
                "MANAGE_PAGES",
                "CONFIG_HISTORY",
                "CONFIG_SCRIPT",
                "VALIDATE",
                "DEPLOY",
                "DONE",
            }
        ),
    ),
    "CONFIG_HISTORY": StateSpec(
        name="CONFIG_HISTORY",
        description="Configure historian sampling / retention / queries.",
        allowed_tools=frozenset(
            {
                "enable_history",
                "disable_history",
                "set_retention",
                "query_history",
                "list_history",
                "list_points",
            }
        ),
        next_states=frozenset(
            {"CONFIG_SCRIPT", "CONFIG_ALARM", "VALIDATE", "DEPLOY", "DONE"}
        ),
    ),
    "CONFIG_SCRIPT": StateSpec(
        name="CONFIG_SCRIPT",
        description="Author / enable / disable user scripts.",
        allowed_tools=frozenset(
            {
                "create_script",
                "update_script_body",
                "enable_script",
                "disable_script",
                "delete_script",
                "list_scripts",
                "list_points",
            }
        ),
        next_states=frozenset({"VALIDATE", "DEPLOY", "DONE"}),
    ),
    "VALIDATE": StateSpec(
        name="VALIDATE",
        description="Cross-entity consistency check before deployment.",
        allowed_tools=frozenset(
            {
                "validate_project",
                "show_deployment_status",
                "list_points",
                "list_pages",
                "list_history",
                "list_scripts",
            }
        ),
        next_states=frozenset({"DEPLOY", "ANALYZE_INTENT", "DONE"}),
    ),
    "DEPLOY": StateSpec(
        name="DEPLOY",
        description="Deploy or roll back the project.",
        allowed_tools=frozenset(
            {
                "deploy_project",
                "rollback_deployment",
                "show_deployment_status",
                "validate_project",
            }
        ),
        next_states=frozenset({"VALIDATE", "DONE"}),
    ),
    "ASK_USER": StateSpec(
        name="ASK_USER",
        description="Agent needs clarification — no tools may be called.",
        allowed_tools=frozenset(),
        next_states=frozenset({"ANALYZE_INTENT", "DONE"}),
    ),
    "DONE": StateSpec(
        name="DONE",
        description="Task complete.",
        allowed_tools=frozenset(),
        next_states=frozenset(),
        terminal=True,
    ),
}


# Every non-terminal working state must be able to bail out to ASK_USER.
# §4.4.3 lists "失败触发：错误时回退到安全状态" as a first-class transition trigger,
# and the out-of-scope circuit breaker in the orchestrator depends on it: without
# a universal escape hatch, an agent that keeps requesting a tool the current
# state forbids can only thrash until max_turns or die early. Applied as a
# post-pass so the per-state tables above stay readable.
STATES = {
    name: (
        spec
        if spec.terminal or name == "ASK_USER"
        else replace(spec, next_states=spec.next_states | frozenset({"ASK_USER"}))
    )
    for name, spec in STATES.items()
}


# ---- categorize the full tool library into per-state whitelists -------------
# Each core state exposes its whole domain (so the tools added to the library
# are reachable under the state machine, not just the original handful), and
# each "extra" domain gets its own configuration state reachable from
# ANALYZE_INTENT. Whitelists are derived from the live *_ACTIONS dicts so future
# tools are categorized automatically.
def _names(*action_dicts: dict) -> frozenset[str]:
    out: set[str] = set()
    for d in action_dicts:
        out.update(d.keys())
    return frozenset(out)


# domain tools that belong on an existing core state
_CORE_STATE_DOMAINS: dict[str, tuple[dict, ...]] = {
    "CONFIG_POINT": (POINT_ACTIONS,),
    "CONFIG_ALARM": (ALARM_ACTIONS,),
    "MANAGE_PAGES": (PAGE_ACTIONS,),
    "GENERATE_LAYOUT": (GRAPHICS_ACTIONS,),
    "CONFIG_HISTORY": (HISTORY_ACTIONS,),
    "CONFIG_SCRIPT": (SCRIPT_ACTIONS,),
    "DEPLOY": (DEPLOYMENT_ACTIONS,),
}

# one new configuration state per extra domain: (state, description, actions)
_EXTRA_STATE_DOMAINS: dict[str, tuple[str, dict]] = {
    "CONFIG_DEVICE": ("Create / configure field devices and equipment.", DEVICE_ACTIONS),
    "CONFIG_TREND": ("Build and configure trend curves.", TREND_ACTIONS),
    "MANAGE_RECIPE": ("Author and manage batch recipes.", RECIPE_ACTIONS),
    "MANAGE_USERS": ("Manage user accounts, roles and permissions.", USER_ACTIONS),
    "CONFIG_COMM": ("Configure communication drivers and mappings.", COMM_ACTIONS),
    "MANAGE_REPORT": ("Design and schedule reports.", REPORT_ACTIONS),
    "CONFIG_SCHEDULE": ("Create and manage scheduled jobs.", SCHEDULE_ACTIONS),
    "CONFIG_SECURITY": ("Configure security, audit and backup policy.", SECURITY_ACTIONS),
    "CONFIG_DATABASE": ("Configure external database connections.", DATABASE_ACTIONS),
    "CONFIG_NOTIFICATION": ("Configure alarm notification rules.", NOTIFICATION_ACTIONS),
}

_EXTRA_COMMON_NEXT = frozenset(
    {"VALIDATE", "DEPLOY", "DONE", "ANALYZE_INTENT", "ASK_USER"}
)


def _categorize_tools(states: dict[str, StateSpec]) -> dict[str, StateSpec]:
    states = dict(states)
    # Tools deliberately placed by the hand-written tables above (e.g.
    # ``bind_point`` lives in BIND_POINTS, not MANAGE_PAGES) keep their
    # placement — only *unplaced* domain tools are folded into the domain's
    # primary state, so the intentional cross-state split is preserved.
    already_placed = {t for s in states.values() for t in s.allowed_tools}
    # 1. add each core domain's not-yet-placed tools to its primary state
    for st, dicts in _CORE_STATE_DOMAINS.items():
        if st in states:
            spec = states[st]
            new_tools = _names(*dicts) - already_placed
            states[st] = replace(spec, allowed_tools=spec.allowed_tools | new_tools)
    # 2. add one state per extra domain
    for st, (desc, actions) in _EXTRA_STATE_DOMAINS.items():
        states[st] = StateSpec(
            name=st,
            description=desc,
            allowed_tools=_names(actions) | {"list_points"},
            next_states=_EXTRA_COMMON_NEXT,
        )
    # 3. make the new states reachable from intent analysis
    ai = states["ANALYZE_INTENT"]
    states["ANALYZE_INTENT"] = replace(
        ai, next_states=ai.next_states | frozenset(_EXTRA_STATE_DOMAINS.keys())
    )
    return states


STATES = _categorize_tools(STATES)

INITIAL_STATE = "ANALYZE_INTENT"


# ============================================================ state machine
@dataclass
class StateMachine:
    """Phase-1 state machine — see §1.4.5."""

    current: str = INITIAL_STATE
    history: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.current not in STATES:
            raise ValueError(f"initial state {self.current!r} unknown")
        self.history.append(self.current)

    # ---------- transitions
    def can_transit(self, target: str) -> bool:
        if target not in STATES:
            return False
        return target in STATES[self.current].next_states

    def transit(self, target: str) -> None:
        if not self.can_transit(target):
            raise ValueError(
                f"illegal transition {self.current!r} → {target!r}; "
                f"allowed: {sorted(STATES[self.current].next_states)}"
            )
        self.current = target
        self.history.append(target)

    def force_to(self, target: str) -> None:
        """Set the state directly, bypassing the per-state ``next_states``
        whitelist.

        Reserved for an authoritative sequencer — the Workflow Engine in
        ``mode: engine`` (§4.3.1) owns control flow while a workflow runs and may
        legitimately land on a state the adjacency graph does not list (e.g. a
        ``conditional_step`` branch). Using ``transit`` there would raise and
        strand the run with a stale whitelist. Not for LLM-driven transitions.
        """
        if target not in STATES:
            raise ValueError(f"unknown state {target!r}")
        self.current = target
        self.history.append(target)

    @property
    def is_terminal(self) -> bool:
        return STATES[self.current].terminal

    # ---------- tool filtering
    def allowed_tools(self, state: str | None = None) -> frozenset[str]:
        s = state or self.current
        if s not in STATES:
            raise KeyError(f"unknown state {s!r}")
        return STATES[s].allowed_tools

    def filter_tools(self, candidates: list[str]) -> list[str]:
        """Drop any tool not in the current state's whitelist."""
        allowed = self.allowed_tools()
        return [t for t in candidates if t in allowed]


__all__ = ["INITIAL_STATE", "STATES", "StateMachine", "StateSpec"]
