"""Regression: ``read_resource`` must reach the *function-call schema*, not just prose.

The original defect (§4.5): ``_render_resource_block`` described the Resource URIs
in the system prompt, and the orchestrator had a live dispatch branch for
``read_resource`` — but no schema builder ever emitted the tool, so with any real
provider the model was told resources existed and given no way to reach them.
``resource_reads`` was 0 across 1,467 runs, in every config.

Asserting on the prompt text would *not* have caught this. These tests assert on
the schema handed to the provider, and on the descriptor the orchestrator emits.
"""
from __future__ import annotations

from pathlib import Path

from agent.llm import READ_RESOURCE_TOOL, OpenAICompatibleLLM
from agent.orchestrator import assemble
from agent.tool_registry import build_default_registry

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"
URIS = ["scada://pages", "scada://pages/{page_id}/widgets"]


def _llm(*, hierarchical: bool) -> OpenAICompatibleLLM:
    """Adapter pointed at a dead endpoint — no request is issued; these tests
    only exercise the pure schema builders."""
    return OpenAICompatibleLLM(
        model="test",
        api_key="test",
        base_url="http://127.0.0.1:9/v1",
        registry=build_default_registry(),
        hierarchical=hierarchical,
    )


def _find(schemas: list[dict]) -> dict | None:
    return next(
        (s for s in schemas if s["function"]["name"] == READ_RESOURCE_TOOL), None
    )


def _assert_uri_enum(schema: dict, expected: list[str]) -> None:
    params = schema["function"]["parameters"]
    assert params["type"] == "object"
    assert params["required"] == ["uri"]
    uri = params["properties"]["uri"]
    assert uri["type"] == "string"
    # The enum is what stops the model inventing URIs no template matches.
    assert uri["enum"] == expected


# ------------------------------------------------------- schema builders
def test_read_resource_in_hierarchical_schema():
    schemas = _llm(hierarchical=True)._domain_tool_schemas(
        [
            {"name": "manage_pages", "allowed_actions": ["list_pages"]},
            {"name": READ_RESOURCE_TOOL, "uris": URIS},
        ]
    )
    found = _find(schemas)
    assert found is not None, "read_resource missing from hierarchical schema"
    _assert_uri_enum(found, URIS)
    # It must not displace the real domain tools.
    assert any(s["function"]["name"] == "manage_pages" for s in schemas)


def test_read_resource_in_flat_schema():
    llm = _llm(hierarchical=False)
    schemas = llm._flat_tool_schemas(["create_page"])
    schemas.append(llm._read_resource_schema(URIS))
    found = _find(schemas)
    assert found is not None, "read_resource missing from flat schema"
    _assert_uri_enum(found, URIS)


def test_flat_builder_skips_the_synthetic_name():
    """``read_resource`` has no registry entry — the atomic builder must skip it
    rather than emit a junk schema."""
    schemas = _llm(hierarchical=False)._flat_tool_schemas(
        ["create_page", READ_RESOURCE_TOOL]
    )
    assert _find(schemas) is None
    assert [s["function"]["name"] for s in schemas] == ["create_page"]


def test_empty_uri_list_yields_empty_enum():
    """An empty registry must not degrade to an unconstrained string."""
    schema = _llm(hierarchical=False)._read_resource_schema([])
    assert schema["function"]["parameters"]["properties"]["uri"]["enum"] == []


# ------------------------------------------------------- orchestrator wiring
def test_orchestrator_emits_read_resource_when_separation_on():
    agent = assemble(CONFIGS_DIR / "F_full_four_in_one.yaml")
    visible, atomic_pool = agent._visible_tools_for("ANALYZE_INTENT", "列出所有页面", None)

    descriptor = next(
        (t for t in visible if t["name"] == READ_RESOURCE_TOOL), None
    )
    assert descriptor is not None, "orchestrator did not emit a read_resource descriptor"
    assert descriptor["uris"], "descriptor carries no URIs, so the enum would be empty"

    # Synthetic: the run loop intercepts it before dispatch, so it must not look
    # like a registry atomic to metrics or the dispatch gate.
    assert READ_RESOURCE_TOOL not in atomic_pool

    # And it must survive into the schema the provider actually receives.
    schemas = _llm(hierarchical=True)._domain_tool_schemas(visible)
    assert _find(schemas) is not None


def test_orchestrator_omits_read_resource_when_separation_off():
    agent = assemble(CONFIGS_DIR / "E_with_state_machine.yaml")
    assert not agent.config.architecture.resources_separation
    visible, _ = agent._visible_tools_for("ANALYZE_INTENT", "列出所有页面", None)
    assert all(t["name"] != READ_RESOURCE_TOOL for t in visible)
