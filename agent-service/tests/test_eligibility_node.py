import pytest

from graph.nodes.eligibility import eligibility_evaluation
from graph.schemas import AuditEventType, ClaimFacts, LineItem, Peril


@pytest.mark.asyncio
async def test_eligibility_node_calls_real_mcp_tool_end_to_end():
    facts = ClaimFacts(
        policy_id="POL-HOME-01", policy_start_date="2024-01-01", date_of_loss="2024-06-01",
        perils=[Peril.WATER_DAMAGE],
        line_items=[LineItem(description="Cabinet", category="cabinetry", claimed_amount=71500,
                              evidence_tags=["sudden_discharge"])],
        narrative_summary="test",
    )
    result = await eligibility_evaluation({"claim_facts": facts, "retrieved_clauses": []})
    eligibility = result["eligibility_result"]
    assert eligibility.total_payable == 25000 - 5000
    assert result["audit_log"][0].event_type == AuditEventType.ELIGIBILITY_COMPLETE
