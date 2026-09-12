import pytest

from graph.nodes.retrieval import policy_retrieval
from graph.schemas import AuditEventType, ClaimFacts, LineItem, Peril


@pytest.mark.asyncio
async def test_retrieves_relevant_clauses_for_cabinetry_claim():
    facts = ClaimFacts(
        policy_id="POL-HOME-01", policy_start_date="2024-01-01", date_of_loss="2024-06-01",
        perils=[Peril.WATER_DAMAGE],
        line_items=[LineItem(description="Cabinet repair", category="cabinetry", claimed_amount=20000,
                              evidence_tags=["sudden_discharge"])],
        narrative_summary="test",
    )
    result = await policy_retrieval({"claim_facts": facts})
    clause_ids = {c.clause_id for c in result["retrieved_clauses"]}
    assert "§4.2.9" in clause_ids
    assert "§2.3" in clause_ids  # deductible query always included
    assert result["audit_log"][0].event_type == AuditEventType.RETRIEVAL_COMPLETE


@pytest.mark.asyncio
async def test_uncovered_peril_flags_empty_coverage_despite_deductible_still_matching():
    """Degradation mode 1 fixture (CLM-022): flood damage has no clause in
    POL-HOME-01 at all (only fire/water-plumbing-discharge/theft are
    covered). The deductible clause (§2.3) is administrative and always
    matches — it must NOT be counted as a coverage hit, or this degradation
    path would never trigger for ANY claim."""
    facts = ClaimFacts(
        policy_id="POL-HOME-01", policy_start_date="2024-01-01", date_of_loss="2024-08-08",
        perils=[Peril.OTHER],
        line_items=[LineItem(description="Flood damage from river overflow", category="flood_damage",
                              claimed_amount=90000)],
        narrative_summary="test",
    )
    result = await policy_retrieval({"claim_facts": facts})
    assert result["retrieval_had_coverage_hit"] is False
    assert result["audit_log"][0].event_type == AuditEventType.RETRIEVAL_EMPTY
    # §2.3 (deductible) may still be present in retrieved_clauses for evidence
    # purposes, but the coverage-hit signal is what the graph actually branches on.
