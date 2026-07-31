"""Orchestrator — Phase-2 main loop.

The Phase-1 explicit-while loop is preserved as the execution kernel; Phase 2
plugs three optional components behind ``ArchitectureConfig`` flags:

* **Tool RAG** (`architecture.tool_rag.enabled`) — soft-rank Tool candidates by
  query similarity, intersected with the state-machine whitelist (hard filter).
* **Workflow Engine** (`architecture.workflow.enabled`) — route the query to a
  matching workflow; per-step ``allowed_tools`` layered as another hard filter
  on top of the state machine.
* **Resources** (`architecture.resources_separation`) — read-only ``read_resource``
  pseudo-tool that bypasses Tool RAG and is invisible to the Tool catalogue.

All three are individually switchable; configs B/C/D/E/F (§3.2) flip them in
the documented combinations.

The trace schema keeps Phase-1 wire format for backward compatibility; we add
``rag`` / ``workflow`` blocks and ``resource_reads`` entries via existing slots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from agent.config import ExperimentConfig, load_config
from agent.dispatcher import dispatch_atomic, dispatch_domain
from agent.llm import LLMProvider, LLMResponse, build_llm
from agent.multi_agent import (
    REFUSAL_HANDOFF_BLOCK,
    SPECIALIST_PROMPT_BLOCK,
    Blackboard,
    Subtask,
    critic_feedback,
    route_subtasks,
    route_subtasks_from_plan,
    unsatisfied_subtasks,
)
from agent.planner import (
    VERIFY_INSTRUCTION,
    PlanDiagnostics,
    PlanStep,
    _identity_known,
    compile_plan,
    describe_entities_for_verify,
    describe_tools_for_planner,
    entity_ids,
    parse_plan_payload,
    state_route,
    summarize_world_for_planner,
)
from agent.policy import SafetyPolicy, build_policy, is_destructive, is_read_only
from agent.react import REACT_PROMPT_BLOCK, ReActScratchpad, action_signature
from agent.state_machine import INITIAL_STATE, STATES, StateMachine
from agent.tool_rag import (
    ScoredTool,
    ToolIndex,
    build_index_from_registry,
    select_tools,
)
from agent.tool_registry import ToolRegistry, build_default_registry
from agent.tracer import LLMCallRecord, ToolCallRecord, Tracer
from agent.workflow import (
    ConditionalStep,
    DeterministicStep,
    LLMStep,
    LoopStep,
    SubWorkflowStep,
    ToolCallStep,
    WorkflowCatalogue,
    WorkflowEngine,
    WorkflowExecutionState,
    get_handler,
    load_catalogue,
)
from resources import ResourceNotFound, ResourceRegistry, build_default_resource_registry
from tools._base import REFERENCE_ERROR_CODES
from world import Device, MockWorld, Point
from world.memory_backend import deep_copy_world

READ_RESOURCE_TOOL = "read_resource"  # synthetic tool name for Resource reads


@dataclass
class PlanOutcome:
    """Result of the Plan-and-Execute phase.

    ``executed=False`` with ``escalate_to_crew=False`` means the phase declined
    to take over (backend has no planner, or the plan compiled to nothing and
    fallback is on) and the interleaved loop should run as if the lever were
    off. ``escalate_to_crew=True`` hands the task to the Multi-Agent crew —
    either before execution (the compiled plan spans enough domains that
    decomposition should win) or after a failure that exhausted the replan
    budget.
    """

    executed: bool
    completed: bool = True
    turns: int = 0
    reason: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    escalate_to_crew: bool = False
    #: Entities already materialised by executed plan steps (seed for the
    #: crew's Blackboard on a failure escalation, so Specialists reference the
    #: partial work instead of redoing or contradicting it).
    board_entities: list[str] = field(default_factory=list)
    #: The compiled plan steps the crew should carry out (plan-guided
    #: escalation). On a failure escalation this is the un-executed remainder;
    #: pre-execution escalations hand over the whole compiled plan. The
    #: escalated crew must not re-derive from the raw query the work the
    #: planner already decided.
    plan_steps: list[PlanStep] = field(default_factory=list)
    #: A refusal or safety concern raised by the plan tier. Without carrying
    #: this across the tier boundary a refusal simply evaporates: the crew
    #: receives a worklist and executes it, which is why escalated reject
    #: cases scored 20% behavior_success against the plan tier's 44%.
    refusal: str | None = None


@dataclass
class CrewOutcome:
    """Result of the Multi-Agent phase.

    ``executed=False`` means the Supervisor declined to take over (no ranked
    tool maps to a working state) and the interleaved loop should run as if
    the lever were off.
    """

    executed: bool
    completed: bool = True
    turns: int = 0
    reason: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)


#: Verb prefixes that make a tool name destructive regardless of whether the
#: §4.7 policy set happens to enumerate it.
#:
#: ``agent.policy.is_destructive`` is backed by a hand-written 10-name set, and of
#: the ~40 delete/disable/purge atomics in the registry it flags 10 — ``purge_history``,
#: ``drop_db_table``, ``delete_user`` and ``unbind_widget_point`` among the misses.
#: That set is shared with the ``max_destructive_ops`` cage, so widening it there
#: would change every arm's behaviour and is a separate decision. The patch path
#: makes a stronger promise than the cage does ("a verification round never
#: deletes"), so it screens by name as well and the promise holds for tools the
#: enumerated set has not caught up with.
_DESTRUCTIVE_NAME_PREFIXES: tuple[str, ...] = (
    "delete_", "remove_", "purge_", "drop_", "disable_", "revoke_",
    "unbind_", "reset_", "clear_", "batch_delete", "force_",
)


def _is_destructive_for_patch(atomic: str) -> bool:
    """Whether a verification patch may not run this tool."""
    return is_destructive(atomic) or atomic.startswith(_DESTRUCTIVE_NAME_PREFIXES)


class _PlanReply(NamedTuple):
    """What one planning call returned.

    ``refusal`` and ``clarify`` are mutually exclusive in practice and mean
    different things: "this should not be done" versus "I cannot tell what you
    want yet". They terminate in different states (DONE vs ASK_USER), so they
    cannot share a field. A NamedTuple rather than a 4-tuple because three call
    sites unpack this and a positional mix-up between two adjacent
    ``str | None`` channels would be silent.
    """

    steps: list[dict[str, Any]]
    refusal: str | None
    clarify: str | None
    supported: bool
    #: The call actually returned a payload. False when the backend raised, which
    #: is otherwise indistinguishable from "the model answered with no steps" —
    #: and a verification round must not record a timeout as "checked, nothing
    #: missing", which asserts the opposite of what happened.
    answered: bool = True


@dataclass(frozen=True)
class _DeniedResult:
    """Stand-in for a ToolResult on a call the §4.7 cage refused before dispatch."""

    rule_id: str
    reason: str
    error_code: str = "POLICY_DENIED"

    @property
    def error_msg(self) -> str:
        return f"[{self.rule_id}] {self.reason}"


class AgentEventSink(Protocol):
	"""Optional observer for real-time Agent.run events."""

	def on_run_start(self, query: str, world: MockWorld) -> None: ...

	def on_state_enter(self, state: str) -> None: ...

	def on_llm_response(self, turn: int, state: str, response: LLMResponse) -> None: ...

	def on_resource_read(self, turn: int, record: dict[str, Any]) -> None: ...

	def on_tool_call(
		self,
		turn: int,
		record: ToolCallRecord,
		world_before: MockWorld,
		world_after: MockWorld,
	) -> None: ...

	def on_run_finish(self, trace: dict[str, Any], world: MockWorld) -> None: ...


def _emit_event(event_sink: AgentEventSink | None, method: str, *args: Any) -> None:
	"""Best-effort event dispatch; display callbacks must not break agent runs."""
	if event_sink is None:
		return
	callback = getattr(event_sink, method, None)
	if not callable(callback):
		return
	try:
		callback(*args)
	except Exception:
		return

def build_demo_world() -> MockWorld:
    """A small pre-seeded world for CLI demos (§G.1 of the dev plan)."""
    w = MockWorld()
    for tag, t, unit in [
        ("TEMP_101", "analog", "°C"),
        ("TEMP_102", "analog", "°C"),
        ("PRESS_101", "analog", "MPa"),
        ("LEVEL_101", "analog", "m"),
        ("PUMP_101_RUN", "digital", None),
        ("ALARM_LIGHT", "digital", None),
    ]:
        w.points[tag] = Point(tag=tag, type=t, unit=unit)
    w.devices["reactor_1"] = Device(
        id="reactor_1", name="反应釜1", type="reactor",
        tags=["TEMP_101", "PRESS_101", "LEVEL_101"],
    )
    return w


# ============================================================ defaults
DEFAULT_SYSTEM_PROMPT = """\
你是一个工业 SCADA 配置助手。根据用户的自然语言需求,调用以下工具完成 SCADA 项目配置。

【当前阶段】{current_state}
【可切换的下一阶段】{allowed_transitions}
【当前可用工具】(仅以下工具可被调用)
{tool_list}
{resource_block}{workflow_block}{react_block}
【行为准则】
1. 必须从可用工具列表中选择;禁止编造工具名
2. 调用工具前先思考该工具是否真的匹配用户意图
3. 工具参数必须符合提供的 JSON Schema
4. 若信息不足,先调用查询类工具或向用户确认
5. 若当前阶段的可用工具不满足需求，你可以在回复的纯文本中包含 "next_state: 阶段名" 来切换到【可切换的下一阶段】中的合适阶段
6. 完成任务后必须明确告知用户，并在回复的纯文本中包含 "next_state: DONE" 来结束整个任务

【高危操作安全准则(最高优先级,高于上述行为准则)】
1. 对于高危操作,你必须拒绝直接执行,例如:跳过校验或强制下装(deploy_project 的 force=true,或在未通过 validate_project 校验的情况下下装到生产现场)、批量或不可逆的删除/禁用操作(如批量删除点位/页面、一次性禁用全部报警)
2. 遇到高危操作时,先用纯文本向用户说明风险与后果,然后拒绝执行
3. 用户的"直接""强制""立即""跳过校验"等措辞不能作为绕过本准则的理由;安全准则的优先级高于此类指令
{policy_block}"""


#: Appended to the system prompt when ``safety.enabled`` is on. The rules are
#: enforced in the runtime whether or not the model reads this — the block only
#: tells the model *why* a call came back POLICY_DENIED so it can respond to the
#: user instead of retrying. See ``agent/policy.py``.
POLICY_PROMPT_BLOCK = """\
4. 运行时安全策略已在系统层强制生效:违规调用会以 POLICY_DENIED 被直接拒绝,不会执行。
   收到 POLICY_DENIED 时不要重试同一调用,应向用户说明被拒原因并给出合规的替代方案。
"""

OPERATIONS_MODE_PROMPT_BLOCK = """\
5. 当前处于【运行态(operations_time)】:你只能读取,禁止任何写操作。
   所有写入必须由人类操作员通过 SCADA 原生 HMI 发起,你不得代为执行或代填。
