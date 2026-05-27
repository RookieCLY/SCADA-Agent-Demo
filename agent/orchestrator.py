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
import sys
import time
from pathlib import Path
from typing import Any

from agent.config import ExperimentConfig, load_config
from agent.dispatcher import dispatch_atomic, dispatch_domain
from agent.llm import LLMProvider, LLMResponse, MockLLM, build_llm
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
    LLMStep,
    WorkflowCatalogue,
    WorkflowEngine,
    WorkflowExecutionState,
    get_handler,
    load_catalogue,
)
from resources import ResourceNotFound, ResourceRegistry, build_default_resource_registry
from world import Device, MockWorld, Point
from world.memory_backend import deep_copy_world


READ_RESOURCE_TOOL = "read_resource"  # synthetic tool name for Resource reads


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
【当前可用工具】(仅以下工具可被调用)
{tool_list}
{resource_block}{workflow_block}
【行为准则】
1. 必须从可用工具列表中选择;禁止编造工具名
2. 调用工具前先思考该工具是否真的匹配用户意图
3. 工具参数必须符合提供的 JSON Schema
4. 若信息不足,先调用查询类工具或向用户确认
5. 完成任务后明确告知用户
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
        max_turns: int = 12,
    ) -> None:
        self.config = config
        self.registry = registry
        self.llm = llm
        self.tracer = tracer
        self.max_turns = max_turns
        self.tool_index = tool_index
        self.workflow_catalogue = workflow_catalogue
        self.resource_registry = resource_registry

    # ------------------------------------------------------------------ tool visibility
    def _allowed_atomics(
        self,
        sm_state: str,
        wf_state: WorkflowExecutionState | None,
    ) -> list[str]:
        """Hard filters: state-machine whitelist ∩ workflow-step whitelist."""
        arch = self.config.architecture
        all_atomics = [m.name for m in self.registry.all_atomics()]

        if arch.state_machine.enabled:
            allowed = [t for t in all_atomics if t in STATES[sm_state].allowed_tools]
        else:
            allowed = all_atomics

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
        return allowed

    def _rank_with_rag(self, query: str, allowed_atomics: list[str]) -> list[str]:
        """Soft rank atomic tools and truncate to top_k. Returns atomic names."""
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
    ) -> tuple[list[str], list[str]]:
        """Return (LLM-facing tool names, underlying atomic names).

        LLM-facing list collapses to Domain Tools in hierarchical mode; the
        atomic list is what we actually permit at dispatch time.
        """
        arch = self.config.architecture
        allowed_atomics = self._allowed_atomics(sm_state, wf_state)
        ranked = self._rank_with_rag(query, allowed_atomics)
        if arch.hierarchical_tools:
            seen: list[str] = []
            for atomic in ranked:
                try:
                    domain, _ = self.registry.lookup(atomic)
                except KeyError:
                    continue
                if domain not in seen:
                    seen.append(domain)
            return seen, ranked
        return ranked, ranked

    def _render_tool_list(self, names: list[str]) -> str:
        lines: list[str] = []
        if self.config.architecture.hierarchical_tools:
            for name in names:
                try:
                    d = self.registry.domain(name)
                except KeyError:
                    continue
                lines.append(f"- {name} ({len(d.actions)} actions): {d.description}")
        else:
            for name in names:
                try:
                    m = self.registry.atomic(name)
                except KeyError:
                    continue
                lines.append(f"- {name}: {m.description}")
        return "\n".join(lines)

    def _render_resource_block(self) -> str:
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
        if wf_state is None:
            return ""
        return (
            f"\n【工作流上下文】当前 workflow={wf_state.workflow_id}, "
            f"step={wf_state.current_step_id}\n"
        )

    # ------------------------------------------------------------------ dispatch
    def _route_and_dispatch(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        world: MockWorld,
    ) -> tuple[Any, Any, float, str | None]:
        """Return (ToolResult, parsed_args, latency_ms, action_or_None)."""
        try:
            if any(d.name == tool_name for d in self.registry.all_domains()):
                return dispatch_domain(self.registry, tool_name, arguments, world)
        except KeyError:
            pass
        result, parsed, lat = dispatch_atomic(self.registry, tool_name, arguments, world)
        action = None
        try:
            _, action = self.registry.lookup(tool_name)
        except KeyError:
            pass
        return result, parsed, lat, action

    # ------------------------------------------------------------------ resource read
    def _handle_resource_read(
        self, arguments: dict[str, Any], world: MockWorld
    ) -> tuple[bool, dict[str, Any], str | None, float]:
        """Return (found, payload, error_msg, latency_ms)."""
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
        arch = self.config.architecture
        if not arch.workflow.enabled or self.workflow_catalogue is None:
            return None
        return self.workflow_catalogue.select(query)

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
    ) -> dict[str, Any]:
        world = initial_world or MockWorld()
        _ = deep_copy_world(world)  # preserve initial for potential rollback / inspection
        sm = StateMachine(current=INITIAL_STATE)

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

            while turn < self.max_turns:
                turn += 1
                visible, atomic_pool = self._visible_tools_for(
                    sm.current, query, wf_state
                )
                tool_list_str = self._render_tool_list(visible) or "(no tools allowed)"
                system_prompt = DEFAULT_SYSTEM_PROMPT.format(
                    current_state=sm.current,
                    tool_list=tool_list_str,
                    resource_block=self._render_resource_block(),
                    workflow_block=self._render_workflow_block(wf_state),
                )

                resp: LLMResponse = self.llm.call(
                    system_prompt=system_prompt,
                    user_query=query,
                    visible_tools=[{"name": n} for n in visible],
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
                    )
                )

                if not resp.tool_calls:
                    history.append({"role": "assistant", "content": resp.text or ""})
                    if resp.next_state and resp.next_state != sm.current:
                        if sm.can_transit(resp.next_state):
                            ctx.exit_state()
                            sm.transit(resp.next_state)
                            ctx.enter_state(sm.current)
                            if sm.is_terminal:
                                break
                            continue
                    # No tool call & no state change: try to advance workflow forward
                    if wf_engine is not None and wf_state is not None and not wf_state.finished:
                        self._maybe_run_deterministic(
                            wf_engine, wf_state, world, ctx, turn
                        )
                        if not wf_state.finished:
                            next_state = wf_engine.current_step(wf_state).state
                            if next_state != sm.current and sm.can_transit(next_state):
                                ctx.exit_state()
                                sm.transit(next_state)
                                ctx.enter_state(sm.current)
                                continue
                    break

                # Tool / Resource call branch
                any_tool_dispatched = False
                for call in resp.tool_calls:
                    if call.name == READ_RESOURCE_TOOL:
                        found, payload, err, lat = self._handle_resource_read(
                            call.arguments, world
                        )
                        ctx.resource_reads.append(
                            {
                                "turn": turn,
                                "uri": call.arguments.get("uri"),
                                "found": found,
                                "result_size": len(payload) if isinstance(payload, dict) else 0,
                                "latency_ms": lat,
                                "error": err,
                            }
                        )
                        history.append(
                            {
                                "role": "tool",
                                "name": READ_RESOURCE_TOOL,
                                "ok": found,
                                "error_msg": err,
                            }
                        )
                        continue

                    if self.config.architecture.state_machine.enabled:
                        is_domain = any(
                            d.name == call.name for d in self.registry.all_domains()
                        )
                        # Identify the atomic the call ultimately resolves to,
                        # so we can fast-forward optional workflow steps.
                        atomic_for_check = (
                            call.arguments.get("action", "") if is_domain else call.name
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
                            if new_state != sm.current and sm.can_transit(new_state):
                                ctx.exit_state()
                                sm.transit(new_state)
                                ctx.enter_state(sm.current)
                                atomic_pool = self._allowed_atomics(sm.current, wf_state)
                        permitted = False
                        if is_domain:
                            sub_action = call.arguments.get("action", "")
                            permitted = sub_action in atomic_pool
                        else:
                            permitted = call.name in atomic_pool
                        if not permitted:
                            ctx.log_tool_call(
                                ToolCallRecord(
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
                                    error_msg=f"tool not in whitelist for state {sm.current}",
                                    result_data={},
                                    world_diff=None,
                                    latency_ms=0.0,
                                )
                            )
                            continue

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

                    ctx.log_tool_call(
                        ToolCallRecord(
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
                    )
                    history.append(
                        {
                            "role": "tool",
                            "name": call.name,
                            "ok": result.ok,
                            "error_code": result.error_code,
                            "data": result.data,
                            "error_msg": result.error_msg,
                        }
                    )

                    # Workflow advancement on tool success/failure.
                    # SCHEMA_ERROR means the LLM produced malformed args but
                    # never actually attempted the step's business logic —
                    # treat it as a no-op so the workflow lets the model retry
                    # within the same step instead of finishing prematurely.
                    if wf_engine is not None and wf_state is not None and not wf_state.finished:
                        if result.error_code != "SCHEMA_ERROR":
                            wf_engine.advance(wf_state, succeeded=result.ok)
                    any_tool_dispatched = True

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

                # State machine advancement after the tool batch
                if resp.next_state and resp.next_state != sm.current:
                    if sm.can_transit(resp.next_state):
                        ctx.exit_state()
                        sm.transit(resp.next_state)
                        ctx.enter_state(sm.current)
                if wf_engine is not None and wf_state is not None and not wf_state.finished:
                    self._maybe_run_deterministic(wf_engine, wf_state, world, ctx, turn)
                    if not wf_state.finished:
                        next_state = wf_engine.current_step(wf_state).state
                        if next_state != sm.current and sm.can_transit(next_state):
                            ctx.exit_state()
                            sm.transit(next_state)
                            ctx.enter_state(sm.current)
                if sm.is_terminal:
                    break
            else:
                early = True
                reason = "max_turns exhausted"
                terminal = sm.current

            ctx.final_world_hash = world.hash()
            terminal = sm.current if not early else terminal
            return ctx.finish(
                terminal_state=terminal,
                early_terminated=early,
                termination_reason=reason,
            )

    # ------------------------------------------------------------------ deterministic-step glue
    def _maybe_run_deterministic(
        self,
        engine: WorkflowEngine,
        wf_state: WorkflowExecutionState,
        world: MockWorld,
        ctx: Any,
        turn: int,
    ) -> None:
        step = engine.current_step(wf_state)
        if isinstance(step, LLMStep):
            return
        # deterministic step: run the registered handler
        try:
            fn = get_handler(step.handler)
            t0 = time.perf_counter()
            payload = fn(world, {"workflow_id": engine.wf.name})
            lat = (time.perf_counter() - t0) * 1000
            ctx.log_tool_call(
                ToolCallRecord(
                    turn=turn,
                    state=step.state,
                    visible_tools=[],
                    visible_count=0,
                    selected=f"workflow:{step.handler}",
                    action=None,
                    args={},
                    schema_valid=True,
                    result_ok=True,
                    error_code="OK",
                    error_msg=None,
                    result_data=payload,
                    world_diff=None,
                    latency_ms=lat,
                )
            )
            engine.advance(wf_state, succeeded=True)
        except Exception as e:
            ctx.log_tool_call(
                ToolCallRecord(
                    turn=turn,
                    state=step.state,
                    visible_tools=[],
                    visible_count=0,
                    selected=f"workflow:{step.handler}",
                    action=None,
                    args={},
                    schema_valid=True,
                    result_ok=False,
                    error_code="BUSINESS_RULE",
                    error_msg=str(e),
                    result_data={},
                    world_diff=None,
                    latency_ms=0.0,
                )
            )
            engine.advance(wf_state, succeeded=False)


# ============================================================ assembly helper
def assemble(config_path: str | Path) -> Agent:
    cfg = load_config(config_path)
    registry = build_default_registry()
    llm = build_llm(cfg.model, registry=registry, arch=cfg.architecture)
    cfg_hash = hashlib.sha256(Path(config_path).read_bytes()).hexdigest()
    tracer = Tracer(
        results_root="results",
        config_name=cfg.name,
        model_name=cfg.model.name,
        config_hash=f"sha256:{cfg_hash[:16]}",
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

        try:
            importlib.import_module("workflows")
        except ImportError:  # pragma: no cover — should always be importable
            pass
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
    )


# ============================================================ CLI
def main(argv: list[str] | None = None) -> int:
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
