"""Env-driven LLM provider config for OpenAI and MiniMax.

Switch providers with `LLM_PROVIDER=openai|minimax`. Each provider has a
single model name. Keys and model names are resolved from env; never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Never

from openai import AsyncOpenAI

_DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
_DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/v1"
_DEFAULT_MINIMAX_MODEL = "MiniMax-M3"

# USD per million tokens — used only for eval `node_metrics.cost_usd`.
# MiniMax-M3 standard ≤512k pay-as-you-go (published list with the current
# 50% off applied): $0.30 input / $1.20 output.
# OpenAI default is a gpt-4o-class placeholder so an OpenAI-provider eval
# stays internally comparable; override via env if the live model differs.
_MINIMAX_INPUT_USD_PER_MILLION = 0.30
_MINIMAX_OUTPUT_USD_PER_MILLION = 1.20
_OPENAI_INPUT_USD_PER_MILLION = 2.50
_OPENAI_OUTPUT_USD_PER_MILLION = 10.00


class LlmProvider(str, Enum):
    OPENAI = "openai"
    MINIMAX = "minimax"


@dataclass(frozen=True)
class LlmSettings:
    provider: LlmProvider
    api_key: str
    base_url: str | None
    model: str


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required env var {name} for the active LLM provider. "
            "Set it in agent-service/.env (never commit real keys)."
        )
    return value


def load_llm_settings() -> LlmSettings:
    raw = os.environ.get("LLM_PROVIDER", "minimax").strip().lower()
    try:
        provider = LlmProvider(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid LLM_PROVIDER={raw!r}; expected one of: "
            f"{', '.join(p.value for p in LlmProvider)}"
        ) from exc

    if provider is LlmProvider.OPENAI:
        model = (
            os.environ.get("OPENAI_MODEL", _DEFAULT_OPENAI_MODEL).strip()
            or _DEFAULT_OPENAI_MODEL
        )
        base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
        return LlmSettings(
            provider=provider,
            api_key=_require("OPENAI_API_KEY"),
            base_url=base_url,
            model=model,
        )

    if provider is LlmProvider.MINIMAX:
        model = (
            os.environ.get("MINIMAX_MODEL", _DEFAULT_MINIMAX_MODEL).strip()
            or _DEFAULT_MINIMAX_MODEL
        )
        return LlmSettings(
            provider=provider,
            api_key=_require("MINIMAX_API_KEY"),
            base_url=(
                os.environ.get("MINIMAX_BASE_URL", _DEFAULT_MINIMAX_BASE_URL).strip()
                or _DEFAULT_MINIMAX_BASE_URL
            ),
            model=model,
        )

    unreachable: Never = provider
    raise RuntimeError(f"Unhandled LLM provider: {unreachable}")


def _usd_per_million(provider: LlmProvider) -> tuple[float, float]:
    if provider is LlmProvider.MINIMAX:
        return (
            float(os.environ.get("LLM_INPUT_USD_PER_MILLION", _MINIMAX_INPUT_USD_PER_MILLION)),
            float(os.environ.get("LLM_OUTPUT_USD_PER_MILLION", _MINIMAX_OUTPUT_USD_PER_MILLION)),
        )
    if provider is LlmProvider.OPENAI:
        return (
            float(os.environ.get("LLM_INPUT_USD_PER_MILLION", _OPENAI_INPUT_USD_PER_MILLION)),
            float(os.environ.get("LLM_OUTPUT_USD_PER_MILLION", _OPENAI_OUTPUT_USD_PER_MILLION)),
        )
    unreachable: Never = provider
    raise RuntimeError(f"Unhandled LLM provider: {unreachable}")


def estimate_cost_usd(tokens_in: int, tokens_out: int, settings: LlmSettings | None = None) -> float:
    """List-price estimate from token counts. Does not require an API key."""
    if settings is not None:
        provider = settings.provider
    else:
        raw = os.environ.get("LLM_PROVIDER", "minimax").strip().lower()
        try:
            provider = LlmProvider(raw)
        except ValueError:
            provider = LlmProvider.MINIMAX
    input_rate, output_rate = _usd_per_million(provider)
    return (tokens_in / 1_000_000.0) * input_rate + (tokens_out / 1_000_000.0) * output_rate


def get_async_client(settings: LlmSettings | None = None) -> AsyncOpenAI:
    cfg = settings if settings is not None else load_llm_settings()
    kwargs: dict = {"api_key": cfg.api_key}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return AsyncOpenAI(**kwargs)