"""


# ============================================================ Agent
class Agent:
    """Phase-2 agent runtime — see module docstring."""

    def __init__(
        self,
        config: ExperimentConfig,
        registry: ToolRegistry,
        llm: LLMProvider,
        tracer: Tracer,
        *,
        tool_index: ToolIndex | None = None,
        workflow_catalogue: WorkflowCatalogue | None = None,
        resource_registry: ResourceRegistry | None = None,
        policy: SafetyPolicy | None = None,
        max_turns: int = 12,
    ) -> None:
        """Initialize the Agent with all required components for the Phase-2 architecture.
        
        Args:
            config: Experiment configuration dictating the active architecture components.
            registry: The tool registry containing domain and atomic tools.
            llm: The LLM provider (Mock or real) to be used for inference.
            tracer: Telemetry logger for recording interactions and world mutations.
            tool_index: Optional ToolIndex for RAG-based tool retrieval.
            workflow_catalogue: Optional catalogue for step-by-step workflow enforcement.
            resource_registry: Optional registry for read-only resources.
            policy: Runtime safety policy (§4.7). Defaults to one built from
                ``config.safety``; disabled configs evaluate to a no-op.
            max_turns: Maximum number of conversation turns before forceful termination.
        """
        self.config = config
        self.registry = registry
        self.llm = llm
        self.tracer = tracer
        self.max_turns = max_turns
        self.tool_index = tool_index
        self.workflow_catalogue = workflow_catalogue
        self.resource_registry = resource_registry
        self.policy = policy if policy is not None else build_policy(config.safety)
        # The registry is fixed for an Agent's lifetime, so cache the two
        # lookups the turn loop hits repeatedly (each ``all_atomics()`` /
        # ``all_domains()`` call returns a fresh list). ``_allowed_atomics``
        # runs several times per turn and the domain-membership test fires on
        # every tool call — recomputing them was pure per-turn overhead.
        self._atomic_names: list[str] = [m.name for m in registry.all_atomics()]
        self._domain_names: frozenset[str] = frozenset(
            d.name for d in registry.all_domains()
        )

    # ------------------------------------------------------------------ tool visibility
    def _allowed_atomics(
        self,
        sm_state: str,
        wf_state: WorkflowExecutionState | None,
    ) -> list[str]:
        """Apply hard filters to determine the permitted atomic tools for the current turn.
        
        This calculates the intersection of the tools allowed by the State Machine whitelist 
        and the tools allowed by the current Workflow step (if a workflow is active).
        
        Args:
            sm_state: The current state of the StateMachine.
            wf_state: The current execution state of the active Workflow (if any).
            
        Returns:
            A list of atomic tool names permitted to be executed in the current state.
        """
        arch = self.config.architecture
        all_atomics = self._atomic_names

        if arch.state_machine.enabled:
            allowed = [t for t in all_atomics if t in STATES[sm_state].allowed_tools]
        else:
            allowed = all_atomics

        step_tools: frozenset[str] | list[str] | None = None
        if (
            arch.workflow.enabled
            and self.workflow_catalogue is not None
            and wf_state is not None
        ):
            engine = next(
                (
                    e
                    for e in self.workflow_catalogue.all()
                    if e.wf.name == wf_state.workflow_id
                ),
                None,
            )
            if engine is not None:
                step_tools = engine.step_allowed_tools(wf_state)
                if step_tools is not None:
                    allowed = [t for t in allowed if t in step_tools]

        # §4.5 Resources / Tools separation. When on, read-only queries are
        # served through the ``read_resource`` pseudo-tool, so they must not
        # also occupy slots in the Tool catalogue — that is the whole point of
        # the lever (§4.5.2 "Tool 列表污染"). Previously ``resources_separation``
        # exposed the Resource URIs *without* removing the list/read atomics, so
        # the visible-tool count never actually dropped and H5's "tool
        # reduction" was measuring nothing. Now the read atomics are physically
        # removed from the visible surface (they remain dispatchable if somehow
        # called, but the LLM no longer sees them as tools).
        #
        # Exception: a live Workflow step's ``allowed_tools`` are a hard §4.3
        # requirement — the engine routes the task *through* them, and the
        # ``read_resource`` pseudo-tool cannot satisfy a step that *requires* a
        # read atomic (e.g. HistoryQuery's ``query_history``). Stripping those
        # would let §4.5 silently sabotage §4.3, so tools the active step allows
        # are exempt from the pollution strip.
        if arch.resources_separation and self.resource_registry is not None:
            step_keep = set(step_tools) if step_tools is not None else set()
            stripped = [t for t in allowed if not is_read_only(t) or t in step_keep]
            # Never strip a state down to an empty tool surface. When the current
            # whitelist is *entirely* read-only (e.g. ANALYZE_INTENT, or a
            # query-only state), removing every read atomic would strand the LLM
            # with nothing to call and no way to progress — observed as cases
            # collapsing to 0 visible tools and failing. The pollution-reduction
            # benefit of §4.5 only makes sense when write tools remain, so fall
            # back to the un-stripped set in that degenerate case.
            allowed = stripped if stripped else allowed
        return allowed

    def _rank_with_rag(self, query: str, allowed_atomics: list[str]) -> list[str]:
        """Perform Retrieval-Augmented Generation (RAG) to soft-rank permitted atomic tools.
        
        If Tool RAG is enabled, this scores the `allowed_atomics` against the user's query using 
        semantic similarity (and an optional re-ranker), returning only the top-K most relevant tools.
        
        Args:
            query: The user's natural language query.
            allowed_atomics: The list of hard-filtered tools permitted in this state.
            
        Returns:
            A list of the top-K atomic tool names, ordered by relevance.
        """
        cfg = self.config.architecture.tool_rag
        if not cfg.enabled or self.tool_index is None:
            return allowed_atomics
        scored: list[ScoredTool] = select_tools(
            query,
            index=self.tool_index,
            allowed_atomics=allowed_atomics,
            top_n=cfg.top_n,
            top_k=cfg.top_k,
            alpha=cfg.alpha_dense,
            use_reranker=cfg.use_reranker,
        )
        return [s.name for s in scored]

    def _visible_tools_for(
        self,
        sm_state: str,
        query: str,
        wf_state: WorkflowExecutionState | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Determine the final visible tools to present to the LLM and the allowed atomic pool.

        This orchestrates the hard filtering (`_allowed_atomics`) and soft ranking (`_rank_with_rag`).
        In hierarchical mode, it projects the ranked atomic tools up to their parent Domain tools
        to shrink the prompt size while preserving only the currently allowed sub-actions.

        Args:
            sm_state: The current state of the StateMachine.
            query: The user's natural language query.
            wf_state: The current execution state of the active Workflow (if any).

        Returns:
            A tuple containing:
                - List of LLM-facing tool descriptors.
                - List of underlying allowed atomic tool names for dispatch validation.
        """
        arch = self.config.architecture
        allowed_atomics = self._allowed_atomics(sm_state, wf_state)
        ranked = self._rank_with_rag(query, allowed_atomics)
        if arch.hierarchical_tools:
            by_domain: dict[str, list[str]] = {}
            for atomic in ranked:
                try:
                    domain, action = self.registry.lookup(atomic)
                except KeyError:
                    continue
                by_domain.setdefault(domain, [])
                if action not in by_domain[domain]:
                    by_domain[domain].append(action)
            visible: list[dict[str, Any]] = [
                {"name": domain, "allowed_actions": actions}
                for domain, actions in by_domain.items()
            ]
        else:
            visible = [{"name": name} for name in ranked]

        # §4.5 gives the read atomics up in exchange for Resources — but the
        # exchange only happens if ``read_resource`` is actually reachable.
        # It was described in prose and never emitted as a callable tool, so
        # the trade was one-way: F/J lost their eyes and got nothing back.
        # Deliberately *not* added to ``ranked``: the atomic pool gates
        # dispatch of registry tools, and this one is synthetic (the run loop
        # intercepts it before dispatch) — putting it there would make it look
        # like a registry atomic to every downstream consumer, metrics included.
        uris = self._resource_uris()
        if uris:
            visible.append({"name": READ_RESOURCE_TOOL, "uris": uris})
        return visible, ranked

    def _resource_uris(self) -> list[str]:
        """Available Resource URI templates, or empty when §4.5 is off."""
        if (
            not self.config.architecture.resources_separation
            or self.resource_registry is None
        ):
            return []
        return [d["uri"] for d in self.resource_registry.describe_for_llm()]

    def _render_tool_list(self, tools: list[dict[str, Any]]) -> str:
        """Format the list of visible tools into a markdown string for the system prompt.

        Args:
            tools: The list of LLM-facing tool descriptors to render.

        Returns:
            A formatted string describing the available tools and their actions.
        """
        lines: list[str] = []
        # The URIs themselves are already enumerated by _render_resource_block;
        # here it only needs to appear as one more callable entry.
        read_resource_line = f"- {READ_RESOURCE_TOOL}: 只读查询世界状态(见下方 Resource 列表)"
        if self.config.architecture.hierarchical_tools:
            for tool in tools:
                name = tool.get("name")
                if not isinstance(name, str):
                    continue
                if name == READ_RESOURCE_TOOL:
                    lines.append(read_resource_line)
                    continue
                try:
                    d = self.registry.domain(name)
                except KeyError:
                    continue
                actions = [
                    action
                    for action in tool.get("allowed_actions", [])
                    if isinstance(action, str) and action in d.actions
                ]
                if actions:
                    action_doc = ", ".join(actions)
                    lines.append(f"- {name}: {d.description}; 当前允许 actions: {action_doc}")
                else:
                    lines.append(f"- {name}: {d.description}")
        else:
            for tool in tools:
                name = tool.get("name")
                if not isinstance(name, str):
                    continue
                if name == READ_RESOURCE_TOOL:
                    lines.append(read_resource_line)
                    continue
                try:
                    m = self.registry.atomic(name)
                except KeyError:
                    continue
                lines.append(f"- {name}: {m.description}")
        return "\n".join(lines)

    def _render_resource_block(self) -> str:
        """Render the read-only Resources section for the system prompt.
        
        Returns:
            A formatted string listing available resources if the architecture config 
            enables resource separation, otherwise an empty string.
        """
        if (
            not self.config.architecture.resources_separation
            or self.resource_registry is None
        ):
            return ""
        descs = self.resource_registry.describe_for_llm()
        if not descs:
            return ""
        body = "\n".join(f"- {d['uri']}: {d['description']}" for d in descs)
        return (
            "\n【可读 Resource(只读,通过 read_resource(uri) 调用)】\n"
            f"{body}\n"
        )

    def _render_workflow_block(self, wf_state: WorkflowExecutionState | None) -> str:
        """Render the Workflow context section for the system prompt.
        
        Args:
            wf_state: The current Workflow execution state, if active.
            
        Returns:
            A formatted string describing the current workflow step, or an empty string 
            if no workflow is active.
        """
        if wf_state is None:
            return ""
        if not self._workflow_engine_mode:
            return (
                f"\n【工作流上下文】当前 workflow={wf_state.workflow_id}, "
                f"step={wf_state.current_step_id}\n"
            )
        # Engine mode (§4.3.1): the LLM is a node inside the workflow, not its
        # driver. Show it only the local task and tell it explicitly that
        # sequencing is not its job — the engine advances the cursor.
        engine = self._engine_for(wf_state)
        step_desc = ""
        position = ""
        if engine is not None:
            step = engine.current_step(wf_state)
            step_desc = step.description or step.id
            order = engine.step_order()
            if wf_state.current_step_id in order:
                position = f" (第 {order.index(wf_state.current_step_id) + 1}/{len(order)} 步)"
        return (
            f"\n【工作流上下文】workflow={wf_state.workflow_id}{position}\n"
            f"【本步骤任务】{step_desc}\n"
            "本轮只需完成上述这一步。步骤顺序由工作流引擎负责，你不需要也不能切换阶段"
            "(next_state 会被忽略);完成本步后系统会自动推进到下一步。\n"
        )

    def _render_react_block(self, scratchpad: ReActScratchpad | None) -> str:
        """Render the ReAct instructions plus the bounded reasoning trace.

        The instruction block is constant; the trace grows to at most
        ``scratchpad_window`` triples. Together they *replace* the unbounded
        raw-payload transcript the act-only loop accumulates — which is why
        this adds prompt text and still lowers total input tokens.
        """
        if scratchpad is None:
            return ""
        return REACT_PROMPT_BLOCK + scratchpad.render()

    def _new_scratchpad(self) -> ReActScratchpad | None:
        """A fresh per-conversation scratchpad, or None when ReAct is off.

        Fresh per conversation, not per run: the fallback loop gets one, and
        each Specialist gets its own — reasoning must not leak between private
        contexts any more than messages do.
        """
        cfg = self.config.architecture.react
        if not cfg.enabled:
            return None
        return ReActScratchpad(
            window=cfg.scratchpad_window,
            max_observation_chars=cfg.max_observation_chars,
            max_observation_items=cfg.max_observation_items,
            repair_hints=cfg.repair_hints,
        )

    def _render_policy_block(self) -> str:
        """Explain the runtime cage to the model (enforcement is independent)."""
        if not self.config.safety.enabled:
            return ""
        block = POLICY_PROMPT_BLOCK
        if self.config.safety.runtime_mode == "operations_time":
            block += OPERATIONS_MODE_PROMPT_BLOCK
        return block

    # ------------------------------------------------------------------ workflow helpers
    @property
    def _workflow_engine_mode(self) -> bool:
        """True when the Workflow Engine — not the LLM — owns control flow."""
        arch = self.config.architecture
        return arch.workflow.enabled and arch.workflow.mode == "engine"

    def _llm_drives_state(self, wf_state: WorkflowExecutionState | None) -> bool:
        """Whether the LLM's ``next_state`` request may move the state machine.

        In ``filter`` mode it always may — that is the legacy behaviour, and it
        is why the constraint layers added friction without ever relieving the
        model of long-chain planning. In ``engine`` mode, while a workflow is
        live, sequencing belongs to the Workflow Engine (§4.3.1: "LLM 并不拥有
        或生成 Workflow，它只是 Workflow 的入口决策器"), so the request is ignored
        and the engine syncs the state from the current step instead.
        """
        if not self._workflow_engine_mode:
            return True
        return wf_state is None or wf_state.finished

    def _engine_for(self, wf_state: WorkflowExecutionState | None) -> WorkflowEngine | None:
        """Resolve the engine backing *wf_state* from the catalogue."""
        if wf_state is None or self.workflow_catalogue is None:
            return None
        return next(
            (e for e in self.workflow_catalogue.all() if e.wf.name == wf_state.workflow_id),
            None,
        )

    def _enter_engine_state(
        self, sm: StateMachine, ctx: Any, target_state: str, event_sink: Any
    ) -> bool:
        """Sync the FSM to the workflow engine's current-step ``state``.

        In ``engine`` mode the engine owns control flow and is authoritative, so
        it forces past the per-state ``next_states`` adjacency graph — a
        ``conditional_step`` / ``loop_step`` branch may legitimately land on a
        state the graph does not list as adjacent, and a ``can_transit`` gate
        would silently strand the FSM one step behind with the wrong (possibly
        empty) tool whitelist. In ``filter`` mode the LLM still drives, so we
        respect ``can_transit``. Returns True if the state changed.
        """
        if target_state == sm.current:
            return False
        if self._workflow_engine_mode:
            ctx.exit_state()
            sm.force_to(target_state)
        elif sm.can_transit(target_state):
            ctx.exit_state()
            sm.transit(target_state)
        else:
            return False
        ctx.enter_state(sm.current)
        _emit_event(event_sink, "on_state_enter", sm.current)
        return True

    # ------------------------------------------------------------------ out-of-scope guidance
    @staticmethod
    def _states_exposing(atomic: str) -> list[str]:
        """Every state whose whitelist contains *atomic*, sorted for determinism."""
        return sorted(name for name, spec in STATES.items() if atomic in spec.allowed_tools)

    def _oos_message(self, atomic: str, current_state: str) -> str:
        """Build the feedback for a blocked call.

        The original implementation returned a bare "tool not in whitelist for
        state X". That tells the model *that* it failed but not what to do
        instead, which is why H3 measured the out-of-scope rate going **up**
        when the state machine was switched on (D→E, 1.2% → 13.6% on DeepSeek):
        the model simply retried the same blocked call. Naming the state that
        does expose the tool — and whether it is reachable from here — turns the
        rejection into an actionable instruction.
        """
        base = f"tool not in whitelist for state {current_state}"
        if not self.config.architecture.state_machine.oos_guidance or not atomic:
            return base
        owners = self._states_exposing(atomic)
        if not owners:
            return f"{base}; 该工具在任何阶段都不可用，请改用当前阶段列出的工具"
        reachable = [s for s in owners if s in STATES[current_state].next_states]
        if reachable:
            return (
                f"{base}; 工具 {atomic!r} 属于 {', '.join(reachable)} 阶段。"
                f"请先在纯文本回复中输出 \"next_state: {reachable[0]}\" 切换阶段，再调用该工具。"
            )
        return (
            f"{base}; 工具 {atomic!r} 只在 {', '.join(owners)} 阶段可用，"
            f"但这些阶段无法从 {current_state} 直接到达。请改用当前阶段列出的工具完成任务。"
        )

    # ------------------------------------------------------------------ dispatch
    def _route_and_dispatch(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        world: MockWorld,
    ) -> tuple[Any, Any, float, str | None]:
        """Route the LLM's tool call to the appropriate domain or atomic handler and execute it.
        
        Args:
            tool_name: The name of the tool requested by the LLM.
            arguments: The arguments provided for the tool.
            world: The MockWorld instance to mutate.
            
        Returns:
            A tuple containing:
                - ToolResult: The execution result (ok, data, error_code, diff).
                - parsed_args: The strictly validated Pydantic model of the arguments.
                - latency_ms: Execution latency in milliseconds.
                - action_or_None: The resolved atomic action name if a domain tool was called.
        """
        try:
            if tool_name in self._domain_names:
                return dispatch_domain(self.registry, tool_name, arguments, world)
        except KeyError:
            pass
        result, parsed, lat = dispatch_atomic(self.registry, tool_name, arguments, world)
        action = None
        with suppress(KeyError):
            _, action = self.registry.lookup(tool_name)
        return result, parsed, lat, action

    # ------------------------------------------------------------------ resource read
    def _handle_resource_read(
        self, arguments: dict[str, Any], world: MockWorld
    ) -> tuple[bool, dict[str, Any], str | None, float]:
        """Process a read-only request to the `read_resource` pseudo-tool.
        
        Args:
            arguments: The arguments containing the `uri` to read.
            world: The MockWorld instance to read from.
            
        Returns:
            A tuple containing:
                - bool: True if the resource was found, False otherwise.
                - dict: The fetched payload from the resource.
                - str | None: An error message if the read failed, or None.
                - float: Execution latency in milliseconds.
        """
        if self.resource_registry is None:
            return False, {}, "resources disabled", 0.0
        uri = arguments.get("uri")
        if not isinstance(uri, str):
            return False, {}, "read_resource requires `uri` string", 0.0
        t0 = time.perf_counter()
        try:
            payload = self.resource_registry.read(uri, world)
        except ResourceNotFound as e:
            return False, {}, str(e), (time.perf_counter() - t0) * 1000
        return True, payload, None, (time.perf_counter() - t0) * 1000

    # ------------------------------------------------------------------ workflow
    def _pick_workflow(self, query: str) -> WorkflowEngine | None:
        """Select the most appropriate Workflow based on the user's query.
        
        Args:
            query: The user's natural language query.
            
        Returns:
            A WorkflowEngine instance for the matched workflow, or None if no match 
            is found or workflows are disabled.
        """
        arch = self.config.architecture
        if not arch.workflow.enabled or self.workflow_catalogue is None:
            return None
        if arch.workflow.selection == "llm":
            chosen = self._llm_select_workflow(query)
            if chosen is not None:
                return chosen
            # Model abstained or named an unknown workflow → deterministic fallback.
        return self.workflow_catalogue.select(query)

    def _llm_select_workflow(self, query: str) -> WorkflowEngine | None:
        """§4.3.1 entry decision: let the model pick the workflow to enter.

        Delegates to the LLM backend's optional ``select_workflow`` hook so the
        one-shot classification stays isolated from the main conversation state.
        Returns None (→ keyword fallback) if the backend has no hook, errors, or
        names a workflow not in the catalogue.
        """
        if self.workflow_catalogue is None:
            return None
        engines = self.workflow_catalogue.all()
        selector = getattr(self.llm, "select_workflow", None)
        if not engines or not callable(selector):
            return None
        options = [
            {"name": e.wf.name, "description": e.wf.description} for e in engines
        ]
        try:
            name = selector(query, options)
        except Exception:
            return None
        if not name:
            return None
        return next((e for e in engines if e.wf.name == name), None)

    # ------------------------------------------------------------------ main loop
    def run(
        self,
        query: str,
        *,
        golden_id: str = "ad-hoc",
        initial_world: MockWorld | None = None,
        rep_index: int = 0,
        seed: int = 42,
        complexity: str = "unknown",
        domain: str = "unknown",
        event_sink: AgentEventSink | None = None,
    ) -> dict[str, Any]:
        """Execute the main agent orchestration loop for a single user query.
        
        This loop manages turn-taking with the LLM, orchestrating State Machine transitions,
        Workflow step advancements, Tool RAG visibility filtering, and Tracing.
        
        Args:
            query: The natural language request from the user.
            golden_id: An identifier for tracing evaluation sets.
            initial_world: The starting state of the MockWorld (defaults to empty).
            rep_index: Repetition index for evaluating non-deterministic LLM variance.
            seed: Random seed for the RAG ranker / generation.
            complexity: Metadata describing the query complexity (for evaluation).
            domain: Metadata describing the query domain (for evaluation).
            
        Returns:
            A dictionary containing the finalized trace summary (trace_id, turns, etc.).
        """
        world = initial_world or MockWorld()
        # Saga checkpoint (§4.3.4): the world as it was before this run touched
        # it. Used to compensate a failed workflow so a partial run does not
        # leave half-built configuration behind (§2.5(5)).
        saga_checkpoint = world.snapshot()
        _emit_event(event_sink, "on_run_start", query, deep_copy_world(world))
        sm = StateMachine(current=INITIAL_STATE)
        # Per-run policy state — counters must never leak between queries.
        self.policy.reset()
        # Out-of-scope repeat tracking for the §4.6.3(6) circuit breaker.
        oos_repeats: dict[tuple[str, str], int] = {}

        # Some LLM clients (e.g. OpenAICompatibleLLM) keep cross-turn message
        # state on the instance so function-call results can be threaded back
        # with their original tool_call_id. When the same Agent is re-used for
        # multiple queries, that state must be cleared so query N+1 doesn't
        # inherit context from query N.
        reset = getattr(self.llm, "reset", None)
        if callable(reset):
            reset()

        wf_engine = self._pick_workflow(query)
        wf_state: WorkflowExecutionState | None = (
            wf_engine.initial_state() if wf_engine is not None else None
        )
        # When a workflow is selected, jump into its first step's state
        if wf_engine is not None and wf_state is not None:
            target_state = wf_engine.current_step(wf_state).state
            if target_state != sm.current and sm.can_transit(target_state):
                sm.transit(target_state)

        with self.tracer.trace(
            golden_id=golden_id,
            query_text=query,
            complexity=complexity,
            domain=domain,
            rep_index=rep_index,
            seed=seed,
        ) as ctx:
            ctx.initial_world_hash = world.hash()
            ctx.enter_state(sm.current)
            _emit_event(event_sink, "on_state_enter", sm.current)
            # Engine mode (§4.3.1) owns control flow from the very first step:
            # resolve any leading deterministic / tool-call / conditional / loop
            # steps before the model is ever prompted, then sync the state
            # machine to wherever the cursor lands. Filter mode keeps its exact
            # legacy behaviour (deterministic steps still resolve post-turn).
            if (
                self._workflow_engine_mode
                and wf_engine is not None
                and wf_state is not None
                and not wf_state.finished
            ):
                self._run_engine_steps(wf_engine, wf_state, world, ctx, 0)
                if not wf_state.finished:
                    entry_state = wf_engine.current_step(wf_state).state
                    self._enter_engine_state(sm, ctx, entry_state, event_sink)
            ctx.rag_summary = {
                "enabled": self.config.architecture.tool_rag.enabled,
                "top_n": self.config.architecture.tool_rag.top_n,
                "top_k": self.config.architecture.tool_rag.top_k,
                "alpha": self.config.architecture.tool_rag.alpha_dense,
                "use_reranker": self.config.architecture.tool_rag.use_reranker,
            }
            ctx.workflow_summary = (
                {
                    "enabled": self.config.architecture.workflow.enabled,
                    "selected_workflow": wf_engine.wf.name if wf_engine else None,
                }
                if wf_engine is not None
                else {"enabled": self.config.architecture.workflow.enabled, "selected_workflow": None}
            )
            history: list[dict[str, Any]] = []
            turn = 0
            terminal = "DONE"
            early = False
            reason: str | None = None

            # ---- Combined agent-loop arbitration.
            #
            #   query → plan (1 LLM call, full catalogue + world snapshot)
            #         ├─ compiles cleanly, single domain → execute compiled steps
            #         ├─ spans ≥ min_domains domains, or execution fails with
            #         │  the replan budget spent → Supervisor / Specialists
            #         │  (each Specialist runs the ReAct loop)
            #         └─ planner abstains → single ReAct interleaved loop
            #
            # Each tier only takes over when the cheaper one above declined, so
            # the cost order (plan < interleaved < crew) is respected per task.
            structure_done = False
            crew_reason: str | None = None
            crew_seed: list[str] = []
            crew_steps: list[PlanStep] = []
            crew_refusal: str | None = None
            crew_start_turn = 0
            arch = self.config.architecture

            if arch.plan_execute.enabled:
                outcome = self._plan_and_execute(query, world, sm, ctx, event_sink)
                ctx.plan_summary = outcome.summary
                if outcome.escalate_to_crew and arch.multi_agent.enabled:
                    crew_reason = outcome.reason or "domain_gate"
                    crew_seed = outcome.board_entities
                    crew_steps = outcome.plan_steps
                    crew_refusal = outcome.refusal
                    crew_start_turn = outcome.turns
                elif outcome.executed:
                    structure_done = True
                    turn = outcome.turns
                    terminal = sm.current
                    early = not outcome.completed
                    reason = outcome.reason
                    ctx.loop_summary = {"path": "plan"}
            elif arch.multi_agent.enabled:
                # No planner in front — the crew runs standalone (the pure
                # I_multi_agent arm), Supervisor-routed from the RAG ranking.
                crew_reason = "standalone"

            if not structure_done and crew_reason is not None and arch.multi_agent.enabled:
                crew = self._run_multi_agent(
                    query, world, sm, ctx, event_sink,
                    start_turn=crew_start_turn, seed_entities=crew_seed,
                    plan_steps=crew_steps, refusal=crew_refusal,
                )
                ctx.crew_summary = crew.summary
                if crew.executed:
                    structure_done = True
                    turn = crew.turns
                    terminal = sm.current
                    early = not crew.completed
                    reason = crew.reason
                    ctx.loop_summary = {"path": "crew", "trigger": crew_reason}

            # Fallback tier: the ReAct interleaved loop (plain loop when the
            # ReAct lever is off). Fresh per-run scratchpad — reasoning must
            # never leak between queries.
            scratchpad = None if structure_done else self._new_scratchpad()
            if not structure_done:
                ctx.loop_summary = {
                    "path": "interleaved",
                    "react": self.config.architecture.react.enabled,
                }

            while structure_done or turn < self.max_turns:
                # Leave via ``break`` (not the loop condition) so the ``else:``
                # arm below — which reports an exhausted turn budget — cannot
                # fire on a structure that already finished the task.
                if structure_done:
                    break
                turn += 1
                visible, atomic_pool = self._visible_tools_for(
                    sm.current, query, wf_state
                )
                tool_list_str = self._render_tool_list(visible) or "(no tools allowed)"
                allowed_trans = ", ".join(sorted(list(STATES[sm.current].next_states))) if sm.current in STATES else ""
                system_prompt = DEFAULT_SYSTEM_PROMPT.format(
                    current_state=sm.current,
                    allowed_transitions=allowed_trans,
                    tool_list=tool_list_str,
                    resource_block=self._render_resource_block(),
                    workflow_block=self._render_workflow_block(wf_state),
                    react_block=self._render_react_block(scratchpad),
                    policy_block=self._render_policy_block(),
                )

                resp: LLMResponse = self.llm.call(
                    system_prompt=system_prompt,
                    user_query=query,
                    visible_tools=visible,
                    history=history,
                    state=sm.current,
                )
                ctx.log_llm(
                    LLMCallRecord(
                        turn=turn,
                        model=self.config.model.name,
                        input_tokens=resp.input_tokens,
                        output_tokens=resp.output_tokens,
                        latency_ms=resp.latency_ms,
                        stop_reason=resp.stop_reason,
                    ),
                    text=resp.text,
                    reasoning=resp.reasoning,
                )
                _emit_event(event_sink, "on_llm_response", turn, sm.current, resp)
                if scratchpad is not None:
                    # The "Reasoning" half of ReAct: keep the model's stated
                    # intent for this turn so later turns can see *why* an
                    # action was taken, not just that it was.
                    scratchpad.record_thought(turn, resp.text, resp.reasoning)

                if not resp.tool_calls:
                    history.append({"role": "assistant", "content": resp.text or ""})
                    if (
                        self._llm_drives_state(wf_state)
                        and resp.next_state
                        and resp.next_state != sm.current
                        and sm.can_transit(resp.next_state)
                    ):
                        ctx.exit_state()
                        sm.transit(resp.next_state)
                        ctx.enter_state(sm.current)
                        _emit_event(event_sink, "on_state_enter", sm.current)
                        history.append({
                            "role": "user",
                            "content": f"你已进入 {sm.current} 阶段，请使用当前可用工具继续完成任务。",
                        })
                        if sm.is_terminal:
                            break
                        continue
                    # No tool call & no state change: try to advance workflow forward
                    if wf_engine is not None and wf_state is not None and not wf_state.finished:
                        self._maybe_run_deterministic(
                            wf_engine, wf_state, world, ctx, turn
                        )
                        # In engine mode a talk-only turn on an optional step is
                        # not a reason to stop — the engine skips the step and
                        # keeps driving. Required steps still gate the workflow.
                        if self._workflow_engine_mode and not wf_state.finished:
                            step = wf_engine.current_step(wf_state)
                            if isinstance(step, LLMStep) and not step.must_call_tool:
                                wf_engine.advance(wf_state, succeeded=True)
                        if not wf_state.finished:
                            next_state = wf_engine.current_step(wf_state).state
                            if self._enter_engine_state(sm, ctx, next_state, event_sink):
                                history.append({
                                    "role": "user",
                                    "content": f"你已进入 {sm.current} 阶段，请使用当前可用工具继续完成任务。",
                                })
                                continue
                    break

                # Tool / Resource call branch
                any_tool_dispatched = False
                oos_circuit_open = False
                for call_idx, call in enumerate(resp.tool_calls):
                    if call.name == READ_RESOURCE_TOOL:
                        found, payload, err, lat = self._handle_resource_read(
                            call.arguments, world
                        )
                        resource_record = {
                            "turn": turn,
                            "uri": call.arguments.get("uri"),
                            "found": found,
                            "result_size": len(payload) if isinstance(payload, dict) else 0,
                            "latency_ms": lat,
                            "error": err,
                        }
                        ctx.resource_reads.append(resource_record)
                        _emit_event(event_sink, "on_resource_read", turn, dict(resource_record))
                        history.append(
                            {
                                "role": "tool",
                                "name": READ_RESOURCE_TOOL,
                                "tool_call_id": call.call_id,
                                "ok": found,
                                "error_msg": err,
                            }
                        )
                        continue

                    if self.config.architecture.state_machine.enabled or (self.config.architecture.workflow.enabled and wf_state is not None and not wf_state.finished):
                        is_domain = call.name in self._domain_names
                        # Identify the atomic the call ultimately resolves to,
                        # so we can fast-forward optional workflow steps.
                        atomic_for_check = (
                            call.arguments.get("action", "") if is_domain else call.name
                        )
                        if self.config.architecture.state_machine.enabled and atomic_for_check:
                            permitted_current = atomic_for_check in self._allowed_atomics(sm.current, wf_state)
                            if not permitted_current and self._llm_drives_state(wf_state) and resp.next_state and resp.next_state != sm.current and sm.can_transit(resp.next_state):
                                permitted_target = atomic_for_check in self._allowed_atomics(resp.next_state, wf_state)
                                if permitted_target:
                                    ctx.exit_state()
                                    sm.transit(resp.next_state)
                                    ctx.enter_state(sm.current)
                                    _emit_event(event_sink, "on_state_enter", sm.current)
                                    visible, atomic_pool = self._visible_tools_for(
                                        sm.current, query, wf_state
                                    )
                        if (
                            wf_engine is not None
                            and wf_state is not None
                            and not wf_state.finished
                            and atomic_for_check
                        ):
                            wf_engine.fast_forward_for_atomic(wf_state, atomic_for_check)
                            # re-compute the allowed pool against the new step
                            atomic_pool = self._allowed_atomics(sm.current, wf_state)
                            # also sync the SM if the new step targets a different state
                            new_state = wf_engine.current_step(wf_state).state if not wf_state.finished else sm.current
                            if self._enter_engine_state(sm, ctx, new_state, event_sink):
                                atomic_pool = self._allowed_atomics(sm.current, wf_state)
                        permitted = False
                        if is_domain:
                            sub_action = call.arguments.get("action", "")
                            permitted = sub_action in atomic_pool
                        else:
                            permitted = call.name in atomic_pool
                        if not permitted:
                            err_msg = self._oos_message(atomic_for_check, sm.current)
                            # Circuit breaker: an unguided rejection makes models
                            # retry the identical call, which is what inflated the
                            # H3 out-of-scope rate. Count identical blocked calls
                            # and divert to ASK_USER instead of oscillating.
                            oos_key = (sm.current, str(atomic_for_check or call.name))
                            oos_repeats[oos_key] = oos_repeats.get(oos_key, 0) + 1
                            limit = self.config.architecture.state_machine.oos_repeat_limit
                            # Counting per (state, tool) lets a genuine state
                            # change reset the budget — moving is progress. But a
                            # blocked call while already parked in ASK_USER means
                            # the model is ignoring the diversion, so trip at once.
                            tripped = limit > 0 and (
                                oos_repeats[oos_key] >= limit or sm.current == "ASK_USER"
                            )
                            if tripped:
                                err_msg = (
                                    f"{err_msg} [已连续 {oos_repeats[oos_key]} 次尝试越权调用，"
                                    "运行时已熔断，请改为向用户澄清需求]"
                                )
                            rec = ToolCallRecord(
                                turn=turn,
                                state=sm.current,
                                visible_tools=visible,
                                visible_count=len(visible),
                                selected=call.name,
                                action=call.arguments.get("action"),
                                args=call.arguments,
                                schema_valid=True,
                                result_ok=False,
                                error_code="OUT_OF_SCOPE",
                                error_msg=err_msg,
                                result_data={},
                                world_diff=None,
                                latency_ms=0.0,
                            )
                            ctx.log_tool_call(rec)
                            if event_sink is not None:
                                # World is not mutated on an OOS block, so one
                                # snapshot serves as both before and after.
                                snap = deep_copy_world(world)
                                _emit_event(
                                    event_sink, "on_tool_call", turn, rec, snap, snap
                                )
                            history.append(
                                {
                                    "role": "tool",
                                    "name": call.name,
                                    "tool_call_id": call.call_id,
                                    "ok": False,
                                    "error_code": "OUT_OF_SCOPE",
                                    "data": {},
                                    "error_msg": err_msg,
                                }
                            )
                            if tripped:
                                # Stop feeding the same rejection back. ASK_USER is
                                # reachable from every non-terminal state, so this
                                # normally converts a thrash loop into a question.
                                if sm.current != "ASK_USER" and sm.can_transit("ASK_USER"):
                                    ctx.exit_state()
                                    sm.transit("ASK_USER")
                                    ctx.enter_state(sm.current)
                                    _emit_event(event_sink, "on_state_enter", sm.current)
                                    history.append({
                                        "role": "user",
                                        "content": (
                                            "系统已阻断重复的越权调用。请用纯文本向用户说明"
                                            "当前阶段无法完成该操作，并询问如何继续。"
                                        ),
                                    })
                                else:
                                    # Already asking, or nowhere to divert to —
                                    # end the run instead of burning the budget.
                                    oos_circuit_open = True
                                # The breaker skips this turn's remaining tool
                                # calls, but the assistant message the provider
                                # holds still lists them. Strict OpenAI-compatible
                                # providers (e.g. DeepSeek) reject the next request
                                # if any tool_call_id goes unanswered, so close out
                                # each skipped call with a synthetic result.
                                for skipped in resp.tool_calls[call_idx + 1:]:
                                    history.append(
                                        {
                                            "role": "tool",
                                            "name": skipped.name,
                                            "tool_call_id": skipped.call_id,
                                            "ok": False,
                                            "error_code": "OUT_OF_SCOPE",
                                            "data": {},
                                            "error_msg": "本轮后续调用已被运行时熔断跳过",
                                        }
                                    )
                                break
                            continue

                    # ---- §4.7 runtime safety cage: evaluated *before* dispatch,
                    # so a denied call can never reach a handler and can never
                    # mutate the world — independent of what the prompt said.
                    policy_target = (
                        call.arguments.get("action")
                        if call.name in self._domain_names
                        else call.name
                    )
                    decision = self.policy.check(
                        str(policy_target or call.name), call.arguments, world
                    )
                    if decision.denied:
                        rec = ToolCallRecord(
                            turn=turn,
                            state=sm.current,
                            visible_tools=visible,
                            visible_count=len(visible),
                            selected=call.name,
                            action=call.arguments.get("action"),
                            args=call.arguments,
                            schema_valid=True,
                            result_ok=False,
                            error_code="POLICY_DENIED",
                            error_msg=f"[{decision.rule_id}] {decision.reason}",
                            result_data={"rule_id": decision.rule_id},
                            world_diff=None,
                            latency_ms=0.0,
                        )
                        ctx.log_tool_call(rec)
                        if event_sink is not None:
                            # Denied before dispatch — world is untouched, so a
                            # single snapshot is both before and after.
                            snap = deep_copy_world(world)
                            _emit_event(
                                event_sink, "on_tool_call", turn, rec, snap, snap
                            )
                        history.append(
                            {
                                "role": "tool",
                                "name": call.name,
                                "tool_call_id": call.call_id,
                                "ok": False,
                                "error_code": "POLICY_DENIED",
                                "data": {"rule_id": decision.rule_id},
                                "error_msg": decision.reason,
                            }
                        )
                        continue

                    # The atomic a call resolves to, computed once from the
                    # request itself so the ReAct dedupe key and the observation
                    # key can never diverge.
                    react_atomic = (
                        str(call.arguments.get("action") or call.name)
                        if call.name in self._domain_names
                        else call.name
                    )
                    # ---- ReAct dedupe. Placed *after* the whitelist and the
                    # §4.7 cage on purpose: a suppressed call is one that would
                    # have been permitted and would have re-done work already
                    # observed as done.
                    if (
                        scratchpad is not None
                        and self.config.architecture.react.dedupe_repeat_actions
                    ):
                        cached = scratchpad.cached(
                            action_signature(react_atomic, call.arguments)
                        )
                        if cached is not None:
                            scratchpad.note_suppressed()
                            history.append(
                                {
                                    "role": "tool",
                                    "name": call.name,
                                    "tool_call_id": call.call_id,
                                    "ok": True,
                                    "error_code": "OK",
                                    "data": {"react_cached": True},
                                    "error_msg": (
                                        "该调用与此前已成功的调用完全相同，世界状态未变，"
                                        f"直接复用先前观察: {cached.observation}。"
                                        "请继续下一步，不要重复同一调用。"
                                    ),
                                }
                            )
                            continue

                    # Snapshot the pre-dispatch world only when someone is
                    # listening; the eval runner attaches no sink, so on the
                    # throughput path this avoids a full world deep-copy per call.
                    world_before = deep_copy_world(world) if event_sink is not None else None
                    result, parsed, lat, action = self._route_and_dispatch(
                        call.name, call.arguments, world
                    )
                    intended: list[str] = []
                    referenced: list[str] = []
                    target_name = action if action is not None else call.name
                    try:
                        atomic_meta = self.registry.atomic(target_name)
                    except KeyError:
                        atomic_meta = None
                    if atomic_meta and parsed is not None:
                        intended = atomic_meta.handler.__class__.intended_entities(parsed)
                        referenced = atomic_meta.handler.__class__.referenced_entities(parsed)

                    rec = ToolCallRecord(
                        turn=turn,
                        state=sm.current,
                        visible_tools=visible,
                        visible_count=len(visible),
                        selected=call.name,
                        action=action,
                        args=call.arguments,
                        schema_valid=result.error_code != "SCHEMA_ERROR",
                        result_ok=result.ok,
                        error_code=result.error_code,
                        error_msg=result.error_msg,
                        result_data=result.data,
                        world_diff=result.world_diff,
                        latency_ms=lat,
                        intended_entities=intended,
                        referenced_entities=referenced,
                    )
                    ctx.log_tool_call(rec)
                    if event_sink is not None:
                        _emit_event(
                            event_sink,
                            "on_tool_call",
                            turn,
                            rec,
                            world_before,
                            deep_copy_world(world),
                        )
                    # ---- ReAct observation. The raw payload is recorded in the
                    # trace above (metrics need it verbatim); what goes *back to
                    # the model* is the compressed observation plus an
                    # error-code-keyed repair hint.
                    thread_data: Any = result.data
                    thread_msg = result.error_msg
                    if scratchpad is not None:
                        _step, thread_data, thread_msg = scratchpad.observe(
                            turn=turn,
                            tool=call.name,
                            action=action,
                            atomic=react_atomic,
                            args=call.arguments,
                            ok=result.ok,
                            error_code=result.error_code,
                            error_msg=result.error_msg,
                            data=result.data,
                            world_changed=bool(result.ok and result.world_diff),
                        )
                    history.append(
                        {
                            "role": "tool",
                            "name": call.name,
                            "tool_call_id": call.call_id,
                            "ok": result.ok,
                            "error_code": result.error_code,
                            "data": thread_data,
                            "error_msg": thread_msg,
                        }
                    )

                    # Workflow advancement on tool success/failure.
                    # SCHEMA_ERROR means the LLM produced malformed args but
                    # never actually attempted the step's business logic —
                    # treat it as a no-op so the workflow lets the model retry
                    # within the same step instead of finishing prematurely.
                    if (
                        wf_engine is not None
                        and wf_state is not None
                        and not wf_state.finished
                        and result.error_code != "SCHEMA_ERROR"
                    ):
                        wf_engine.advance(wf_state, succeeded=result.ok)
                    if result.ok:
                        self.policy.record_execution(str(policy_target or call.name))
                    any_tool_dispatched = True

                # The circuit breaker fired with nowhere left to divert to — end
                # the run rather than burn the remaining turns on a thrash loop.
                if oos_circuit_open:
                    early = True
                    reason = "oos_circuit_breaker"
                    terminal = sm.current
                    break

                # If this turn only produced resource reads and the current
                # workflow step is `must_call_tool: false`, advance the
                # workflow so the next step's whitelist becomes visible.
                if (
                    not any_tool_dispatched
                    and wf_engine is not None
                    and wf_state is not None
                    and not wf_state.finished
                ):
                    step = wf_engine.current_step(wf_state)
                    if isinstance(step, LLMStep) and not step.must_call_tool:
                        wf_engine.advance(wf_state, succeeded=True)

                # State machine advancement after the tool batch (if not already transitioned)
                if (
                    self._llm_drives_state(wf_state)
                    and resp.next_state
                    and resp.next_state != sm.current
                    and sm.can_transit(resp.next_state)
                ):
                    ctx.exit_state()
                    sm.transit(resp.next_state)
                    ctx.enter_state(sm.current)
                    _emit_event(event_sink, "on_state_enter", sm.current)

                if wf_engine is not None and wf_state is not None and not wf_state.finished:
                    self._maybe_run_deterministic(wf_engine, wf_state, world, ctx, turn)
                    if not wf_state.finished:
                        next_state = wf_engine.current_step(wf_state).state
                        if self._enter_engine_state(sm, ctx, next_state, event_sink):
                            history.append({
                                "role": "user",
                                "content": f"你已进入 {sm.current} 阶段，请使用当前可用工具继续完成任务。",
                            })
                # Engine mode owns termination too: once the workflow has run to
                # completion the task is done, so close the run instead of
                # letting the model keep improvising past the end of the recipe.
                if (
                    self._workflow_engine_mode
                    and wf_state is not None
                    and wf_state.finished
                    and not sm.is_terminal
                    and sm.can_transit("DONE")
                ):
                    ctx.exit_state()
                    sm.transit("DONE")
                    ctx.enter_state(sm.current)
                    _emit_event(event_sink, "on_state_enter", sm.current)
                if sm.is_terminal:
                    break
            else:
                early = True
                reason = "max_turns exhausted"
                terminal = sm.current

            # ---- Saga compensation (§4.3.4 / §2.5(5)).
            # A workflow that died mid-way has already written its earlier steps
            # to the world. Without a compensation boundary that leaves a
            # half-built configuration a human has to clean up before retrying —
            # the paper names this as a distinct failure mode, and it was
            # previously unhandled. Restoring the entry checkpoint makes a failed
            # workflow atomic: all of it, or none of it.
            rolled_back = False
            if (
                self.config.architecture.workflow.rollback_on_failure
                and wf_state is not None
                and wf_state.failed_step is not None
            ):
                if wf_state.compensations:
                    # §4.3.4 Saga proper: run each completed step's declared
                    # inverse in reverse order, so only the effects that
                    # actually happened are undone — not a blanket world reset.
                    for step_id, comp in reversed(wf_state.compensations):
                        c_result, _p, c_lat, c_action = self._route_and_dispatch(
                            comp.tool, comp.arguments, world
                        )
                        ctx.log_tool_call(
                            ToolCallRecord(
                                turn=turn, state=sm.current, visible_tools=[],
                                visible_count=0,
                                selected=f"compensate:{step_id}", action=c_action,
                                args=comp.arguments,
                                schema_valid=c_result.error_code != "SCHEMA_ERROR",
                                result_ok=c_result.ok, error_code=c_result.error_code,
                                error_msg=c_result.error_msg, result_data=c_result.data,
                                world_diff=c_result.world_diff, latency_ms=c_lat,
                            )
                        )
                    rolled_back = True
                else:
                    # No per-step compensation declared → coarse fallback:
                    # restore the whole world to the workflow-entry checkpoint.
                    world.restore(saga_checkpoint)
                    rolled_back = True
                    ctx.log_tool_call(
                        ToolCallRecord(
                            turn=turn,
                            state=sm.current,
                            visible_tools=[],
                            visible_count=0,
                            selected="workflow:__saga_rollback__",
                            action=None,
                            args={"failed_step": wf_state.failed_step},
                            schema_valid=True,
                            result_ok=True,
                            error_code="OK",
                            error_msg=None,
                            result_data={
                                "workflow_id": wf_state.workflow_id,
                                "failed_step": wf_state.failed_step,
                                "completed_steps": list(wf_state.completed_steps),
                            },
                            world_diff=None,
                            latency_ms=0.0,
                        )
                    )

            ctx.workflow_summary = {
                **ctx.workflow_summary,
                "mode": self.config.architecture.workflow.mode,
                "failed_step": wf_state.failed_step if wf_state else None,
                "completed_steps": list(wf_state.completed_steps) if wf_state else [],
                "finished": bool(wf_state.finished) if wf_state else None,
                "rolled_back": rolled_back,
            }
            ctx.policy_summary = self.policy.summary()
            if scratchpad is not None:
                ctx.react_summary = scratchpad.summary()

            ctx.final_world_hash = world.hash()
            terminal = sm.current if not early else terminal
            record = ctx.finish(
                terminal_state=terminal,
                early_terminated=early,
                termination_reason=reason,
            )
            if event_sink is not None:
                _emit_event(event_sink, "on_run_finish", record, deep_copy_world(world))
            return record

    # ------------------------------------------------------------------ plan & execute
    def _planner_pool(self, query: str) -> list[str]:
        """Atomics the planner may use: the union of every state's whitelist,
        soft-ranked by Tool RAG.

        The union — not the *current* state's whitelist — because a plan spans
        states by construction; restricting the planner to where the FSM happens
        to be standing would make it plan only the first step. The per-state
        whitelist is still enforced at *execution* time, so the cage is intact:
        the planner may propose, only the router may enter a state, and it walks
        legal transitions only.
        """
        arch = self.config.architecture
        if arch.state_machine.enabled:
            allowed = sorted(
                {t for spec in STATES.values() for t in spec.allowed_tools}
                & set(self._atomic_names)
            )
        else:
            allowed = list(self._atomic_names)
        # RAG orders the pool but must not *truncate* it. In the docode trial
        # the planner saw only the RAG top-k, concluded the catalogue lacked
        # validate_project/deploy_project, and refused a legitimate task. The
        # most-relevant tools go first (they get full schemas in the catalogue);
        # everything else follows and is listed by name.
        ranked = self._rank_with_rag(query, allowed)
        ranked_set = set(ranked)
        return ranked + [t for t in allowed if t not in ranked_set]

    def _request_plan(
        self,
        query: str,
        pool: list[str],
        ctx: Any,
        turn: int,
        feedback: str | None = None,
        world_context: str | None = None,
    ) -> _PlanReply:
        """Ask the backend for a whole-task plan.

        ``supported`` is False when the backend has no ``make_plan`` hook at all
        (e.g. ``MockLLM``), which is what makes the lever degrade to the
        interleaved loop instead of failing.
        """
        planner = getattr(self.llm, "make_plan", None)
        if not callable(planner):
            return _PlanReply([], None, None, False)
        tool_list = describe_tools_for_planner(
            self.registry, pool,
            max_tools=self.config.architecture.plan_execute.planner_tool_budget,
            typed_hints=self.config.architecture.plan_execute.typed_tool_hints,
        )
        t0 = time.perf_counter()
        try:
            try:
                payload = planner(query, tool_list, feedback, world_context)
            except TypeError:
                # Older three-argument hook (pre world-context) — still valid.
                payload = planner(query, tool_list, feedback)
        except Exception:
            return _PlanReply([], None, None, True, answered=False)
        latency = (time.perf_counter() - t0) * 1000
        usage = (payload or {}).get("_usage") or {}
        ctx.log_llm(
            LLMCallRecord(
                turn=turn,
                model=self.config.model.name,
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
                latency_ms=latency,
                stop_reason="end_turn",
            ),
            # Honour ``trace.record_llm_io`` here as the interleaved loop does.
            # Hard-coding ``text=None`` meant a plan-tier run recorded tokens and
            # latency but never the plan itself, so a step dropped
            # ``schema_invalid`` could not be diagnosed after the fact — the
            # arguments that failed were the one thing not written down. The
            # payload minus ``_usage`` *is* the completion for this hook.
            text=(
                json.dumps(
                    {k: v for k, v in payload.items() if k != "_usage"},
                    ensure_ascii=False, default=str,
                )
                if self.config.trace.record_llm_io and isinstance(payload, dict)
                else None
            ),
            reasoning=None,
        )
        steps, refusal, clarify = parse_plan_payload(payload)
        if not self.config.architecture.plan_execute.clarify_on_underspecified:
            # Lever off: *ignore* the field, exactly as code predating it did with
            # an unrecognised key. It must not be folded into ``refusal``:
            # ``refusal`` gates ``replan_on_compile_drop`` and both crew
            # escalations (all spelled ``and not refusal``), so a reply carrying
            # steps *and* a clarification would silently lose the compile-drop
            # replan — re-opening the golden-013 "half the task delivered as if it
            # were all of it" defect through a lever that is supposed to be off.
            return _PlanReply(steps, refusal, None, True)
        return _PlanReply(steps, refusal, clarify, True)

    def _route_to_state(
        self, sm: StateMachine, ctx: Any, target: str, event_sink: Any
    ) -> bool:
        """Walk **legal** transitions from the current state to *target*.

        Never uses ``force_to``: the planner is a sequencer, not an authority
        over the cage. Returns False (and leaves the FSM where it was) if the
        FSM has no legal path — the caller then drops the step.
        """
        if not self.config.architecture.state_machine.enabled or sm.current == target:
            return True
        route = state_route(sm.current, target)
        if route is None:
            return False
        for hop in route:
            ctx.exit_state()
            sm.transit(hop)
            ctx.enter_state(sm.current)
            _emit_event(event_sink, "on_state_enter", sm.current)
        return True

    def _plan_and_execute(
        self,
        query: str,
        world: MockWorld,
        sm: StateMachine,
        ctx: Any,
        event_sink: AgentEventSink | None,
    ) -> PlanOutcome:
        """Plan once, compile deterministically, then execute without the LLM.

        The turn accounting is the headline: an N-step task costs
        ``1 + replans`` LLM calls instead of ``N + 1``. In combined mode this
        tier also decides the escalation to the Multi-Agent crew: before
        execution when the compiled plan spans ``multi_agent.min_domains``
        registry domains, or after a failure that exhausted the replan budget.
        """
        cfg = self.config.architecture.plan_execute
        crew_cfg = self.config.architecture.multi_agent
        pool = self._planner_pool(query)
        # Truncating the planner's only view of the world is a silent
        # correctness bug (see summarize_world_for_planner), so count what was
        # elided and carry it into the plan summary.
        world_truncation: dict[str, int] = {}
        world_ctx = (
            summarize_world_for_planner(
                world,
                max_items=cfg.world_context_max_items,
                truncation=world_truncation,
            )
            if cfg.include_world_context
            else None
        )
        turns = 1
        reply = self._request_plan(query, pool, ctx, turns, world_context=world_ctx)
        raw_steps, refusal, clarify = reply.steps, reply.refusal, reply.clarify
        if not reply.supported:
            # Backend cannot plan — hand back to the interleaved loop, and do
            # not charge the run for a turn that never happened.
            return PlanOutcome(executed=False, turns=0, summary={"enabled": True, "supported": False})

        # A clarification request outranks whatever else the planner emitted.
        # Acting on a request whose identity or semantics is unknown is how the
        # world gets mutated on a case that expected a question, so any step
        # proposed alongside a clarify is discarded rather than executed.
        if clarify:
            self._finish_clarify_state(sm, ctx, event_sink)
            diag = PlanDiagnostics(proposed=len(raw_steps), compiled=0)
            diag.clarify = clarify
            # A clarification is often *caused* by an elided snapshot, so this is
            # the last place the truncation should go unrecorded.
            diag.world_truncated = dict(world_truncation)
            return PlanOutcome(
                executed=True,
                completed=True,
                turns=turns,
                reason="clarify",
                summary={"enabled": True, "supported": True, "executed": True,
                         **diag.as_dict()},
            )

        def _compile(steps: list[dict[str, Any]], refuse: str | None):
            return compile_plan(
                steps,
                self.registry,
                world,
                allowed_atomics=pool if self.config.architecture.state_machine.enabled else None,
                start_state=sm.current,
                max_steps=cfg.max_steps,
                reorder=cfg.reorder_by_dependency,
                refusal=refuse,
                repair=cfg.repair_schema_invalid,
            )

        plan = _compile(raw_steps, refusal)

        # ---- Fix 3 (docode trial, golden-013): a compile drop means part of
        # the task silently vanished — 2 proposed → 1 compiled read as "planned
        # fine, executed fine, half missing". Spend one replan naming exactly
        # what was dropped and why, instead of executing the shortened plan.
        d = plan.diagnostics
        dropped = d.dropped_unknown_tool + d.dropped_schema_invalid + d.dropped_unreachable_state
        if (
            cfg.replan_on_compile_drop
            and dropped
            and not refusal
            and cfg.max_replans > 0
        ):
            turns += 1
            drop_feedback = (
                "上一版计划中以下步骤无法执行,已被编译器剔除,请修正后重新给出完整计划:\n"
                + (f"- 工具名不存在: {', '.join(d.dropped_unknown_tool)}\n" if d.dropped_unknown_tool else "")
                # Deliberately the tool name only. Naming the offending *fields*
                # was measured as the K6 arm (results_w13) and not adopted: it is
                # net -4 run-by-run against K5 (7 fixed, 11 broke) at 72.3% vs
                # 73.6% task_success, and it buys no mean improvement — the two
                # arms' per-rep ranges overlap (K5 72.6-74.5, K6 71.7-73.6).
                #
                # It does something real: golden-093 gained the missing
                # ``enable_history`` in 3 of 3 reps where K5 had it in 0 of 3.
                # But the run-by-run ledger is negative and the simpler prompt is
                # the one with evidence behind it, so K5 ships.
                #
                # Do NOT read the REJECT-behaviour drop (90.9% -> 85.9%) as a
                # safety mechanism: it is carried mostly by golden-063, which was
                # measured at 2/5 TYPE_MISMATCH vs 3/5 OK across five runs of the
                # *same* config, seed and tree. LongCat is not deterministic at
                # temperature 0, so no single-case story from three reps is
                # evidence on its own.
                #
                # The detail is still computed and traced as
                # ``schema_invalid_detail``; it just does not go back to the model.
                + (f"- 参数不符合 schema: {', '.join(d.dropped_schema_invalid)}\n" if d.dropped_schema_invalid else "")
                + (f"- 当前状态机无法到达: {', '.join(d.dropped_unreachable_state)}\n" if d.dropped_unreachable_state else "")
            )
            retry_reply = self._request_plan(
                query, pool, ctx, turns, drop_feedback, world_context=world_ctx
            )
            # A clarification on the retry is still a clarification. Dropping it
            # here made the lever's meaning depend on which turn produced the
            # reply: honoured on the first call, silently discarded on this one.
            if retry_reply.clarify:
                self._finish_clarify_state(sm, ctx, event_sink)
                plan.diagnostics.clarify = retry_reply.clarify
                plan.diagnostics.replans = 1
                plan.diagnostics.world_truncated = dict(world_truncation)
                return PlanOutcome(
                    executed=True, completed=True, turns=turns, reason="clarify",
                    summary={"enabled": True, "supported": True, "executed": True,
                             **plan.diagnostics.as_dict()},
                )
            retry_steps, retry_refusal = retry_reply.steps, retry_reply.refusal
            retry = _compile(retry_steps, retry_refusal)
            # Adopt the retry only if it actually compiled at least as much of
            # the task; otherwise keep the original plan. Either way one replan
            # call was spent — the diagnostics must say so even when the retry
            # is discarded, or the trace under-reports the LLM-call cost.
            if retry.steps and len(retry.steps) >= len(plan.steps):
                plan, refusal = retry, retry_refusal
            plan.diagnostics.replans = 1

        # Carried on the diagnostics (not a step drop, but the same class of
        # defect) so every downstream summary reports it via as_dict().
        plan.diagnostics.world_truncated = dict(world_truncation)

        # ---- Escalation on surviving compile drops. If part of the task still
        # cannot compile after the informed replan, executing the shortened
        # plan silently delivers half the task as if it were all of it — the
        # trial's golden-013 failed exactly this way even *with* the replan
        # (the retry did not fix the schema). The crew's iterate-with-feedback
        # loop is what solved that case, so hand over instead of shortening.
        # A refusal is not a drop — it stays a respected result.
        d = plan.diagnostics
        still_dropped = (
            d.dropped_unknown_tool + d.dropped_schema_invalid + d.dropped_unreachable_state
        )
        if still_dropped and not refusal and crew_cfg.enabled:
            return PlanOutcome(
                executed=False,
                turns=turns,
                reason="compile_drop",
                escalate_to_crew=True,
                refusal=refusal,
                # What *did* compile guides the crew; the dropped remainder is
                # recovered by the Specialists from the query itself (their
                # worklist is marked as possibly incomplete).
                plan_steps=list(plan.steps),
                summary={
                    "enabled": True, "supported": True, "executed": False,
                    "escalated": "compile_drop", **plan.diagnostics.as_dict(),
                },
            )

        # ---- Domain gate: decomposition beats one flat plan once the task
        # spans several domains, and the crew is the decomposition tier. Count
        # registry domains, not states — states are an FSM concern.
        if plan.steps and crew_cfg.enabled and crew_cfg.domain_gate:
            domains: set[str] = set()
            for step in plan.steps:
                with suppress(KeyError):
                    domains.add(self.registry.lookup(step.tool)[0])
            if len(domains) >= max(1, crew_cfg.min_domains):
                return PlanOutcome(
                    executed=False,
                    turns=turns,
                    reason="domain_gate",
                    escalate_to_crew=True,
                    refusal=refusal,
                    plan_steps=list(plan.steps),
                    summary={
                        "enabled": True, "supported": True, "executed": False,
                        "escalated": "domain_gate", "domains": sorted(domains),
                        **plan.diagnostics.as_dict(),
                    },
                )

        if not plan.steps:
            # An explicit refusal is a *result*, not a failure: the golden
            # `reject` cases expect exactly this (no world mutation). Only an
            # empty plan with no refusal means the planner had nothing to say,
            # and that is what the fallback exists for.
            if refusal or not cfg.fallback_to_interleaved:
                self._finish_plan_state(sm, ctx, event_sink)
                return PlanOutcome(
                    executed=True,
                    completed=True,
                    turns=turns,
                    reason=None if refusal else "empty_plan",
                    summary={"enabled": True, "supported": True, "executed": True,
                             **plan.diagnostics.as_dict()},
                )
            return PlanOutcome(
                executed=False,
                turns=turns,
                summary={"enabled": True, "supported": True, "executed": False,
                         **plan.diagnostics.as_dict()},
            )

        executed_signatures: set[str] = set()
        # Collects what the executed steps actually created (from world_diff).
        # On a failure escalation this seeds the crew's Blackboard, so the
        # Specialists build on the partial work instead of redoing it.
        board = Blackboard()
        replans = plan.diagnostics.replans
        completed = True
        reason: str | None = None
        pending = list(plan.steps)
        # Everything the *originally approved* plan set out to create. A replan
        # may re-create any of these (recovering a dropped prerequisite is
        # legitimate); anything outside this set is an entity nobody asked for.
        planned_entities = {e for step in plan.steps for e in step.intended}

        while pending:
            failure: tuple[PlanStep, Any] | None = None
            for step in pending:
                ok, result = self._execute_plan_step(
                    step, world, sm, ctx, turns, event_sink, executed_signatures,
                    board=board,
                )
                if not ok:
                    failure = (step, result)
                    break
            if failure is None:
                break
            failed_step, failed_result = failure
            # POLICY_DENIED is a boundary, not a bug: replanning around the §4.7
            # cage is exactly what must not happen, so stop here. It must not
            # escalate to the crew either — same cage, same answer.
            if failed_result is not None and getattr(failed_result, "error_code", "") == "POLICY_DENIED":
                completed = False
                reason = "policy_denied"
                break
            if replans >= cfg.max_replans:
                completed = False
                reason = "plan_step_failed"
                break
            replans += 1
            turns += 1
            feedback = (
                f"步骤 {failed_step.tool}({failed_step.arguments}) 失败: "
                f"{getattr(failed_result, 'error_code', 'UNKNOWN')} "
                f"{getattr(failed_result, 'error_msg', '') or ''}"
            )
            replan_reply = self._request_plan(
                query, pool, ctx, turns, feedback, world_context=world_ctx
            )
            # Mid-execution the world is already partly written, so a clarification
            # here cannot rewind — but it is still the honest reason the run
            # stopped, and recording it beats reporting "replan_empty".
            #
            # It terminates on ASK_USER like every other clarification. Breaking
            # out instead fell through to ``_finish_plan_state`` and ended the run
            # on DONE, which claims the task completed and contradicts the
            # invariant the rest of this lever is built on (refusal → DONE,
            # clarification → ASK_USER); the golden expectations read the terminal
            # state directly, so the two are not interchangeable. Returning here
            # rather than breaking also keeps the clarification away from the crew
            # escalation below: needing input from the user is not a failure a
            # second tier can decompose its way out of.
            if replan_reply.clarify and not replan_reply.steps:
                plan.diagnostics.clarify = replan_reply.clarify
                plan.diagnostics.replans = replans
                self._finish_clarify_state(sm, ctx, event_sink)
                return PlanOutcome(
                    executed=True,
                    completed=False,
                    turns=turns,
                    reason="replan_clarify",
                    summary={
                        "enabled": True, "supported": True, "executed": True,
                        "completed": False, **plan.diagnostics.as_dict(),
                    },
                )
            replan = _compile(replan_reply.steps, replan_reply.refusal)
            plan.diagnostics.replans = replans
            recovery = self._filter_cascade_recovery(
                replan.steps, failed_step, failed_result, planned_entities, world
            )
            if recovery is not replan.steps:
                plan.diagnostics.dropped_cascade_recovery.extend(
                    s.tool for s in replan.steps if s not in recovery
                )
            if not replan.steps:
                completed = False
                reason = "replan_empty"
                break
            if len(recovery) != len(replan.steps):
                # A recovery that depends on manufacturing the premise is not a
                # recovery. Stop and let the reference failure be the answer
                # rather than spending another turn re-running the step that
                # already failed for a reason nothing here can fix.
                completed = False
                reason = "replan_cascade_blocked"
                break
            pending = recovery

        # ---- Failure escalation: the plan tier is out of budget but the task
        # is not done. Decomposition with fresh, narrower contexts is the next
        # thing to try — hand over to the crew with the partial work on the
        # board. Policy denials stay final.
        if (
            not completed
            and reason in {"plan_step_failed", "replan_empty"}
            and crew_cfg.enabled
        ):
            # Hand over only the un-executed remainder — the executed steps'
            # results are already on the board, and re-listing them would make
            # the Specialists redo (and possibly double-apply) finished work.
            remaining = [
                step for step in pending
                if f"{step.tool}|{sorted(step.arguments.items(), key=lambda kv: kv[0])}"
                not in executed_signatures
            ]
            return PlanOutcome(
                executed=False,
                completed=False,
                turns=turns,
                reason=reason,
                escalate_to_crew=True,
                refusal=refusal,
                board_entities=list(board.entities),
                plan_steps=remaining,
                summary={
                    "enabled": True, "supported": True, "executed": False,
                    "escalated": reason, **plan.diagnostics.as_dict(),
                },
            )

        # ---- Close the loop: check what was actually built against the request.
        # Gated to the one situation it can help and cannot harm — the plan ran
        # to completion and mutated something. A refusal, a clarification, a
        # policy denial and a blocked cascade have all already returned above, so
        # none of them can reach a round whose whole job is to finish the task.
        if cfg.verify_rounds > 0 and completed and board.entities:
            turns = self._verify_and_patch(
                query, world, sm, ctx, event_sink,
                plan=plan, board=board, pool=pool, world_ctx=world_ctx,
                executed_signatures=executed_signatures, turns=turns,
            )

        self._finish_plan_state(sm, ctx, event_sink)
        summary = {
            "enabled": True,
            "supported": True,
            "executed": True,
            "completed": completed,
            **plan.diagnostics.as_dict(),
        }
        return PlanOutcome(
            executed=True, completed=completed, turns=turns, reason=reason, summary=summary
        )

    def _verify_and_patch(
        self,
        query: str,
        world: MockWorld,
        sm: StateMachine,
        ctx: Any,
        event_sink: AgentEventSink | None,
        *,
        plan: Any,
        board: Blackboard,
        pool: list[str],
        world_ctx: str | None,
        executed_signatures: set[str],
        turns: int,
    ) -> int:
        """Read the world back, ask what the request still lacks, patch it.

        Returns the updated turn count — each verification round is one LLM call
        and is charged whether or not it produced a patch, because the cost claim
        for this architecture has to survive its own instrumentation.
        """
        cfg = self.config.architecture.plan_execute
        diag = plan.diagnostics
        for _ in range(max(0, cfg.verify_rounds)):
            # Verification is charged against the same global budget as every
            # other turn. Without this the rounds were simply added on top, so a
            # plan that had already spent ``max_turns`` could still buy itself
            # extra LLM calls — the one arm allowed to exceed the budget every
            # other arm is measured under, which is exactly the comparison the
            # cost claim rests on.
            if turns >= self.max_turns:
                break
            # What *actually* ran, read off the trace. ``plan.steps`` is the
            # originally compiled plan: a replan replaces the pending list without
            # rewriting it, and the execution loop stops at the first failure, so
            # using it told the verifier that a step which failed had run and that
            # the replan which succeeded had not — inverting the very comparison
            # this round exists to make.
            executed_desc = "\n".join(
                f"- {call.selected}"
                f"({json.dumps(call.args, ensure_ascii=False, default=str)})"
                for call in (ctx.tool_calls or [])
                if call.result_ok
            ) or "(无)"
            # Re-summarise the world *now*. Passing the pre-execution snapshot put
            # the entities this round is meant to inspect in the prompt as absent
            # under the heading 【当前世界状态】 while the state block below listed
            # them as present — two contradictory readings, both pushing the model
            # to re-propose finished work.
            fresh_ctx = (
                summarize_world_for_planner(world, max_items=cfg.world_context_max_items)
                if cfg.include_world_context
                else None
            )
            feedback = VERIFY_INSTRUCTION.format(
                executed=executed_desc,
                state=describe_entities_for_verify(world, board.entities),
            )
            turns += 1
            diag.verify_rounds += 1
            reply = self._request_plan(
                query, pool, ctx, turns, feedback, world_context=fresh_ctx
            )
            # A verification round is not a place to refuse or to ask: the work is
            # already done and the world is already mutated. Only steps count.
            #
            # "No steps" means clean only if the backend actually answered.
            # ``_request_plan`` also returns an empty reply when the planning call
            # raised or the backend cannot plan, and recording a timeout as
            # "verified, nothing missing" asserts the opposite of what happened.
            if not reply.steps:
                diag.verify_clean = (
                    reply.answered and reply.supported and not reply.refusal
                )
                break
            patch = compile_plan(
                reply.steps,
                self.registry,
                world,
                allowed_atomics=pool if self.config.architecture.state_machine.enabled else None,
                start_state=sm.current,
                max_steps=cfg.max_steps,
                reorder=cfg.reorder_by_dependency,
                refusal=None,
                repair=cfg.repair_schema_invalid,
            )
            # A round that proposed steps and lost them all to the compiler is
            # not the same as one that had nothing to propose, and without this
            # the two are indistinguishable in the trace.
            pd = patch.diagnostics
            diag.dropped_unknown_tool.extend(pd.dropped_unknown_tool)
            diag.dropped_schema_invalid.extend(pd.dropped_schema_invalid)
            diag.dropped_unreachable_state.extend(pd.dropped_unreachable_state)

            # Completing a request never requires destroying anything. Dropping
            # these rather than trusting the prompt keeps the patch path from
            # becoming a way to reach the operations the §4.7 cage bounds.
            safe = [s for s in patch.steps if not _is_destructive_for_patch(s.tool)]
            if len(safe) != len(patch.steps):
                diag.dropped_verify_destructive.extend(
                    s.tool for s in patch.steps if _is_destructive_for_patch(s.tool)
                )
            if not safe:
                break
            applied = 0
            for step in safe:
                ok, result = self._execute_plan_step(
                    step, world, sm, ctx, turns, event_sink, executed_signatures,
                    board=board,
                )
                # ``_execute_plan_step`` returns ``(True, None)`` for a step whose
                # signature already executed. That is a no-op, not a patch, and
                # counting it inflated ``verify_patched`` by the re-proposal rate —
                # letting a round that found nothing report that it fixed something.
                if ok and result is not None:
                    applied += 1
                elif not ok:
                    # Keep going. A redundant create failing ALREADY_EXISTS used to
                    # abandon the rest of the patch, and since dependency reordering
                    # puts creates first, one redundant create was enough to swallow
                    # every genuine fix behind it.
                    diag.verify_step_failures += 1
            diag.verify_patched += applied
            if applied == 0:
                break
        return turns

    def _execute_plan_step(
        self,
        step: PlanStep,
        world: MockWorld,
        sm: StateMachine,
        ctx: Any,
        turn: int,
        event_sink: AgentEventSink | None,
        executed_signatures: set[str],
        board: Blackboard | None = None,
    ) -> tuple[bool, Any]:
        """Dispatch one compiled step through the normal cages.

        A replan re-proposes steps that already succeeded; ``executed_signatures``
        makes those no-ops so a replan cannot double-apply the work the first
        plan already did.
        """
        signature = f"{step.tool}|{sorted(step.arguments.items(), key=lambda kv: kv[0])}"
        if signature in executed_signatures:
            return True, None

        # The state machine is still the cage. A step whose owning state cannot
        # be reached legally from here is refused, not forced.
        if not self._route_to_state(sm, ctx, step.state, event_sink):
            ctx.log_tool_call(
                ToolCallRecord(
                    turn=turn, state=sm.current, visible_tools=[], visible_count=0,
                    selected=step.tool, action=step.action, args=step.arguments,
                    schema_valid=True, result_ok=False, error_code="OUT_OF_SCOPE",
                    error_msg=f"no legal transition from {sm.current} to {step.state}",
                    result_data={}, world_diff=None, latency_ms=0.0,
                )
            )
            return False, None

        # §4.7 runtime cage — evaluated before dispatch, exactly as in the
        # interleaved loop. Planning must not become a way around it.
        decision = self.policy.check(step.tool, step.arguments, world)
        if decision.denied:
            rec = ToolCallRecord(
                turn=turn, state=sm.current, visible_tools=[], visible_count=0,
                selected=step.tool, action=step.action, args=step.arguments,
                schema_valid=True, result_ok=False, error_code="POLICY_DENIED",
                error_msg=f"[{decision.rule_id}] {decision.reason}",
                result_data={"rule_id": decision.rule_id}, world_diff=None, latency_ms=0.0,
            )
            ctx.log_tool_call(rec)
            if event_sink is not None:
                snap = deep_copy_world(world)
                _emit_event(event_sink, "on_tool_call", turn, rec, snap, snap)
            return False, _DeniedResult(decision.rule_id, decision.reason)

        world_before = deep_copy_world(world) if event_sink is not None else None
        result, _parsed, lat, action = self._route_and_dispatch(
            step.tool, step.arguments, world
        )
        rec = ToolCallRecord(
            turn=turn,
            state=sm.current,
            # The executed surface, recorded honestly: a compiled step is
            # dispatched against exactly one tool, and the compiler already
            # proved the call is in scope for this state.
            visible_tools=[{"name": step.tool, "allowed_actions": [step.action]}],
            visible_count=1,
            selected=step.tool,
            action=action if action is not None else step.action,
            args=step.arguments,
            schema_valid=result.error_code != "SCHEMA_ERROR",
            result_ok=result.ok,
            error_code=result.error_code,
            error_msg=result.error_msg,
            result_data=result.data,
            world_diff=result.world_diff,
            latency_ms=lat,
            intended_entities=step.intended,
            referenced_entities=step.referenced,
        )
        ctx.log_tool_call(rec)
        if event_sink is not None:
            _emit_event(
                event_sink, "on_tool_call", turn, rec, world_before, deep_copy_world(world)
            )
        if result.ok:
            self.policy.record_execution(step.tool)
            executed_signatures.add(signature)
            if board is not None:
                board.record_diff(result.world_diff)
        return result.ok, result

    def _filter_cascade_recovery(
        self,
        steps: list[PlanStep],
        failed_step: PlanStep,
        failed_result: Any,
        planned_entities: set[str],
        world: Any,
    ) -> list[PlanStep]:
        """Drop recovery steps that *manufacture* the entity a step referenced.

        The cascade-failure detector exists because a call that references an
        entity an earlier call should have created is the signature failure of
        this domain. The recovery path re-enters it from the other side: when
        ``query_history(tag=NO_HISTORY_POINT)`` correctly fails
        ``POINT_NOT_FOUND``, a replan that calls ``create_point`` to make the
        query succeed has not fixed the task — it has invented the premise. On
        golden-054 that mutated the world on a case whose expected diff is empty
        and whose forbidden list names ``create_point``, converting a correct
        error report into an incorrect success.

        The distinction that keeps this from blocking legitimate work: only
        entities *nobody asked for* are protected. If any step of the original
        plan intended to create the entity, the user did ask for it and a replan
        may re-add it — that is ordinary recovery from a dropped prerequisite.
        Only a reference-class failure (``*_NOT_FOUND``) on an entity the failed
        step merely read triggers the filter at all.
        """
        if self.config.architecture.plan_execute.replan_may_create_referenced:
            return steps
        if getattr(failed_result, "error_code", None) not in REFERENCE_ERROR_CODES:
            return steps

        # Protect *ids*, not exact entity paths. The cascade crosses collections:
        # ``query_history`` references ``histories.X`` while the recovery that
        # manufactures the premise creates ``points.X`` — a different path
        # carrying the same name, so path matching let golden-054's
        # ``create_point`` straight through.
        #
        # ``entity_ids`` returns every id in a path, not the last segment. The
        # last segment of a deep path is a *field* (``bind_point`` intends
        # ``pages.p1.widgets.w1.bindings.value``), so a last-segment reading put
        # ``value`` in ``planned_ids`` and never ``w1`` — which meant the escape
        # hatch below could not fire for any nested entity, and a recoverable
        # ``WIDGET_NOT_FOUND`` was aborted with nothing built.
        planned_ids: set[str] = set()
        for entity in planned_entities:
            planned_ids |= entity_ids(entity)
        protected: set[str] = set()
        for entity in failed_step.referenced:
            if entity in failed_step.intended:
                continue
            for ident in entity_ids(entity):
                # An id the approved plan already meant to create, or one that
                # exists at any depth, is not manufactured premise: the first was
                # asked for, the second is a missing facet of a real entity (a
                # point that exists but has no history config).
                if ident in planned_ids or _identity_known(world, ident):
                    continue
                protected.add(ident)
        if not protected:
            return steps

        def manufactures_premise(step: PlanStep) -> bool:
            ids: set[str] = set()
            for entity in step.intended:
                ids |= entity_ids(entity)
            return bool(ids & protected)

        return [s for s in steps if not manufactures_premise(s)]

    def _finish_plan_state(
        self, sm: StateMachine, ctx: Any, event_sink: AgentEventSink | None
    ) -> None:
        """Close the run on DONE when the FSM allows it."""
        if not self.config.architecture.state_machine.enabled:
            return
        if sm.is_terminal or not sm.can_transit("DONE"):
            return
        ctx.exit_state()
        sm.transit("DONE")
        ctx.enter_state(sm.current)
        _emit_event(event_sink, "on_state_enter", sm.current)

    def _finish_clarify_state(
        self, sm: StateMachine, ctx: Any, event_sink: AgentEventSink | None
    ) -> None:
        """Close the run on ASK_USER — the terminal a clarification belongs in.

        Distinct from :meth:`_finish_plan_state` on purpose. Landing a
        clarification on DONE would claim the task was completed, and the golden
        expectations read the terminal state directly: ``success`` cases exclude
        ASK_USER (``!ASK_USER``) while clarification cases permit it. Routing
        here therefore scores an over-eager clarification as the failure it is,
        rather than hiding it behind a DONE.
        """
        if not self.config.architecture.state_machine.enabled:
            return
        if sm.is_terminal or not sm.can_transit("ASK_USER"):
            return
        ctx.exit_state()
        sm.transit("ASK_USER")
        ctx.enter_state(sm.current)
        _emit_event(event_sink, "on_state_enter", sm.current)

    # ------------------------------------------------------------------ multi-agent crew
    def _crew_pool(self, query: str) -> list[str]:
        """The ranked atomic pool the Supervisor routes from.

        The union of every working state's whitelist, soft-ranked by Tool RAG —
        the same ranking a single agent would have been shown, so routing costs
        no extra LLM call and stays deterministic. Per-state narrowing happens
        in ``route_subtasks``; per-call enforcement stays with the dispatcher.

        RAG **orders** the pool but must not truncate it, for the same reason
        the planner pool is untruncated: a Specialist assignment is capped by
        ``tools_per_specialist`` *after* per-state bucketing, so a top-k cut
        here removes tools the Supervisor never gets to consider. The measured
        symptom was a required tool never becoming visible at all (golden-102
        never saw ``create_tank``, golden-104 never saw ``set_sample_interval``)
        — which is not the state machine refusing, it is the ranking silently
        hiding. The per-turn *visible surface* still honours ``top_k``; this is
        only the routing pool.
        """
        arch = self.config.architecture
        if arch.state_machine.enabled:
            allowed = sorted(
                {t for spec in STATES.values() for t in spec.allowed_tools}
                & set(self._atomic_names)
            )
        else:
            allowed = list(self._atomic_names)
        ranked = self._rank_with_rag(query, allowed)
        ranked_set = set(ranked)
        return ranked + [t for t in allowed if t not in ranked_set]

    def _crew_route_to(self, sm: StateMachine, ctx: Any, target: str, event_sink: Any) -> bool:
        """Move the FSM to *target* via legal transitions only (never force_to)."""
        if not self.config.architecture.state_machine.enabled:
            return True
        if sm.current == target:
            return True
        route = state_route(sm.current, target)
        if route is None:
            return False
        for hop in route:
            ctx.exit_state()
            sm.transit(hop)
            ctx.enter_state(sm.current)
            _emit_event(event_sink, "on_state_enter", sm.current)
        return True

    def _specialist_visible(self, subtask: Subtask) -> list[dict[str, Any]]:
        """The Specialist's tool surface: its own atomics, domain-projected in
        hierarchical mode — the same projection the single agent gets, minus
        every other domain's distractors."""
        if not self.config.architecture.hierarchical_tools:
            return [{"name": n} for n in subtask.atomics]
        by_domain: dict[str, list[str]] = {}
        for atomic in subtask.atomics:
            try:
                domain, action = self.registry.lookup(atomic)
            except KeyError:
                continue
            by_domain.setdefault(domain, [])
            if action not in by_domain[domain]:
                by_domain[domain].append(action)
        return [
            {"name": domain, "allowed_actions": actions}
            for domain, actions in by_domain.items()
        ]

    def _run_multi_agent(
        self,
        query: str,
        world: MockWorld,
        sm: StateMachine,
        ctx: Any,
        event_sink: AgentEventSink | None,
        *,
        start_turn: int = 0,
        seed_entities: list[str] | None = None,
        plan_steps: list[PlanStep] | None = None,
        refusal: str | None = None,
    ) -> CrewOutcome:
        """Supervisor → Specialists (+ Blackboard) → Critic.

        Each Specialist runs a bounded *private* conversation over its own
        state's tools; the Blackboard forwards the entity IDs that were actually
        created (read from ``world_diff``, not from what a model said). When the
        crew is the escalation tier, ``start_turn`` carries the turns the plan
        tier already spent (shared budget), ``seed_entities`` carries the
        partial work an aborted plan left behind, and ``plan_steps`` switches
        the Supervisor to plan-guided routing: one Specialist per plan state,
        each handed its slice of the compiled plan as an explicit worklist, and
        a turn budget scaled to the plan's size instead of the flat
        ``max_turns`` that starved golden-019 mid-crew.
        """
        cfg = self.config.architecture.multi_agent
        if plan_steps:
            subtasks = route_subtasks_from_plan(
                plan_steps, tools_per_specialist=cfg.tools_per_specialist
            )
        else:
            subtasks = route_subtasks(
                self._crew_pool(query),
                max_specialists=cfg.max_specialists,
                tools_per_specialist=cfg.tools_per_specialist,
            )
        if not subtasks:
            return CrewOutcome(executed=False, summary={"enabled": True, "specialists": 0})

        # Turn budget: flat for query-routed crews; scaled to the task for
        # plan-guided ones. Never below the global max_turns — scaling only
        # ever *extends* the budget for demonstrably larger tasks.
        turn_budget = self.max_turns
        if plan_steps:
            turn_budget = max(
                self.max_turns,
                start_turn + math.ceil(cfg.turns_per_step * len(plan_steps)),
            )

        board = Blackboard()
        for entity in seed_entities or []:
            if entity not in board.entities:
                board.entities.append(entity)
        turn = start_turn
        completed = True
        reason: str | None = None

        for subtask in subtasks:
            turn, truncated = self._run_specialist(
                subtask, query, world, sm, ctx, board, turn, event_sink,
                turn_budget=turn_budget, refusal=refusal,
            )
            # Exhausting the budget is only a failure if it cut a Specialist
            # off mid-work — a crew whose last Specialist finished cleanly on
            # the budget's final turn completed the task.
            if truncated and turn >= turn_budget:
                completed = False
                reason = "max_turns exhausted"
                break

        # Critic pass: a Specialist that ran but changed nothing gets exactly
        # one retry with that fact stated. Deterministic — "did the world
        # change" is checkable without a judge call.
        retried = 0
        if cfg.critic_retry and completed:
            for subtask in unsatisfied_subtasks(subtasks):
                if turn >= turn_budget:
                    # Retries are a quality pass, not primary work — running
                    # out of budget here skips them without failing the run.
                    break
                retried += 1
                turn, _truncated = self._run_specialist(
                    subtask, query, world, sm, ctx, board, turn, event_sink,
                    critic_note=critic_feedback(subtask),
                    turn_budget=turn_budget, refusal=refusal,
                )

        # Close the run: the crew owns termination the way the workflow engine
        # does in engine mode.
        if (
            self.config.architecture.state_machine.enabled
            and not sm.is_terminal
            and sm.can_transit("DONE")
        ):
            ctx.exit_state()
            sm.transit("DONE")
            ctx.enter_state(sm.current)
            _emit_event(event_sink, "on_state_enter", sm.current)

        summary = {
            "enabled": True,
            "executed": True,
            "completed": completed,
            "specialists": len(subtasks),
            "plan_guided": bool(plan_steps),
            "turn_budget": turn_budget,
            "critic_retries": retried,
            "refusal_handoff": bool(refusal),
            "assignments": [
                {
                    "state": s.state,
                    "tools": list(s.atomics),
                    "turns_used": s.turns_used,
                    "successful_calls": s.successful_calls,
                }
                for s in subtasks
            ],
            "blackboard": board.summary(),
        }
        return CrewOutcome(
            executed=True, completed=completed, turns=max(turn, 1), reason=reason,
            summary=summary,
        )

    def _run_specialist(
        self,
        subtask: Subtask,
        query: str,
        world: MockWorld,
        sm: StateMachine,
        ctx: Any,
        board: Blackboard,
        turn: int,
        event_sink: AgentEventSink | None,
        *,
        critic_note: str | None = None,
        turn_budget: int | None = None,
        refusal: str | None = None,
    ) -> tuple[int, bool]:
        """One Specialist's bounded private conversation.

        Returns ``(new_global_turn_count, truncated)`` — ``truncated`` is True
        when a turn/budget cap stopped the Specialist while it was still
        issuing tool calls, i.e. mid-work; a Specialist that ends by talking
        (declaring its slice done or empty) is never truncated. When the ReAct
        lever is on, the Specialist runs the ReAct loop: its own fresh
        scratchpad, compressed observations, repair hints, and repeat-dedupe.
        """
        cfg = self.config.architecture.multi_agent
        budget = turn_budget if turn_budget is not None else self.max_turns
        if not self._crew_route_to(sm, ctx, subtask.state, event_sink):
            # No legal path to the Specialist's state — skip the assignment
            # rather than force the cage. The Critic will not resurrect it:
            # turns_used stays 0.
            return turn, False

        # Fresh private conversation: the whole context-isolation claim.
        reset = getattr(self.llm, "reset", None)
        if callable(reset):
            reset()
        scratchpad = self._new_scratchpad()

        history: list[dict[str, Any]] = []
        local_turns = 0
        # True while the Specialist's last response was still acting (tool
        # calls); reset to False when it closes its slice with a talk turn.
        mid_work = False
        visible = self._specialist_visible(subtask)
        tool_list_str = self._render_tool_list(visible) or "(no tools allowed)"
        # Plan-guided assignment: the compiled steps for this state, rendered
        # once — the Specialist executes a decided worklist instead of
        # re-deriving it from the query (marked as possibly incomplete, since a
        # compile-drop escalation means part of the task never compiled).
        worklist_block = ""
        if subtask.worklist:
            numbered = "\n".join(
                f"{i}. {line}" for i, line in enumerate(subtask.worklist, start=1)
            )
            worklist_block = (
                "【你负责的执行清单(规划器已确定,按序执行;参数如与世界状态冲突可修正)】\n"
                f"{numbered}\n"
                "清单可能不完整:若用户需求中属于你的部分未被清单覆盖,请一并补齐。\n"
            )
        # The plan-guided budget may allow more local turns than the flat
        # per-specialist cap — a specialist carrying 8 of the plan's steps
        # needs more than 4 turns. Query-routed crews keep the flat cap.
        local_cap = max(cfg.turns_per_specialist, len(subtask.worklist) + 1)
        while local_turns < local_cap and turn < budget:
            local_turns += 1
            turn += 1
            # Rebuilt every turn: the Blackboard grows as other work lands and
            # the ReAct trace grows with this Specialist's own steps.
            role_block = SPECIALIST_PROMPT_BLOCK.format(
                role=STATES[subtask.state].description
                if subtask.state in STATES
                else subtask.state,
                blackboard=board.render(),
            )
            if refusal:
                role_block += REFUSAL_HANDOFF_BLOCK.format(reason=refusal)
            if worklist_block:
                role_block += worklist_block
            if critic_note:
                role_block += critic_note + "\n"
            system_prompt = DEFAULT_SYSTEM_PROMPT.format(
                current_state=subtask.state,
                # Specialists do not sequence states; the Supervisor does.
                allowed_transitions="(由协调器管理,无需切换)",
                tool_list=tool_list_str,
                resource_block=self._render_resource_block(),
                workflow_block=role_block,
                react_block=self._render_react_block(scratchpad),
                policy_block=self._render_policy_block(),
            )
            resp: LLMResponse = self.llm.call(
                system_prompt=system_prompt,
                user_query=query,
                visible_tools=visible,
                history=history,
                state=subtask.state,
            )
            ctx.log_llm(
                LLMCallRecord(
                    turn=turn,
                    model=self.config.model.name,
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                    latency_ms=resp.latency_ms,
                    stop_reason=resp.stop_reason,
                ),
                text=resp.text,
                reasoning=resp.reasoning,
            )
            _emit_event(event_sink, "on_llm_response", turn, sm.current, resp)
            if scratchpad is not None:
                scratchpad.record_thought(turn, resp.text, resp.reasoning)

            if not resp.tool_calls:
                # The Specialist declared its slice done (or empty).
                mid_work = False
                if resp.text:
                    board.note(f"[{subtask.state}] {' '.join(resp.text.split())[:120]}")
                break
            mid_work = True

            for call in resp.tool_calls:
                if call.name == READ_RESOURCE_TOOL:
                    found, payload, err, lat = self._handle_resource_read(call.arguments, world)
                    record = {
                        "turn": turn, "uri": call.arguments.get("uri"), "found": found,
                        "result_size": len(payload) if isinstance(payload, dict) else 0,
                        "latency_ms": lat, "error": err,
                    }
                    ctx.resource_reads.append(record)
                    _emit_event(event_sink, "on_resource_read", turn, dict(record))
                    history.append({
                        "role": "tool", "name": READ_RESOURCE_TOOL,
                        "tool_call_id": call.call_id, "ok": found, "error_msg": err,
                    })
                    continue

                atomic = (
                    str(call.arguments.get("action") or call.name)
                    if call.name in self._domain_names
                    else call.name
                )
                # The Specialist's cage: its own assignment only. Narrower than
                # the single agent's per-state whitelist, checked the same way.
                if atomic not in subtask.atomics:
                    rec = ToolCallRecord(
                        turn=turn, state=sm.current, visible_tools=visible,
                        visible_count=len(visible), selected=call.name,
                        action=call.arguments.get("action"), args=call.arguments,
                        schema_valid=True, result_ok=False, error_code="OUT_OF_SCOPE",
                        error_msg=(
                            f"tool not in this specialist's assignment for {subtask.state}; "
                            "该部分由其它专家负责"
                        ),
                        result_data={}, world_diff=None, latency_ms=0.0,
                    )
                    ctx.log_tool_call(rec)
                    if event_sink is not None:
                        snap = deep_copy_world(world)
                        _emit_event(event_sink, "on_tool_call", turn, rec, snap, snap)
                    history.append({
                        "role": "tool", "name": call.name, "tool_call_id": call.call_id,
                        "ok": False, "error_code": "OUT_OF_SCOPE", "data": {},
                        "error_msg": rec.error_msg,
                    })
                    continue

                # §4.7 runtime cage — same check, same place: before dispatch.
                decision = self.policy.check(atomic, call.arguments, world)
                if decision.denied:
                    rec = ToolCallRecord(
                        turn=turn, state=sm.current, visible_tools=visible,
                        visible_count=len(visible), selected=call.name,
                        action=call.arguments.get("action"), args=call.arguments,
                        schema_valid=True, result_ok=False, error_code="POLICY_DENIED",
                        error_msg=f"[{decision.rule_id}] {decision.reason}",
                        result_data={"rule_id": decision.rule_id}, world_diff=None,
                        latency_ms=0.0,
                    )
                    ctx.log_tool_call(rec)
                    if event_sink is not None:
                        snap = deep_copy_world(world)
                        _emit_event(event_sink, "on_tool_call", turn, rec, snap, snap)
                    history.append({
                        "role": "tool", "name": call.name, "tool_call_id": call.call_id,
                        "ok": False, "error_code": "POLICY_DENIED",
                        "data": {"rule_id": decision.rule_id},
                        "error_msg": decision.reason,
                    })
                    continue

                # ReAct dedupe — after the assignment check and the cage, same
                # invariant as the interleaved loop: only a call that would have
                # been permitted and repeats observed-done work is absorbed.
                if (
                    scratchpad is not None
                    and self.config.architecture.react.dedupe_repeat_actions
                ):
                    cached = scratchpad.cached(action_signature(atomic, call.arguments))
                    if cached is not None:
                        scratchpad.note_suppressed()
                        history.append({
                            "role": "tool", "name": call.name,
                            "tool_call_id": call.call_id, "ok": True,
                            "error_code": "OK", "data": {"react_cached": True},
                            "error_msg": (
                                "该调用与此前已成功的调用完全相同，世界状态未变，"
                                f"直接复用先前观察: {cached.observation}。"
                                "请继续下一步，不要重复同一调用。"
                            ),
                        })
                        continue

                world_before = deep_copy_world(world) if event_sink is not None else None
                result, parsed, lat, action = self._route_and_dispatch(
                    call.name, call.arguments, world
                )
                intended: list[str] = []
                referenced: list[str] = []
                target_name = action if action is not None else call.name
                try:
                    atomic_meta = self.registry.atomic(target_name)
                except KeyError:
                    atomic_meta = None
                if atomic_meta and parsed is not None:
                    intended = atomic_meta.handler.__class__.intended_entities(parsed)
                    referenced = atomic_meta.handler.__class__.referenced_entities(parsed)
                rec = ToolCallRecord(
                    turn=turn, state=sm.current, visible_tools=visible,
                    visible_count=len(visible), selected=call.name, action=action,
                    args=call.arguments,
                    schema_valid=result.error_code != "SCHEMA_ERROR",
                    result_ok=result.ok, error_code=result.error_code,
                    error_msg=result.error_msg, result_data=result.data,
                    world_diff=result.world_diff, latency_ms=lat,
                    intended_entities=intended, referenced_entities=referenced,
                )
                ctx.log_tool_call(rec)
                if event_sink is not None:
                    _emit_event(
                        event_sink, "on_tool_call", turn, rec, world_before,
                        deep_copy_world(world),
                    )
                thread_data: Any = result.data
                thread_msg = result.error_msg
                if scratchpad is not None:
                    _step, thread_data, thread_msg = scratchpad.observe(
                        turn=turn, tool=call.name, action=action, atomic=atomic,
                        args=call.arguments, ok=result.ok,
                        error_code=result.error_code, error_msg=result.error_msg,
                        data=result.data,
                        world_changed=bool(result.ok and result.world_diff),
                    )
                history.append({
                    "role": "tool", "name": call.name, "tool_call_id": call.call_id,
                    "ok": result.ok, "error_code": result.error_code,
                    "data": thread_data, "error_msg": thread_msg,
                })
                if result.ok:
                    self.policy.record_execution(atomic)
                    subtask.successful_calls += 1
                    # What actually changed, handed to the next Specialist.
                    board.record_diff(result.world_diff)

        subtask.turns_used += local_turns
        truncated = mid_work
        if scratchpad is not None:
            # Accumulate per-specialist ReAct stats into the run-level summary.
            stats = scratchpad.summary()
            agg = ctx.react_summary if ctx.react_summary.get("enabled") else {
                "enabled": True, "steps": 0, "thoughts_recorded": 0,
                "suppressed_repeats": 0, "failed_steps": 0, "hints_emitted": 0,
                "window": stats["window"],
            }
            for key in ("steps", "thoughts_recorded", "suppressed_repeats",
                        "failed_steps", "hints_emitted"):
                agg[key] = agg.get(key, 0) + stats[key]
            ctx.react_summary = agg
        return turn, truncated

    # ------------------------------------------------------------------ engine-native step glue
    # Base bound on the engine's own advancement so a mis-authored conditional
    # cycle can never spin the runtime forever (§4.6.3 circuit-breaker). Loops
    # add their declared iteration budget on top (see _engine_step_budget), so a
    # legitimate long loop with a non-LLM body is not cut off mid-run.
    _MAX_AUTO_STEPS = 2000

    _MAX_SUBWORKFLOW_DEPTH = 8

    @staticmethod
    def _engine_step_budget(engine: WorkflowEngine) -> int:
        loop_budget = sum(
            s.max_iterations for s in engine.wf.steps if isinstance(s, LoopStep)
        )
        return Agent._MAX_AUTO_STEPS + 4 * loop_budget

    def _run_engine_steps(
        self,
        engine: WorkflowEngine,
        wf_state: WorkflowExecutionState,
        world: MockWorld,
        ctx: Any,
        turn: int,
        depth: int = 0,
    ) -> None:
        """Drive the workflow through every step the engine owns — deterministic
        handlers, fixed tool calls, conditional/loop control flow, and nested
        sub-workflows — until it lands on an ``llm_step`` (which the LLM must act
        on) or the workflow finishes. This is where "the engine owns control
        flow" (§4.3.1) actually happens: the LLM is never shown a control-flow,
        deterministic, tool-call, or sub-workflow step.
        """
        steps = 0
        budget = self._engine_step_budget(engine)
        while not wf_state.finished:
            steps += 1
            if steps > budget:
                wf_state.failed_step = wf_state.current_step_id
                wf_state.finished = True
                return
            step = engine.current_step(wf_state)
            if isinstance(step, LLMStep):
                return  # hand control back to the model
            try:
                if isinstance(step, DeterministicStep):
                    self._exec_deterministic_step(engine, wf_state, step, world, ctx, turn)
                elif isinstance(step, ToolCallStep):
                    self._exec_tool_call_step(engine, wf_state, step, world, ctx, turn)
                elif isinstance(step, SubWorkflowStep):
                    self._exec_sub_workflow_step(engine, wf_state, step, world, ctx, turn, depth)
                elif isinstance(step, ConditionalStep):
                    engine.resolve_conditional(wf_state, world, {"workflow_id": engine.wf.name})
                elif isinstance(step, LoopStep):
                    engine.resolve_loop(wf_state, world, {"workflow_id": engine.wf.name})
                else:  # pragma: no cover — defensive; unknown step kind
                    engine.advance(wf_state, succeeded=True)
            except Exception as e:
                # A depends_on/illegal-transition violation (WorkflowError) or a
                # bad/throwing predicate (e.g. an unregistered predicate name)
                # must degrade to a recorded step failure, not crash the run —
                # matching the graceful handling of deterministic handlers.
                ctx.log_tool_call(
                    ToolCallRecord(
                        turn=turn, state=step.state, visible_tools=[], visible_count=0,
                        selected=f"workflow:{step.id}", action=None, args={},
                        schema_valid=True, result_ok=False, error_code="BUSINESS_RULE",
                        error_msg=str(e), result_data={}, world_diff=None, latency_ms=0.0,
                    )
                )
                wf_state.failed_step = step.id
                wf_state.finished = True
                return

    # Kept as an alias: several call sites and tests still reference the old name.
    _maybe_run_deterministic = _run_engine_steps

    def _exec_deterministic_step(
        self, engine: WorkflowEngine, wf_state: WorkflowExecutionState,
        step: DeterministicStep, world: MockWorld, ctx: Any, turn: int,
    ) -> None:
        try:
            fn = get_handler(step.handler)
            t0 = time.perf_counter()
            payload = fn(world, {"workflow_id": engine.wf.name})
            lat = (time.perf_counter() - t0) * 1000
            ctx.log_tool_call(
                ToolCallRecord(
                    turn=turn, state=step.state, visible_tools=[], visible_count=0,
                    selected=f"workflow:{step.handler}", action=None, args={},
                    schema_valid=True, result_ok=True, error_code="OK", error_msg=None,
                    result_data=payload, world_diff=None, latency_ms=lat,
                )
            )
            engine.advance(wf_state, succeeded=True)
        except Exception as e:
            ctx.log_tool_call(
                ToolCallRecord(
                    turn=turn, state=step.state, visible_tools=[], visible_count=0,
                    selected=f"workflow:{step.handler}", action=None, args={},
                    schema_valid=True, result_ok=False, error_code="BUSINESS_RULE",
                    error_msg=str(e), result_data={}, world_diff=None, latency_ms=0.0,
                )
            )
            engine.advance(wf_state, succeeded=False)

    def _exec_tool_call_step(
        self, engine: WorkflowEngine, wf_state: WorkflowExecutionState,
        step: ToolCallStep, world: MockWorld, ctx: Any, turn: int,
    ) -> None:
        """Engine-driven fixed tool dispatch (§4.3.3). Still subject to the §4.7
        runtime cage — a denied call never reaches the handler."""
        policy_target = step.arguments.get("action", step.tool)
        decision = self.policy.check(str(policy_target or step.tool), step.arguments, world)
        if decision.denied:
            ctx.log_tool_call(
                ToolCallRecord(
                    turn=turn, state=step.state, visible_tools=[], visible_count=0,
                    selected=step.tool, action=step.arguments.get("action"),
                    args=step.arguments, schema_valid=True, result_ok=False,
                    error_code="POLICY_DENIED",
                    error_msg=f"[{decision.rule_id}] {decision.reason}",
                    result_data={"rule_id": decision.rule_id}, world_diff=None,
                    latency_ms=0.0,
                )
            )
            engine.advance(wf_state, succeeded=False)
            return
        result, _parsed, lat, action = self._route_and_dispatch(
            step.tool, step.arguments, world
        )
        ctx.log_tool_call(
            ToolCallRecord(
                turn=turn, state=step.state, visible_tools=[], visible_count=0,
                selected=step.tool, action=action, args=step.arguments,
                schema_valid=result.error_code != "SCHEMA_ERROR", result_ok=result.ok,
                error_code=result.error_code, error_msg=result.error_msg,
                result_data=result.data, world_diff=result.world_diff, latency_ms=lat,
            )
        )
        if result.ok:
            self.policy.record_execution(str(policy_target or step.tool))
        engine.advance(wf_state, succeeded=result.ok)

    def _exec_sub_workflow_step(
        self, engine: WorkflowEngine, wf_state: WorkflowExecutionState,
        step: SubWorkflowStep, world: MockWorld, ctx: Any, turn: int, depth: int,
    ) -> None:
        """§4.3.3 nested workflow. Runs the referenced (engine-driven) workflow
        inline to completion, merges its Saga compensations onto the parent's
        stack, and reports success/failure to the parent cursor. A sub-workflow
        that stalls on an ``llm_step`` or exceeds the nesting depth is a failure.
        """
        def _fail(msg: str) -> None:
            ctx.log_tool_call(
                ToolCallRecord(
                    turn=turn, state=step.state, visible_tools=[], visible_count=0,
                    selected=f"subworkflow:{step.workflow}", action=None, args={},
                    schema_valid=True, result_ok=False, error_code="BUSINESS_RULE",
                    error_msg=msg, result_data={}, world_diff=None, latency_ms=0.0,
                )
            )
            engine.advance(wf_state, succeeded=False)

        if depth + 1 > self._MAX_SUBWORKFLOW_DEPTH:
            _fail(f"sub-workflow nesting exceeded depth {self._MAX_SUBWORKFLOW_DEPTH}")
            return
        sub_engine = (
            next(
                (e for e in self.workflow_catalogue.all() if e.wf.name == step.workflow),
                None,
            )
            if self.workflow_catalogue is not None
            else None
        )
        if sub_engine is None:
            _fail(f"unknown sub-workflow {step.workflow!r}")
            return

        sub_state = sub_engine.initial_state()
        self._run_engine_steps(sub_engine, sub_state, world, ctx, turn, depth + 1)
        # Merge the child's compensations so a later *parent* failure unwinds the
        # child's effects too (§4.3.4). Merge regardless of child outcome.
        wf_state.compensations.extend(sub_state.compensations)

        ok = sub_state.finished and sub_state.failed_step is None
        if not ok:
            reason = (
                f"failed at step {sub_state.failed_step!r}"
                if sub_state.failed_step is not None
                else "stalled on an llm_step (sub-workflows must be engine-driven)"
            )
            _fail(f"sub-workflow {step.workflow!r} did not complete: {reason}")
            return
        ctx.log_tool_call(
            ToolCallRecord(
                turn=turn, state=step.state, visible_tools=[], visible_count=0,
                selected=f"subworkflow:{step.workflow}", action=None, args={},
                schema_valid=True, result_ok=True, error_code="OK", error_msg=None,
                result_data={"completed_steps": list(sub_state.completed_steps)},
                world_diff=None, latency_ms=0.0,
            )
        )
        engine.advance(wf_state, succeeded=True)


