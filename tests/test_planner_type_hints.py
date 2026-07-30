"""Regression: the planning catalogue must declare tuple *arity*, not just "array".

Every measured compile drop on the 106-case run was ``schema_invalid`` — the
planner picked the right tool and wrote the wrong argument *shape*. The two
leaders were ``create_page`` (14) and ``create_widget`` (11), and both fail on
fixed-length integer tuples (``position``, ``size``, ``resolution``).

Those fields are declared with JSON-Schema ``prefixItems`` and carry no ``items``
key at all, so ``_type_hint``'s ``prop.get("items")`` lookup fell through to a
bare ``"array"``: the planner was told a 2-integer tuple was "an array".
"""
from __future__ import annotations

import pytest

from agent.planner import _type_hint, describe_tools_for_planner
from agent.tool_registry import build_default_registry


@pytest.fixture(scope="module")
def registry():
    return build_default_registry()


# ------------------------------------------------------------ _type_hint
def test_prefix_items_tuple_renders_arity():
    """The shape that actually broke: minItems == maxItems == 2, no `items`."""
    prop = {
        "type": "array",
        "minItems": 2,
        "maxItems": 2,
        "prefixItems": [{"type": "integer"}, {"type": "integer"}],
    }
    assert _type_hint(prop) == "array[integer]×2"


def test_heterogeneous_tuple_renders_each_slot():
    prop = {
        "type": "array",
        "minItems": 2,
        "maxItems": 2,
        "prefixItems": [{"type": "string"}, {"type": "number"}],
    }
    assert _type_hint(prop) == "[string, number]"


def test_bounded_homogeneous_array_renders_arity():
    prop = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
    assert _type_hint(prop) == "array[number]×3"


def test_unbounded_array_stays_unbounded():
    """An open-ended list must NOT gain a fake arity."""
    assert _type_hint({"type": "array", "items": {"type": "string"}}) == "array[string]"


def test_scalars_and_enums_unchanged():
    assert _type_hint({"type": "string"}) == "string"
    assert _type_hint({"enum": ["high", "low"]}) == "high|low"
    assert _type_hint({}) == "any"


# ------------------------------------------- the two measured drop leaders
@pytest.mark.parametrize(
    "tool, field, expected",
    [
        # required tuples
        ("create_widget", "position", "array[integer]×2"),
        ("create_widget", "size", "array[integer]×2"),
        # optional tuple — create_page's ONLY non-scalar field, and create_page
        # led the drop table, so a name-only rendering hid the whole problem.
        ("create_page", "resolution", "array[integer]×2"),
    ],
)
def test_drop_leaders_declare_tuple_arity(registry, tool, field, expected):
    schema = registry.atomic(tool).args_model.model_json_schema()
    prop = (schema.get("properties") or {})[field]
    assert _type_hint(prop) == expected

    # And it must survive into the rendered catalogue line the planner sees.
    line = describe_tools_for_planner(registry, [tool], max_tools=10)
    assert f"{field}:{expected}" in line, line


def test_catalogue_never_renders_a_bare_array_for_a_tuple(registry):
    """Guard the regression class, not just the two known tools: no tool in the
    registry may render a fixed-length tuple as an unshaped ``array``."""
    offenders: list[str] = []
    for meta in registry.all_atomics():
        schema = meta.args_model.model_json_schema()
        for field_name, prop in (schema.get("properties") or {}).items():
            if "prefixItems" in prop and _type_hint(prop) == "array":
                offenders.append(f"{meta.name}.{field_name}")
    assert not offenders, f"tuple fields rendered as bare 'array': {offenders}"
