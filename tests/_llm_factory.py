"""Test-time LLM factory.

Returns a real LLM when a provider API key is available; otherwise returns
``MockLLM``. The factory mirrors the env-var conventions of ``agent.llm.build_llm``
so the repo-root ``.env`` is auto-loaded.

Behaviour:
  * ``XIAOMI-MIMO_API_KEY`` present  →  ``OpenAICompatibleLLM`` (xiaomi-mimo).
  * Key absent, or ``TEST_LLM_PROVIDER=mock`` env override set  →  ``MockLLM``.

Tests that depend on ``MockLLM``'s scripted behaviour should be marked
``@pytest.mark.mock_only`` (see ``conftest.py``) — the auto-upgrade fixture
will then leave ``build_llm`` untouched for those tests.
"""
from __future__ import annotations

import os
from typing import Any

from agent.config import ArchitectureConfig, ModelConfig
from agent.llm import (
    LLMProvider,
    MockLLM,
    OpenAICompatibleLLM,
    _env,
    _load_dotenv_into_environ,
)


def _provider_override() -> str:
    return os.environ.get("TEST_LLM_PROVIDER", "auto").strip().lower()


def real_llm_available() -> bool:
    """True iff a real LLM API key is available (auto-loaded from .env).

    Honours ``TEST_LLM_PROVIDER=mock`` as a forced override so CI can pin
    every test to MockLLM even when ``.env`` carries a key.
    """
    if _provider_override() == "mock":
        return False
    _load_dotenv_into_environ()
    return bool(_env("XIAOMI-MIMO_API_KEY", "XIAOMI_MIMO_API_KEY"))


def make_test_llm(
    *,
    registry: Any = None,
    arch: ArchitectureConfig | None = None,
    force_mock: bool = False,
    model_name: str | None = None,
) -> LLMProvider:
    """Return a real LLM when a key is set and ``force_mock`` is False; else MockLLM."""
    if force_mock or not real_llm_available():
        return MockLLM(model_name=model_name or "mock")
    _load_dotenv_into_environ()
    api_key = _env("XIAOMI-MIMO_API_KEY", "XIAOMI_MIMO_API_KEY")
    base_url = _env("XIAOMI-MIMO_API_URL", "XIAOMI_MIMO_API_URL")
    model = (
        _env("XIAOMI-MIMO_MODEL", "XIAOMI_MIMO_MODEL")
        or model_name
        or "mimo-v2.5-pro"
    )
    hierarchical = bool(arch and getattr(arch, "hierarchical_tools", False))
    if not api_key or not base_url:
        return MockLLM(model_name=model_name or "mock")
    return OpenAICompatibleLLM(
        model=model,
        api_key=api_key,
        base_url=base_url,
        registry=registry,
        hierarchical=hierarchical,
    )


def make_test_model_config(force_mock: bool = False) -> ModelConfig:
    """Return a ``ModelConfig`` matching the LLM the factory would build.

    Used by tests that construct an ``ExperimentConfig`` directly so the
    ``cfg.model.name`` field shown in traces reflects the actual backend.
    """
    if force_mock or not real_llm_available():
        return ModelConfig(provider="mock", name="mock")
    _load_dotenv_into_environ()
    model = _env("XIAOMI-MIMO_MODEL", "XIAOMI_MIMO_MODEL") or "mimo-v2.5-pro"
    return ModelConfig(provider="xiaomi-mimo", name=model)


__all__ = [
    "make_test_llm",
    "make_test_model_config",
    "real_llm_available",
]
