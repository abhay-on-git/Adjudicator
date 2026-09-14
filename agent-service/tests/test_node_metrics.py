"""Unit tests for node_metrics recording — no LLM, no graph."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from graph.llm_config import estimate_cost_usd
from graph.nodes.llm_utils import (
    STRUCTURED_OUTPUT_TEMPERATURE,
    add_llm_usage,
    consume_llm_usage,
    parse_structured,
    reset_llm_usage,
    usage_from_response,
)
from graph.nodes.metrics import attach_metrics, timed_node
from graph.nodes.intake import intake_normalize


def test_usage_from_response_reads_prompt_and_completion_tokens():
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=120, completion_tokens=40))
    assert usage_from_response(response) == (120, 40)


def test_usage_from_response_reads_openai_input_output_tokens():
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=90, output_tokens=10))
    assert usage_from_response(response) == (90, 10)


def test_usage_from_response_missing_usage_is_zero():
    assert usage_from_response(SimpleNamespace()) == (0, 0)


def test_usage_accumulator_sums_retries():
    reset_llm_usage()
    add_llm_usage(100, 20)
    add_llm_usage(50, 10)
    usage = consume_llm_usage()
    assert usage.tokens_in == 150
    assert usage.tokens_out == 30
    assert usage.tokens == 180
    leftover = consume_llm_usage()
    assert leftover.tokens == 0


def test_minimax_cost_uses_published_m3_rates(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    # 1M in + 1M out at $0.30 / $1.20
    assert estimate_cost_usd(1_000_000, 1_000_000) == 1.50


def test_timed_node_records_zero_tokens_for_non_llm_node():
    state = {
        "raw_input": {
            "claim_id": "CLM-001",
            "policy_id": "POL-HOME-01",
            "policy_start_date": "2024-01-01",
            "filed_date": "2024-08-02",
            "claimant_name": "Priya Nair",
            "claimant_gender": "female",
            "claimant_city": "Pune",
            "narrative_text": "A pipe burst.",
        }
    }
    result = intake_normalize(state)
    metrics = result["node_metrics"]["intake_normalize"]
    assert metrics["tokens"] == 0
    assert metrics["cost_usd"] == 0.0
    assert metrics["latency_ms"] >= 0


def test_attach_metrics_includes_llm_usage():
    reset_llm_usage()
    add_llm_usage(800, 200)
    started = 0.0
    # Force a known latency by using attach_metrics with a started timestamp
    # in the past is racy; just check tokens/cost keys and that latency is a float.
    import time

    started = time.perf_counter()
    result = attach_metrics("extraction", {}, started, llm=True)
    metrics = result["node_metrics"]["extraction"]
    assert metrics["tokens"] == 1000
    assert metrics["tokens_in"] == 800
    assert metrics["tokens_out"] == 200
    assert metrics["cost_usd"] > 0
    assert isinstance(metrics["latency_ms"], float)


def test_timed_node_decorator_on_sync_function():
    @timed_node("toy")
    def toy(_state):
        return {"ok": True}

    result = toy({})
    assert result["ok"] is True
    assert result["node_metrics"]["toy"]["tokens"] == 0


def test_structured_output_temperature_is_zero():
    assert STRUCTURED_OUTPUT_TEMPERATURE == 0.0


class _ToySchema(BaseModel):
    n: int


@pytest.mark.asyncio
async def test_minimax_parse_sends_temperature_zero(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    captured: dict = {}

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"n": 1}'))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
            )

    class _Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_Completions())

    parsed = await parse_structured(
        system="sys", user="user", text_format=_ToySchema, client=_Client()
    )
    assert parsed.n == 1
    assert captured["temperature"] == 0.0
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
