import pytest

import graph.nodes.evidence as evidence_module
from graph.nodes.evidence import evidence_reconciliation
from graph.schemas import (
    AuditEventType,
    ClaimFacts,
    ClauseRef,
    EligibilityResult,
    LineItem,
    LineItemEligibility,
    LineItemVerdict,
    Peril,
)


def _state():
    facts = ClaimFacts(
        policy_id="POL-HEALTH-01",
        policy_start_date="2024-06-01",
        date_of_loss="2024-07-05",
        perils=[Peril.HOSPITALIZATION],
        line_items=[
            LineItem(description="Diagnostic tests", category="diagnostics", claimed_amount=28000)
        ],
        narrative_summary="Diagnostic hospitalization.",
    )
    eligibility = EligibilityResult(
        line_items=[
            LineItemEligibility(
                description="Diagnostic tests",
                claimed_amount=28000,
                verdict=LineItemVerdict.REDUCED,
                allowed_amount=20000,
                governing_clause_ids=["§4.1", "§4.4"],
                reason="Diagnostics capped under §4.4.",
            )
        ],
        deductible_applied=2000,
        total_claimed=28000,
        total_payable=18000,
        clauses_used=["§2.1", "§4.1", "§4.4"],
    )
    return {
        "claim_facts": facts,
        "eligibility_result": eligibility,
        # Deliberately simulate ranked retrieval missing §4.4.
        "retrieved_clauses": [
            ClauseRef(
                policy_id="POL-HEALTH-01",
                clause_id="§2.1",
                title="Deductible",
                text="₹2,000 deductible.",
                relevance_score=1.0,
            ),
            ClauseRef(
                policy_id="POL-HEALTH-01",
                clause_id="§4.1",
                title="Hospitalization",
                text="In-patient hospitalization is covered.",
                relevance_score=1.0,
            ),
        ],
    }


@pytest.mark.asyncio
async def test_fetches_clause_used_by_eligibility_but_missing_from_ranked_retrieval(monkeypatch):
    calls = []

    async def fake_get_clause(policy_id, clause_id):
        calls.append((policy_id, clause_id))
        return ClauseRef(
            policy_id=policy_id,
            clause_id=clause_id,
            title="Treatment Category Sub-limits",
            text="Diagnostic tests and scans are capped at ₹20,000 per claim.",
            relevance_score=1.0,
        )

    monkeypatch.setattr(evidence_module, "get_clause", fake_get_clause)
    result = await evidence_reconciliation(_state())

    assert calls == [("POL-HEALTH-01", "§4.4")]
    assert [clause.clause_id for clause in result["retrieved_clauses"]] == ["§4.4"]
    assert result["evidence_reconciliation_failed"] is False
    assert result["audit_log"][0].event_type == AuditEventType.EVIDENCE_RECONCILED


@pytest.mark.asyncio
async def test_missing_exact_clause_blocks_explanation_path(monkeypatch):
    async def fake_get_clause(policy_id, clause_id):
        return None

    monkeypatch.setattr(evidence_module, "get_clause", fake_get_clause)
    result = await evidence_reconciliation(_state())

    assert result["evidence_reconciliation_failed"] is True
    assert result["escalation_reason"] == "decision-driving policy evidence unavailable"
    assert (
        result["audit_log"][0].event_type
        == AuditEventType.EVIDENCE_RECONCILIATION_FAILED
    )