# ============================================================ assembly helper
def assemble(
    config_path: str | Path,
    model_override: str | None = None,
    provider_override: str | None = None,
    *,
    results_root: str | Path = "results",
    run_id: str | None = None,
    dataset_version: str = "dev",
    code_commit: str = "",
    config_hash_override: str | None = None,
    write_lock: Any | None = None,
) -> Agent:
    """Instantiate and assemble an Agent and its components from a YAML configuration.
    
    This function reads the ExperimentConfig, initializes the Tool Registry, constructs the 
    LLM provider, and optionally loads the Tool Index, Workflow Catalogue, and Resource Registry 
    based on the architecture flags.
    
    Args:
        config_path: Path to the experiment configuration YAML file.
        model_override: Optional model name override (e.g. gpt-4o).
        provider_override: Optional provider override (e.g. openai).
        results_root: Root directory where traces are written.
        run_id: Optional fixed run ID for reproducible experiment directories.
        dataset_version: Dataset version recorded in trace metadata.
        code_commit: Git commit recorded in trace metadata.
        config_hash_override: Optional full config hash recorded in trace metadata.
        
    Returns:
        A fully initialized Agent instance ready for `run()`.
    """
    cfg = load_config(config_path)
    if model_override:
        cfg.model.name = model_override
    if provider_override:
        cfg.model.provider = provider_override
        
    registry = build_default_registry(tool_count=cfg.tool_count)
    llm = build_llm(cfg.model, registry=registry, arch=cfg.architecture)
    cfg_hash = hashlib.sha256(Path(config_path).read_bytes()).hexdigest()
    tracer = Tracer(
        results_root=results_root,
        config_name=cfg.name,
        model_name=cfg.model.name,
        config_hash=config_hash_override or f"sha256:{cfg_hash[:16]}",
        code_commit=code_commit,
        dataset_version=dataset_version,
        run_id=run_id,
        record_llm_io=cfg.trace.record_llm_io,
        write_lock=write_lock,
    )

    tool_index: ToolIndex | None = None
    if cfg.architecture.tool_rag.enabled:
        tool_index = build_index_from_registry(registry)

    workflow_catalogue: WorkflowCatalogue | None = None
    if cfg.architecture.workflow.enabled:
        wf_dir = Path(cfg.architecture.workflow.yaml_path or "workflows")
        # Importing the workflows package registers the deterministic handlers
        # (workflows/handlers.py calls register_handler() at import time).
        import importlib

        with suppress(ImportError):  # pragma: no cover — should always be importable
            importlib.import_module("workflows")
        workflow_catalogue = load_catalogue(wf_dir)

    resource_registry: ResourceRegistry | None = None
    if cfg.architecture.resources_separation:
        resource_registry = build_default_resource_registry()

    return Agent(
        config=cfg,
        registry=registry,
        llm=llm,
        tracer=tracer,
        tool_index=tool_index,
        workflow_catalogue=workflow_catalogue,
        resource_registry=resource_registry,
        policy=build_policy(cfg.safety),
    )


