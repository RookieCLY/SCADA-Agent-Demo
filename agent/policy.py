"""Runtime safety policy — the §4.7 "outer cage".

The four architecture layers (hierarchical tools / Tool RAG / Workflow / state
machine) form the *inner* cage: they stop the LLM from **picking the wrong
tool**. They do not stop it from picking a legitimately-visible tool and doing
something irreversible with it. §4.7 of the paper argues that industrial agents
need a second, independent cage that stops the LLM from **doing damage**, and
that this cage must live in the runtime rather than in the system prompt:

    "运行态写权限不是用审计与确认去管，而是从系统层就不给。"

Before this module the repository enforced that claim only through
``DEFAULT_SYSTEM_PROMPT`` — and ``deploy_project(force=True)`` demonstrably
bypassed validation at the handler level (``tools/deployment.py``), so a model
that ignored the prompt could still deploy an unvalidated project. A prompt is
not a safety boundary; it is a request.

This module turns those rules into a declarative, auditable table that is
evaluated *before dispatch*. A denied call never reaches the tool handler, so
no world mutation is possible regardless of what the model was told, how the
user phrased the request, or whether the prompt was followed at all.

Design notes
------------
* Rules are data (``POLICY_RULES``), not scattered ``if`` statements, so the
  policy set can be diffed, version-pinned and audited — §4.7.5 treats the
  policy as a compliance asset, not an implementation detail.
* The engine carries **per-run** mutable state (the destructive-operation
  counter). ``SafetyPolicy.reset()`` is called at the top of every
  ``Agent.run`` so counts never leak between queries.
* Denials surface as ``ErrorCode.POLICY_DENIED``, which is distinct from
  ``OUT_OF_SCOPE`` (inner cage: tool not visible in this state) and from
  ``BUSINESS_RULE`` (handler-level domain rule). Keeping the three apart is
  what lets the evaluation layer report *prompt-only refusal* separately from
  *runtime-enforced refusal*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent.config import SafetyPolicyConfig
from world import MockWorld

# ============================================================ tool classification

#: Atomics that only read the world. Everything else is treated as a write.
#: Deliberately a hard-coded allow-list rather than a heuristic: in operations
#: mode an unrecognised tool must fail *closed*, not open.
READ_ONLY_ATOMICS: frozenset[str] = frozenset(
    {
        "list_pages",
        "list_points",
        "list_history",
        "list_scripts",
        "list_devices",
        "list_alarms",
        "list_trend_groups",
        "list_users",
        "query_history",
        "show_deployment_status",
    }
)

#: Name prefixes that also denote read-only access. Synthesised filler tools
#: (``build_default_registry(tool_count=N)``) follow these conventions.
READ_ONLY_PREFIXES: tuple[str, ...] = ("list_", "show_", "query_", "get_", "describe_")

#: Irreversible or hard-to-undo operations. §2.5(5) / §4.7.2 — the paper's
#: "批量或不可逆的删除/禁用操作" clause.
DESTRUCTIVE_ATOMICS: frozenset[str] = frozenset(
    {
        "delete_point",
        "delete_page",
        "delete_alarm",
        "delete_widget",
        "delete_script",
        "delete_device",
        "delete_trend_group",
        "disable_alarm",
        "disable_script",
        "disable_history",
    }
)


def is_read_only(atomic: str) -> bool:
    """True when *atomic* cannot mutate the world."""
    if atomic in READ_ONLY_ATOMICS:
        return True
    return atomic.startswith(READ_ONLY_PREFIXES)


def is_destructive(atomic: str) -> bool:
    """True when *atomic* removes or disables an existing entity."""
    return atomic in DESTRUCTIVE_ATOMICS


# ============================================================ decision type
@dataclass(frozen=True)
class PolicyDecision:
    """Outcome of evaluating one tool call against the policy table."""

    allowed: bool
    rule_id: str | None = None
    reason: str | None = None

    @property
    def denied(self) -> bool:
        return not self.allowed


ALLOW = PolicyDecision(allowed=True)


# ============================================================ rule table
#: Signature of a rule predicate. Returns a human-readable reason string when
#: the call must be **denied**, or ``None`` to allow it.
RulePredicate = Callable[[str, dict[str, Any], MockWorld, "SafetyPolicy"], str | None]


@dataclass(frozen=True)
class PolicyRule:
    id: str
    severity: str  # "critical" — never overridable | "high" — bounded by config
    description: str
    rationale: str  # paper section that motivates the rule
    predicate: RulePredicate


# ---- predicates ---------------------------------------------------------
def _deny_forced_deploy(
    atomic: str, args: dict[str, Any], world: MockWorld, policy: "SafetyPolicy"
) -> str | None:
    if atomic != "deploy_project":
        return None
    if not args.get("force"):
        return None
    return (
        "deploy_project(force=true) 会跳过项目校验直接下装，属于不可逆高危操作，"
        "运行时策略禁止执行。请先调用 validate_project，校验通过后再以 force=false 下装。"
    )


def _deny_unvalidated_deploy(
    atomic: str, args: dict[str, Any], world: MockWorld, policy: "SafetyPolicy"
) -> str | None:
    if atomic != "deploy_project":
        return None
    deployment_id = args.get("deployment_id", "default")
    record = world.deployments.get(deployment_id)
    if record is None:
        return (
            f"部署 {deployment_id} 尚未经过 validate_project 校验，运行时策略禁止下装。"
            "请先调用 validate_project。"
        )
    if getattr(record, "status", None) == "failed":
        errors = list(getattr(record, "validation_errors", []) or [])[:3]
        return (
            f"部署 {deployment_id} 的上一次校验未通过（{errors}），运行时策略禁止下装。"
            "请修复校验错误后重新调用 validate_project。"
        )
    return None


def _deny_runtime_write(
    atomic: str, args: dict[str, Any], world: MockWorld, policy: "SafetyPolicy"
) -> str | None:
    if policy.config.runtime_mode != "operations_time":
        return None
    if is_read_only(atomic):
        return None
    return (
        f"当前处于运行态(operations_time)，Agent 仅允许只读观察。写操作 {atomic!r} "
        "必须由人类操作员通过 SCADA 原生 HMI 发起，运行时策略已在系统层剥夺该权限。"
    )


def _deny_bulk_destructive(
    atomic: str, args: dict[str, Any], world: MockWorld, policy: "SafetyPolicy"
) -> str | None:
    if not is_destructive(atomic):
        return None
    limit = policy.config.max_destructive_ops
    if limit < 0:  # negative disables the cap
        return None
    if policy.destructive_count < limit:
        return None
    return (
        f"本次会话已执行 {policy.destructive_count} 次删除/禁用操作，达到运行时策略上限 "
        f"{limit}。批量不可逆操作必须由人工确认后分批执行，运行时策略拒绝继续。"
    )


POLICY_RULES: tuple[PolicyRule, ...] = (
    PolicyRule(
        id="R-DEPLOY-FORCE",
        severity="critical",
        description="Refuse deploy_project(force=true) — it skips validation.",
        rationale="§4.7.2 red line; §4.3.6 'validate before deploy' process dependency",
        predicate=_deny_forced_deploy,
    ),
    PolicyRule(
        id="R-DEPLOY-UNVALIDATED",
        severity="critical",
        description="Refuse deploy_project when validation is missing or failed.",
        rationale="§4.3.6 — 必须先校验项目才能部署",
        predicate=_deny_unvalidated_deploy,
    ),
    PolicyRule(
        id="R-RUNTIME-WRITE",
        severity="critical",
        description="In operations_time mode, refuse every write; reads only.",
        rationale="§4.7.3(3) / §4.7.4 — 运行态下 LLM 绝对只读，永远不写",
        predicate=_deny_runtime_write,
    ),
    PolicyRule(
        id="R-BULK-DESTRUCTIVE",
        severity="high",
        description="Cap the number of delete/disable operations per session.",
        rationale="§2.5(5) / §4.6.3(6) — 批量不可逆操作与熔断",
        predicate=_deny_bulk_destructive,
    ),
)


# ============================================================ engine
@dataclass
class SafetyPolicy:
    """Evaluates tool calls against :data:`POLICY_RULES` before dispatch.

    Carries per-run mutable state; call :meth:`reset` between queries.
    """

    config: SafetyPolicyConfig
    destructive_count: int = 0
    denials: list[dict[str, str]] = field(default_factory=list)

    # ---------------------------------------------------------------- lifecycle
    def reset(self) -> None:
        """Clear per-run counters. Called at the top of every ``Agent.run``."""
        self.destructive_count = 0
        self.denials = []

    # ---------------------------------------------------------------- rules
    def active_rules(self) -> tuple[PolicyRule, ...]:
        """The rule subset this config enables (``rules: null`` means all)."""
        selected = self.config.rules
        if selected is None:
            return POLICY_RULES
        wanted = set(selected)
        return tuple(r for r in POLICY_RULES if r.id in wanted)

    # ---------------------------------------------------------------- evaluation
    def check(
        self, atomic: str, args: dict[str, Any], world: MockWorld
    ) -> PolicyDecision:
        """Evaluate one atomic call. Rules are checked in declaration order.

        Args:
            atomic: The resolved atomic tool name (post domain-action unwrap).
            args: Raw arguments as emitted by the LLM.
            world: The world the call would mutate.

        Returns:
            :data:`ALLOW`, or a denying :class:`PolicyDecision` carrying the
            first matching rule's id and an LLM-readable reason.
        """
        if not self.config.enabled:
            return ALLOW
        for rule in self.active_rules():
            reason = rule.predicate(atomic, args, world, self)
            if reason is not None:
                decision = PolicyDecision(allowed=False, rule_id=rule.id, reason=reason)
                self.denials.append(
                    {"rule_id": rule.id, "atomic": atomic, "severity": rule.severity}
                )
                return decision
        return ALLOW

    def record_execution(self, atomic: str) -> None:
        """Account for a call that was allowed **and** actually dispatched."""
        if not self.config.enabled:
            return
        if is_destructive(atomic):
            self.destructive_count += 1

    # ---------------------------------------------------------------- reporting
    def summary(self) -> dict[str, Any]:
        """Per-run policy activity, embedded in the trace for offline analysis."""
        return {
            "enabled": self.config.enabled,
            "runtime_mode": self.config.runtime_mode,
            "max_destructive_ops": self.config.max_destructive_ops,
            "active_rules": [r.id for r in self.active_rules()],
            "denials": list(self.denials),
            "denial_count": len(self.denials),
            "destructive_executed": self.destructive_count,
        }


def build_policy(config: SafetyPolicyConfig) -> SafetyPolicy:
    """Construct the policy engine for an experiment config."""
    return SafetyPolicy(config=config)


__all__ = [
    "ALLOW",
    "DESTRUCTIVE_ATOMICS",
    "POLICY_RULES",
    "PolicyDecision",
    "PolicyRule",
    "READ_ONLY_ATOMICS",
    "SafetyPolicy",
    "build_policy",
    "is_destructive",
    "is_read_only",
]
