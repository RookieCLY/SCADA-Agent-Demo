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
