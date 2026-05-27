"""Shared pytest fixtures.

This conftest also installs an autouse fixture that transparently swaps the
``mock`` LLM provider with the real ``xiaomi-mimo`` backend whenever an API
key is detected on disk (``.env``) or in the environment. The goal is to let
the existing test suite double as a real-LLM regression run when credentials
are available, while degrading cleanly to ``MockLLM`` otherwise.

Tests that *must* keep MockLLM (e.g. those asserting scripted regex outputs
or monkeypatching ``MockLLM.call``) should be decorated with
``@pytest.mark.mock_only`` to opt out of the auto-upgrade.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.tool_registry import build_default_registry
from world import Device, MockWorld, Point

from tests._llm_factory import (
    make_test_llm,
    make_test_model_config,
    real_llm_available,
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "mock_only: keep MockLLM for this test even when a real-LLM API key is set",
    )


@pytest.fixture(scope="session")
def real_llm_enabled() -> bool:
    """Session-level cache of whether a real LLM key is available."""
    return real_llm_available()


@pytest.fixture
def default_llm(request, registry):
    """Real LLM when ``XIAOMI-MIMO_API_KEY`` is set, else MockLLM.

    Honours the ``mock_only`` marker — marked tests always get MockLLM.
    """
    force_mock = request.node.get_closest_marker("mock_only") is not None
    return make_test_llm(registry=registry, force_mock=force_mock)


@pytest.fixture
def default_model_config(request):
    force_mock = request.node.get_closest_marker("mock_only") is not None
    return make_test_model_config(force_mock=force_mock)


@pytest.fixture(autouse=True)
def _auto_upgrade_build_llm(request, monkeypatch):
    """Re-route ``agent.llm.build_llm`` so ``assemble()`` uses the test factory.

    When this fixture is active:

    * ``provider="mock"`` configs are upgraded to ``xiaomi-mimo`` if a key exists.
    * ``provider="xiaomi-mimo"`` configs are downgraded to ``MockLLM`` if the
      key is missing, so configs like ``configs/xiaomi_mimo_smoke.yaml`` no
      longer crash test_configs.py when running on a fresh checkout.

    Tests carrying ``@pytest.mark.mock_only`` skip the patch entirely so that
    direct assertions on ``MockLLM`` and monkeypatches against it still work.
    """
    if request.node.get_closest_marker("mock_only"):
        return

    from agent import llm as llm_mod, orchestrator as orch_mod

    orig = llm_mod.build_llm

    def patched(cfg, *, registry=None, arch=None):
        if cfg.provider in ("mock", "xiaomi-mimo"):
            return make_test_llm(
                registry=registry, arch=arch, model_name=cfg.name
            )
        return orig(cfg, registry=registry, arch=arch)

    monkeypatch.setattr(llm_mod, "build_llm", patched)
    monkeypatch.setattr(orch_mod, "build_llm", patched)


@pytest.fixture
def fresh_world() -> MockWorld:
    return MockWorld()


@pytest.fixture
def chemical_world() -> MockWorld:
    """Pre-loaded with the §G.1 demo entities."""
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


@pytest.fixture
def registry():
    return build_default_registry()


@pytest.fixture
def tmp_results(tmp_path: Path) -> Path:
    out = tmp_path / "results"
    out.mkdir()
    return out
