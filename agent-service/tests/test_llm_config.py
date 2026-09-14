"""LLM config resolution tests — no network, no real keys required beyond env stubs."""

from __future__ import annotations

import pytest

from graph.llm_config import LlmProvider, get_async_client, load_llm_settings


def test_minimax_settings_resolve_single_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-M3")

    settings = load_llm_settings()
    assert settings.provider is LlmProvider.MINIMAX
    assert settings.api_key == "test-minimax-key"
    assert settings.base_url == "https://api.minimax.io/v1"
    assert settings.model == "MiniMax-M3"

    client = get_async_client(settings)
    assert str(client.base_url).rstrip("/") == "https://api.minimax.io/v1"
    assert client.timeout == 60.0


def test_openai_settings_resolve_single_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test-model")

    settings = load_llm_settings()
    assert settings.provider is LlmProvider.OPENAI
    assert settings.base_url is None
    assert settings.model == "gpt-test-model"


def test_openai_default_model_when_unset(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    settings = load_llm_settings()
    assert settings.model == "gpt-5.6-luna"


def test_missing_key_for_active_provider_fails_fast(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="MINIMAX_API_KEY"):
        load_llm_settings()


def test_invalid_provider_fails_fast(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    with pytest.raises(RuntimeError, match="Invalid LLM_PROVIDER"):
        load_llm_settings()


def test_default_provider_is_minimax(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)

    settings = load_llm_settings()
    assert settings.provider is LlmProvider.MINIMAX
    assert settings.model == "MiniMax-M3"


def test_estimate_cost_usd_does_not_require_api_key(monkeypatch):
    from graph.llm_config import estimate_cost_usd

    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    assert estimate_cost_usd(1_000_000, 0) == 0.30
    assert estimate_cost_usd(0, 1_000_000) == 1.20
