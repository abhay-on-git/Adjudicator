"""End-to-end MCP layer tests: real client -> real MCPServer -> real tool ->
real deterministic engine, over the actual MCP protocol (in-process
transport, see mcp_client/client.py docstring), not calling the underlying
Python functions directly. This is the P0 "one MCP tool end to end"
deliverable, covering both search_policy and compute_payout (see DESIGN.md
for why both, not one)."""

import pytest

from graph.schemas import ClaimFacts, LineItem, Peril
from mcp_client.client import compute_payout, get_clause, search_policy


@pytest.mark.asyncio
async def test_search_policy_over_real_mcp_protocol():
    clauses = await search_policy("cabinetry water damage cap", "POL-HOME-01")
    clause_ids = {c.clause_id for c in clauses}
    assert "§4.2.9" in clause_ids
    assert all(c.policy_id == "POL-HOME-01" for c in clauses)


@pytest.mark.asyncio
async def test_get_clause_exact_text_over_real_mcp_protocol():
    clause = await get_clause("POL-HOME-01", "§7.1.4")
    assert clause is not None
    assert "seepage" in clause.text.lower()


@pytest.mark.asyncio
async def test_get_clause_missing_returns_none():
    clause = await get_clause("POL-HOME-01", "§99.99")
    assert clause is None


@pytest.mark.asyncio
async def test_compute_payout_over_real_mcp_protocol():
    facts = ClaimFacts(
        policy_id="POL-HOME-01",
        policy_start_date="2024-01-01",
        date_of_loss="2024-06-01",
        perils=[Peril.WATER_DAMAGE],
        line_items=[
            LineItem(description="Cabinet", category="cabinetry", claimed_amount=71_500,
                      evidence_tags=["sudden_discharge"]),
        ],
        narrative_summary="test",
    )
    result = await compute_payout(facts, clauses=[])
    assert result.total_payable == 25_000 - 5_000  # §4.2.9 cap minus §2.3 deductible
    assert result.line_items[0].verdict.value == "reduced"
