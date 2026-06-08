"""Regression tests for state-filtered hierarchical tool actions."""
from __future__ import annotations

from pathlib import Path

from agent.llm import OpenAICompatibleLLM
from agent.orchestrator import assemble
from agent.state_machine import STATES

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"


def _action_titles(schema: dict) -> set[str]:
	branches = schema["function"]["parameters"].get("oneOf", [])
	return {branch["title"] for branch in branches}


def _schema_for(schemas: list[dict], name: str) -> dict:
	return next(schema for schema in schemas if schema["function"]["name"] == name)


def test_hierarchical_prompt_lists_only_allowed_actions():
	agent = assemble(CONFIGS_DIR / "F_full_four_in_one.yaml")
	visible, atomic_pool = agent._visible_tools_for("ANALYZE_INTENT", "维护模式横幅", None)

	manage_pages = next(tool for tool in visible if tool["name"] == "manage_pages")
	assert manage_pages["allowed_actions"] == ["list_pages"]
	assert "create_widget" not in atomic_pool

	rendered = agent._render_tool_list(visible)
	assert "manage_pages" in rendered
	assert "list_pages" in rendered
	assert "create_widget" not in rendered


def test_hierarchical_schema_filters_domain_action_branches():
	agent = assemble(CONFIGS_DIR / "F_full_four_in_one.yaml")
	llm = OpenAICompatibleLLM(
		model="test",
		api_key="test",
		base_url="http://127.0.0.1:9/v1",
		registry=agent.registry,
		hierarchical=True,
	)

	visible, _ = agent._visible_tools_for("ANALYZE_INTENT", "维护模式横幅", None)
	schemas = llm._domain_tool_schemas(visible)
	manage_pages_schema = _schema_for(schemas, "manage_pages")
	assert _action_titles(manage_pages_schema) == {"list_pages"}

	visible, _ = agent._visible_tools_for("MANAGE_PAGES", "维护模式横幅", None)
	schemas = llm._domain_tool_schemas(visible)
	manage_pages_schema = _schema_for(schemas, "manage_pages")
	actions = _action_titles(manage_pages_schema)
	assert "create_widget" in actions
	assert "bind_point" not in actions

	visible, _ = agent._visible_tools_for("BIND_POINTS", "维护模式横幅", None)
	schemas = llm._domain_tool_schemas(visible)
	manage_pages_schema = _schema_for(schemas, "manage_pages")
	assert _action_titles(manage_pages_schema) == {"bind_point", "list_pages"}


def test_state_machine_still_rejects_manually_injected_disallowed_action():
	agent = assemble(CONFIGS_DIR / "F_full_four_in_one.yaml")
	visible, atomic_pool = agent._visible_tools_for("MANAGE_PAGES", "维护模式横幅", None)
	assert "bind_point" not in atomic_pool
	assert "bind_point" not in STATES["MANAGE_PAGES"].allowed_tools
	assert any(tool["name"] == "manage_pages" for tool in visible)
