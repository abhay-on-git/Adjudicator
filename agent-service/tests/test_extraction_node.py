"""extraction node tests. No real LLM calls — every test mocks either the
low-level `_call_llm` helper (for retry/escalation logic) or
`parse_structured` (for at least one test verifying the retry loop against
the real call path), so these run with no API key and no network access."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import APIConnectionError
from pydantic import ValidationError

import graph.nodes.extraction as extraction_module
from graph.nodes.extraction import extraction
from graph.schemas import AuditEventType, ExtractedNarrativeFacts, Peril


def make_state(narrative="A pipe burst suddenly."):
    return {
        "normalized_envelope": {
            "policy_id": "POL-HOME-01",
            "policy_start_date": "2024-01-01",
            "narrative_text": narrative,
        },
        "extraction_attempts": 0,
    }


def make_good_facts() -> ExtractedNarrativeFacts:
    return ExtractedNarrativeFacts(
        date_of_loss="2024-06-01", perils=[Peril.WATER_DAMAGE],
        line_items=[], narrative_summary="test",
    )


@pytest.mark.asyncio
async def test_successful_extraction_merges_structural_fields(monkeypatch):
    async def fake_call_llm_with_backoff(narrative_text, extra_instruction=""):
        return make_good_facts(), None

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", fake_call_llm_with_backoff)
    result = await extraction(make_state())
    facts = result["claim_facts"]
    assert facts.policy_id == "POL-HOME-01"  # merged in, never asked of the LLM
    assert facts.policy_start_date == "2024-01-01"
    assert result["audit_log"][-1].event_type == AuditEventType.EXTRACTION_COMPLETE


@pytest.mark.asyncio
async def test_api_failure_exhausts_retries_and_escalates(monkeypatch):
    async def always_fails(narrative_text, extra_instruction=""):
        return None, "RateLimitError: too many requests"

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", always_fails)
    result = await extraction(make_state())
    assert "claim_facts" not in result
    assert result["escalation_reason"] == "adjudication service unavailable"
    assert result["audit_log"][0].event_type == AuditEventType.LLM_UNAVAILABLE


@pytest.mark.asyncio
async def test_validation_failure_retries_once_then_succeeds(monkeypatch):
    calls = {"n": 0}

    async def fake_call_llm_with_backoff(narrative_text, extra_instruction=""):
        calls["n"] += 1
        if calls["n"] == 1:
            # First "success" from the LLM's own perspective, but merging with
            # structural fields will fail because we monkeypatch ClaimFacts
            # construction to fail on the first pass via a bad policy_id below.
            return make_good_facts(), None
        return make_good_facts(), None

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", fake_call_llm_with_backoff)

    # Force the FIRST ClaimFacts construction to fail validation, second to succeed,
    # by monkeypatching ClaimFacts itself to raise once then delegate.
    from graph import schemas as schemas_module

    real_claim_facts = schemas_module.ClaimFacts
    state = {"n": 0}

    class FlakyClaimFacts:
        def __new__(cls, *args, **kwargs):
            state["n"] += 1
            if state["n"] == 1:
                raise ValidationError.from_exception_data("ClaimFacts", [
                    {"type": "missing", "loc": ("date_of_loss",), "input": None}
                ])
            return real_claim_facts(*args, **kwargs)

    monkeypatch.setattr(extraction_module, "ClaimFacts", FlakyClaimFacts)
    result = await extraction(make_state())
    assert calls["n"] == 2  # retried once
    assert result["claim_facts"] is not None
    assert any(e.event_type == AuditEventType.EXTRACTION_RETRY for e in result["audit_log"])


def _json_invalid_error() -> ValidationError:
    """Same class of failure as the live CLM-002 MiniMax parse: pydantic
    `model_validate_json` raises ValidationError(json_invalid), not JSONDecodeError."""
    try:
        ExtractedNarrativeFacts.model_validate_json(
            '{" "date_of_loss": "2024-05-10", "cause_ambiguous": false}'
        )
    except ValidationError as exc:
        return exc
    raise AssertionError("expected json_invalid ValidationError")


@pytest.mark.asyncio
async def test_parse_json_invalid_retries_once_then_succeeds(monkeypatch):
    calls: list[str] = []

    async def fake_call_llm_with_backoff(narrative_text, extra_instruction=""):
        calls.append(extra_instruction)
        if not extra_instruction:
            raise _json_invalid_error()
        return make_good_facts(), None

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", fake_call_llm_with_backoff)
    result = await extraction(make_state())
    assert len(calls) == 2
    assert calls[0] == ""
    assert "Invalid JSON" in calls[1]
    assert result["claim_facts"] is not None
    assert "escalation_reason" not in result
    assert any(e.event_type == AuditEventType.EXTRACTION_RETRY for e in result["audit_log"])


@pytest.mark.asyncio
async def test_parse_json_invalid_twice_escalates_with_extraction_failed(monkeypatch):
    async def always_json_invalid(narrative_text, extra_instruction=""):
        raise _json_invalid_error()

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", always_json_invalid)
    result = await extraction(make_state())
    assert "claim_facts" not in result
    assert result["escalation_reason"] == "extraction failed"
    assert any(e.event_type == AuditEventType.EXTRACTION_RETRY for e in result["audit_log"])


@pytest.mark.asyncio
async def test_validation_failure_twice_escalates_with_extraction_failed(monkeypatch):
    async def fake_call_llm_with_backoff(narrative_text, extra_instruction=""):
        return make_good_facts(), None

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", fake_call_llm_with_backoff)

    def always_raise(*args, **kwargs):
        raise ValidationError.from_exception_data("ClaimFacts", [
            {"type": "missing", "loc": ("date_of_loss",), "input": None}
        ])

    monkeypatch.setattr(extraction_module, "ClaimFacts", always_raise)
    result = await extraction(make_state())
    assert "claim_facts" not in result
    assert result["escalation_reason"] == "extraction failed"


@pytest.mark.asyncio
async def test_backoff_retries_transient_api_error_then_succeeds(monkeypatch):
    """Exercises _call_llm_with_backoff directly against a mocked
    parse_structured to prove the actual retry loop works."""
    call_count = {"n": 0}

    async def flaky_parse(**kwargs):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise APIConnectionError(request=MagicMock())
        return make_good_facts()

    monkeypatch.setattr(extraction_module, "parse_structured", AsyncMock(side_effect=flaky_parse))
    monkeypatch.setattr(extraction_module, "BACKOFF_SECONDS", [0, 0, 0])

    parsed, failure = await extraction_module._call_llm_with_backoff("narrative")
    assert failure is None
    assert parsed is not None
    assert call_count["n"] == 2
