"""Run the **superseded** orchestrator (``agent_old``) through the current
eval harness, as a "before" arm for loop-structure comparisons.

Why this exists: the pre-``perf/Kate`` commit has no ``eval/runner.py`` and no
``eval/golden_dataset.jsonl`` — both were created *during* that branch — so the
old code cannot be pointed at the golden dataset directly. ``agent_old/`` is the
superseded copy of the orchestrator and still imports the *current*
``agent.config`` / ``agent.tracer`` / ``agent.tool_registry`` / ``agent.llm``,
so it runs today. Driving it with today's dataset, tool library, model, and
metrics isolates the **orchestrator loop** changes with everything else held
constant — which is the only clean code-vs-code comparison available.

``agent_old.orchestrator.assemble`` takes only a config path and hardcodes
``results_root="results"`` with no run id, overrides, or write lock, so it
cannot be handed to the concurrent runner as-is. This adapter mirrors the
signature of the current ``agent.orchestrator.assemble`` and builds an
``agent_old`` Agent instead. It deliberately does **not** pass a safety policy:
the old orchestrator has no §4.7 cage, and inventing one here would
misrepresent the baseline.
"""
from __future__ import annotations

import hashlib
import importlib
from contextlib import suppress
from pathlib import Path
from typing import Any

from agent.config import load_config
from agent.llm import build_llm
from agent.tool_rag import ToolIndex, build_index_from_registry
from agent.tool_registry import build_default_registry
from agent.tracer import Tracer
from agent.workflow import WorkflowCatalogue, load_catalogue
from resources import ResourceRegistry, build_default_resource_registry

__all__ = ["assemble_baseline"]


def assemble_baseline(
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
):
    """Signature-compatible with ``agent.orchestrator.assemble``, but returns an
    ``agent_old.orchestrator.Agent``.

    Imported lazily so that ``agent_old`` — which is otherwise dead code — is
    only loaded when the baseline arm is actually requested.
    """
    from agent_old.orchestrator import Agent as OldAgent

    cfg = load_config(config_path)
    if model_override:
        cfg.model.name = model_override
    if provider_override:
        cfg.model.provider = provider_override

    # ``build_default_registry(tool_count=...)`` did not exist in the old
    # orchestrator's era, but the registry itself is current — pass the config's
    # tool_count so the baseline sees the *same* tool library as the new arms.
    # Anything else would confound a loop-structure comparison with a
    # tool-count difference.
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
        with suppress(ImportError):  # registers deterministic handlers
            importlib.import_module("workflows")
        workflow_catalogue = load_catalogue(wf_dir)

    resource_registry: ResourceRegistry | None = None
    if cfg.architecture.resources_separation:
        resource_registry = build_default_resource_registry()

    return OldAgent(
        config=cfg,
        registry=registry,
        llm=llm,
        tracer=tracer,
        tool_index=tool_index,
        workflow_catalogue=workflow_catalogue,
        resource_registry=resource_registry,
    )
