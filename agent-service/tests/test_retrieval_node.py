import pytest

from graph.nodes.retrieval import PERIL_SEARCH_QUERIES, _coverage_queries, policy_retrieval
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


@pytest.mark.asyncio
async def test_motor_accident_retrieves_even_with_weak_line_item_category():
    """Held-out failure: peril enum `motor_accident` is not in POL-MOTOR-01
    wording, and a live LLM often emits categories like 'door_repair' instead
    of fixture-friendly 'accident_damage'. Coverage must still hit §3.1."""
    facts = ClaimFacts(
        policy_id="POL-MOTOR-01",
        policy_start_date="2023-09-01",
        date_of_loss="2024-07-15",
        perils=[Peril.MOTOR_ACCIDENT],
        line_items=[
            LineItem(
                description="Passenger door panel and paint",
                category="door_repair",
                claimed_amount=45000,
            )
        ],
        narrative_summary="Parked scrape by a two-wheeler.",
    )
    result = await policy_retrieval({"claim_facts": facts})
    assert result["retrieval_had_coverage_hit"] is True
    assert result.get("escalation_reason") is None
    clause_ids = {c.clause_id for c in result["retrieved_clauses"]}
    assert "§3.1" in clause_ids
    assert result["audit_log"][0].event_type == AuditEventType.RETRIEVAL_COMPLETE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy_id,peril,expected_clause",
    [
        ("POL-HOME-01", Peril.FIRE, "§4.1"),
        ("POL-HOME-01", Peril.WATER_DAMAGE, "§4.2.1"),
        ("POL-HOME-01", Peril.THEFT, "§5.1"),
        ("POL-HEALTH-01", Peril.HOSPITALIZATION, "§4.1"),
        ("POL-HEALTH-01", Peril.ACCIDENTAL_INJURY, "§3.1.5"),
        ("POL-MOTOR-01", Peril.MOTOR_ACCIDENT, "§3.1"),
        ("POL-MOTOR-01", Peril.MOTOR_THEFT, "§3.2"),
        ("POL-TRAVEL-01", Peril.TRIP_CANCELLATION, "§4.1"),
        ("POL-TRAVEL-01", Peril.BAGGAGE_LOSS, "§4.2"),
        ("POL-TRAVEL-01", Peril.MEDICAL_ABROAD, "§4.3"),
    ],
)
async def test_each_mapped_peril_finds_governing_clause_without_line_items(
    policy_id, peril, expected_clause
):
    """Peril→policy vocabulary mapping must be sufficient on its own — do not
    depend on fixture-tuned line-item categories for a coverage hit."""
    facts = ClaimFacts(
        policy_id=policy_id,
        policy_start_date="2023-01-01",
        date_of_loss="2024-06-01",
        perils=[peril],
        line_items=[],
        narrative_summary="peril-only retrieval probe",
    )
    result = await policy_retrieval({"claim_facts": facts})
    assert result["retrieval_had_coverage_hit"] is True, (
        f"{peril.value} on {policy_id} should hit coverage via {PERIL_SEARCH_QUERIES[peril]!r}"
    )
    clause_ids = {c.clause_id for c in result["retrieved_clauses"]}
    assert expected_clause in clause_ids


def test_coverage_queries_use_mapped_vocabulary_not_raw_enum_slug():
    facts = ClaimFacts(
        policy_id="POL-MOTOR-01",
        policy_start_date="2023-01-01",
        date_of_loss="2024-06-01",
        perils=[Peril.MOTOR_ACCIDENT],
        line_items=[
            LineItem(description="Door repair", category="door_repair", claimed_amount=1000)
        ],
        narrative_summary="probe",
    )
    queries = _coverage_queries(facts)
    assert "accidental damage collision" in queries
    assert "motor accident" not in queries
    assert "door repair Door repair" in queries


def test_every_non_other_peril_has_a_search_mapping():
    missing = [p for p in Peril if p is not Peril.OTHER and p not in PERIL_SEARCH_QUERIES]
    assert missing == [], f"Unmapped perils would regress empty-retrieval: {missing}"