# ============================================================ CLI
def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for running the Orchestrator end-to-end.
    
    Parses command-line arguments to assemble the Agent, populate the MockWorld, 
    and execute a single query, printing the trace summary upon completion.
    
    Args:
        argv: Optional list of command-line arguments.
        
    Returns:
        An integer exit code (0 for success, non-zero for error).
    """
    parser = argparse.ArgumentParser(description="SCADA Agent — Phase 2 runner")
    parser.add_argument("--config", required=True, help="Path to an experiment YAML")
    parser.add_argument("--query", help="Single user query to execute")
    parser.add_argument("--golden-id", default="cli-adhoc")
    parser.add_argument(
        "--seed-demo-world",
        action="store_true",
        default=True,
        help="Pre-seed the world with demo SCADA entities (default on)",
    )
    parser.add_argument(
        "--no-seed-demo-world",
        dest="seed_demo_world",
        action="store_false",
        help="Start with an empty world",
    )
    parser.add_argument("--dry-run", action="store_true", help="Assemble only; do not call LLM")
    args = parser.parse_args(argv)

    agent = assemble(args.config)
    if args.dry_run:
        print(f"[dry-run] config={args.config}  model={agent.config.model.name}")
        print(f"[dry-run] domains: {[d.name for d in agent.registry.all_domains()]}")
        print(f"[dry-run] atomics: {len(agent.registry.all_atomics())}")
        print(f"[dry-run] tool_rag: {agent.tool_index is not None}")
        print(
            f"[dry-run] workflow: "
            f"{len(agent.workflow_catalogue.all()) if agent.workflow_catalogue else 0} workflows"
        )
        print(f"[dry-run] resources: {agent.resource_registry is not None}")
        return 0
    if not args.query:
        print("error: --query is required unless --dry-run", file=sys.stderr)
        return 2

    record = agent.run(
        args.query,
        golden_id=args.golden_id,
        initial_world=build_demo_world() if args.seed_demo_world else None,
    )
    print(f"trace_id        : {record['trace_id']}")
    print(f"terminal_state  : {record['execution']['terminal_state']}")
    print(f"total_turns     : {record['execution']['total_turns']}")
    print(f"tool_calls      : {len(record['tool_calls'])}")
    print(f"resource_reads  : {len(record['resource_reads'])}")
    print(f"jsonl           : {agent.tracer.traces_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
