"""B5: OpenAICompatibleLLM caches the assembled domain-tool schema.

The hierarchical `oneOf` schema for a domain is static per (domain,
allowed-actions) set, but was rebuilt on every turn. These tests confirm the
per-instance cache returns the identical assembled object for a repeated
(domain, actions) key, keys distinctly on the action set, and still produces a
correct discriminated-union schema. Constructs the adapter directly (no network
— __init__ only builds the client object; no request is made).
"""
from __future__ import annotations

from agent.llm import OpenAICompatibleLLM
from agent.tool_registry import build_default_registry


def _llm():
    reg = build_default_registry()
    llm = OpenAICompatibleLLM(
        model="fake", api_key="x", base_url="http://localhost",
        registry=reg, hierarchical=True,
    )
    return llm, reg


def test_domain_schema_cached_and_correct():
    llm, reg = _llm()
    domain = reg.all_domains()[0]
    actions = list(domain.actions.keys())
    assert actions, "expected the domain to expose at least one action"

    desc = [{"name": domain.name, "allowed_actions": actions[:1]}]
    s1 = llm._domain_tool_schemas(desc)
    s2 = llm._domain_tool_schemas([{"name": domain.name, "allowed_actions": actions[:1]}])

    # Correct shape: one function per domain, a discriminated oneOf union.
    assert len(s1) == 1
    fn = s1[0]["function"]
    assert fn["name"] == domain.name
    assert "oneOf" in fn["parameters"]
    branch = fn["parameters"]["oneOf"][0]
    assert branch["properties"]["action"]["const"] == actions[0]

    # Same (domain, actions) key → the identical cached object is returned.
    assert s1[0] is s2[0]


def test_domain_schema_keys_on_action_set():
    llm, reg = _llm()
    domain = next((d for d in reg.all_domains() if len(d.actions) >= 2), None)
    if domain is None:  # pragma: no cover — registry always has multi-action domains
        return
    actions = list(domain.actions.keys())

    s_one = llm._domain_tool_schemas([{"name": domain.name, "allowed_actions": actions[:1]}])
    s_two = llm._domain_tool_schemas([{"name": domain.name, "allowed_actions": actions[:2]}])

    # Different action sets are distinct cache entries (not aliased).
    assert s_one[0] is not s_two[0]
    assert len(s_one[0]["function"]["parameters"]["oneOf"]) == 1
    assert len(s_two[0]["function"]["parameters"]["oneOf"]) == 2
    assert len(llm._domain_schema_cache) >= 2
