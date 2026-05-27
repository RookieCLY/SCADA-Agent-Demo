"""Each experiment YAML in configs/ must load + assemble end-to-end."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import load_config
from agent.orchestrator import assemble

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"


@pytest.mark.parametrize("yaml_name", sorted(p.name for p in CONFIGS_DIR.glob("*.yaml")))
def test_config_loads_and_assembles(yaml_name: str):
    path = CONFIGS_DIR / yaml_name
    cfg = load_config(path)
    assert cfg.name
    agent = assemble(path)
    arch = cfg.architecture
    # The orchestrator must wire each component when the flag is on:
    assert (agent.tool_index is not None) == arch.tool_rag.enabled
    assert (agent.workflow_catalogue is not None) == arch.workflow.enabled
    assert (agent.resource_registry is not None) == arch.resources_separation


# ============================================================ matrix coverage
EXPECTED_MATRIX = {
    "A_flat_baseline":     (False, False, False, False, False),
    "B_hierarchical_only": (True,  False, False, False, False),
    "C_hier_rag":          (True,  True,  False, False, False),
    "D_hier_rag_workflow": (True,  True,  True,  False, False),
    "E_with_state_machine":(True,  True,  True,  True,  False),
    "F_full_four_in_one":  (True,  True,  True,  True,  True),
}


@pytest.mark.parametrize("name,expected", EXPECTED_MATRIX.items())
def test_phase2_matrix_present_and_correct(name: str, expected: tuple[bool, ...]):
    cfg = load_config(CONFIGS_DIR / f"{name}.yaml")
    arch = cfg.architecture
    actual = (
        arch.hierarchical_tools,
        arch.tool_rag.enabled,
        arch.workflow.enabled,
        arch.state_machine.enabled,
        arch.resources_separation,
    )
    assert actual == expected, f"{name}: matrix mismatch — got {actual}, want {expected}"
