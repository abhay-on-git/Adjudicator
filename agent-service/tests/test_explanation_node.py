import pytest

import graph.nodes.explanation as explanation_module
from graph.nodes.explanation import explanation
from graph.schemas import (
    ClauseRef,
    Decision,
    EligibilityResult,
    ExplanationOutput,
    LineItemEligibility,
    LineItemVerdict,
    Outcome,
    RiskResult,
    RiskSeverity,
)


def make_state():
    eligibility = EligibilityResult(
        line_items=[
            LineItemEligibility(description="Cabinet", claimed_amount=71500, verdict=LineItemVerdict.REDUCED,
                                 allowed_amount=25000, governing_clause_ids=["§4.2.9"], reason="capped"),
        ],
        deductible_applied=5000, total_claimed=71500, total_payable=20000,
        clauses_used=["§2.3", "§4.2.9"], needs_escalation=False,
    )
    decision = Decision(outcome=Outcome.PARTIAL, amount=20000, confidence=0.9)
    clauses = [
        ClauseRef(policy_id="POL-HOME-01", clause_id="§4.2.9", title="Cabinetry Sub-limit", text="capped at 25000",
                   relevance_score=1.0),
        ClauseRef(policy_id="POL-HOME-01", clause_id="§2.3", title="Deductible", text="5000 deductible",
                   relevance_score=1.0),
    ]
    return {
        "decision": decision, "eligibility_result": eligibility, "risk_result": RiskResult(severity=RiskSeverity.NONE, flags=[]),
        "retrieved_clauses": clauses,
    }


@pytest.mark.asyncio
async def test_grounded_citations_pass_through(monkeypatch):
    async def fake_call_with_backoff(make_call, max_retries, backoff_seconds):
        return ExplanationOutput(narrative="Capped under §4.2.9.", cited_clause_ids=["§4.2.9"]), None

    monkeypatch.setattr(explanation_module, "call_with_backoff", fake_call_with_backoff)
    result = await explanation(make_state())
    assert result["explanation_text"] == "Capped under §4.2.9."
    assert result["groundedness_violations"] == []


@pytest.mark.asyncio
async def test_fabricated_citation_flagged_as_groundedness_violation(monkeypatch):
    async def fake_call_with_backoff(make_call, max_retries, backoff_seconds):
        return ExplanationOutput(
            narrative="Capped under §4.2.9 and also §99.99.",
            cited_clause_ids=["§4.2.9", "§99.99"],  # §99.99 was never retrieved
        ), None

    monkeypatch.setattr(explanation_module, "call_with_backoff", fake_call_with_backoff)
    result = await explanation(make_state())
    assert result["groundedness_violations"] == ["citation_not_retrieved:§99.99"]
    from graph.schemas import AuditEventType
    assert any(e.event_type == AuditEventType.GROUNDEDNESS_VIOLATION for e in result["audit_log"])


@pytest.mark.asyncio
async def test_retrieved_but_mismatched_clause_content_is_flagged(monkeypatch):
    """Known-bad seed: §2.3 is present, but it says deductible—not that
    cabinetry is capped. Presence-only checking used to pass this."""
    async def fake_call_with_backoff(make_call, max_retries, backoff_seconds):
        return ExplanationOutput(
            narrative="Built-in cabinetry is capped at 25000 under §2.3.",
            cited_clause_ids=["§2.3"],
        ), None

    monkeypatch.setattr(explanation_module, "call_with_backoff", fake_call_with_backoff)
    result = await explanation(make_state())
    assert len(result["groundedness_violations"]) == 1
    assert result["groundedness_violations"][0].startswith("content_mismatch:§2.3:")
    from graph.schemas import AuditEventType
    assert any(e.event_type == AuditEventType.GROUNDEDNESS_VIOLATION for e in result["audit_log"])


@pytest.mark.asyncio
async def test_multi_clause_list_scores_each_citation_locally(monkeypatch):
    """CLM-010 pattern: a long sentence names several irrelevant provisions.
    The local assertion 'hospitalization coverage at §4.1' is still supported
    by §4.1 and must not inherit unrelated words from neighboring citations."""
    state = make_state()
    state["retrieved_clauses"].append(
        ClauseRef(
            policy_id="POL-HOME-01",
            clause_id="§4.1",
            title="Hospitalization — Covered",
            text="In-patient hospitalization is covered.",
            relevance_score=1.0,
        )
    )

    async def fake_call_with_backoff(make_call, max_retries, backoff_seconds):
        return ExplanationOutput(
            narrative=(
                "Other provisions, including hospitalization coverage at §4.1, "
                "the deductible at §2.3, are not relevant here."
            ),
            cited_clause_ids=["§4.1"],
        ), None

    monkeypatch.setattr(explanation_module, "call_with_backoff", fake_call_with_backoff)
    result = await explanation(state)
    assert result["groundedness_violations"] == []


@pytest.mark.asyncio
async def test_leading_citation_and_currency_comma_keep_supporting_text(monkeypatch):
    async def fake_call_with_backoff(make_call, max_retries, backoff_seconds):
        return ExplanationOutput(
            narrative=(
                "Under §4.2.9, cabinetry is capped at ₹25,000. "
                "After that, the ₹5,000 deductible applies under §2.3."
            ),
            cited_clause_ids=["§4.2.9", "§2.3"],
        ), None

    monkeypatch.setattr(explanation_module, "call_with_backoff", fake_call_with_backoff)
    result = await explanation(make_state())
    assert result["groundedness_violations"] == []


@pytest.mark.asyncio
async def test_api_failure_falls_back_to_template_not_escalation(monkeypatch):
    async def fake_call_with_backoff(make_call, max_retries, backoff_seconds):
        return None, "RateLimitError: exhausted"

    monkeypatch.setattr(explanation_module, "call_with_backoff", fake_call_with_backoff)
    result = await explanation(make_state())
    assert "escalation_reason" not in result  # decision stays final; only phrasing degrades
    assert "partial" in result["explanation_text"]
    assert "20000" in result["explanation_text"]
    from graph.schemas import AuditEventType
    assert any(e.event_type == AuditEventType.LLM_UNAVAILABLE for e in result["audit_log"])
