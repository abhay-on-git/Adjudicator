"""End-to-end tests for the compiled graph (graph/build_graph.py).

The two LLM-backed nodes (extraction, explanation) are monkeypatched at
their `_call_llm_with_backoff` / `_call_llm` seams — the same seam the
node-level unit tests use — so these tests run with no network access and no
OPENAI_API_KEY, per the user's explicit instruction to keep building on
mocks until the real key is added. Everything else (router, policy_retrieval,
eligibility_evaluation, risk_anomaly, decision_composition, escalation,
ui_composition) runs for real, over the real fixtures/MCP tools.

Assertions cover the actual P0 requirement this graph exists to demonstrate:
a clean single-peril claim takes a visibly shorter path (fewer node hops,
never touches escalation) than a degraded one (missing field -> escalation).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from graph.build_graph import build_graph
from graph.nodes import explanation as explanation_module
from graph.nodes import extraction as extraction_module
from graph.schemas import ExplanationOutput, ExtractedNarrativeFacts, LineItem, Outcome, Peril
from graph.state import initial_state

CLAIMS_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "claims" / "claims.json"


def _load_claim(claim_id: str) -> dict:
    claims = json.loads(CLAIMS_FIXTURE.read_text(encoding="utf-8"))
    match = next(c for c in claims if c["claim_id"] == claim_id)
    return match


def _compiled_graph():
    return build_graph().compile(checkpointer=InMemorySaver())


@pytest.fixture
def mock_extraction_clm001(monkeypatch):
    """CLM-001: clean single-peril water-damage claim (see fixtures/claims/gold_facts.py)."""
    facts = ExtractedNarrativeFacts(
        date_of_loss="2024-07-30",
        perils=[Peril.WATER_DAMAGE],
        line_items=[
            LineItem(description="Kitchen flooring repair", category="flooring",
                      claimed_amount=8000, evidence_tags=["sudden_discharge"])
        ],
        narrative_summary="Sudden pipe burst under kitchen sink.",
    )

    async def _fake_call_with_backoff(narrative_text, extra_instruction=""):
        return facts, None

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", _fake_call_with_backoff)


@pytest.fixture
def mock_explanation_template(monkeypatch):
    """Skip the real LLM call and go straight to a canned parsed explanation
    (a valid citation set is filled in per-test since it depends on which
    clauses actually got retrieved)."""

    def _make(cited_clause_ids):
        async def _fake_call_llm(context):
            return ExplanationOutput(
                narrative="This is a test explanation.", cited_clause_ids=cited_clause_ids
            )

        monkeypatch.setattr(explanation_module, "_call_llm", _fake_call_llm)

    return _make


@pytest.mark.asyncio
async def test_clean_claim_reaches_end_without_escalation(mock_extraction_clm001, mock_explanation_template):
    mock_explanation_template([])  # avoid depending on exact retrieved clause_ids
    graph = _compiled_graph()
    raw = _load_claim("CLM-001")
    config = {"configurable": {"thread_id": "CLM-001"}}

    result = await graph.ainvoke(initial_state("CLM-001", raw), config=config)

    assert result["decision"].outcome != Outcome.ESCALATE
    assert result["ui_spec"] is not None
    node_names = [e.node for e in result["audit_log"]]
    assert "escalation" not in node_names
    # every required node ran exactly once for a clean single-peril claim
    for expected in [
        "intake_normalize", "extraction", "router", "policy_retrieval",
        "eligibility_evaluation", "risk_anomaly", "decision_composition",
        "explanation", "ui_composition",
    ]:
        assert node_names.count(expected) == 1, f"{expected} ran {node_names.count(expected)} times"


@pytest.mark.asyncio
async def test_missing_field_claim_escalates_and_pauses_for_human(monkeypatch):
    """CLM-020/021 class: router detects a missing required field and routes
    straight to escalation, WITHOUT ever calling policy_retrieval or the
    parallel eligibility/risk branches — the "fewer hops" divergence point."""
    # Extraction succeeds, but the resulting facts are missing date_of_loss,
    # which router treats as a missing required field.
    facts = ExtractedNarrativeFacts(
        date_of_loss=None,
        perils=[Peril.WATER_DAMAGE],
        line_items=[LineItem(description="Flooring repair", category="flooring", claimed_amount=5000)],
        narrative_summary="Water damage, date unclear.",
    )

    async def _fake_call_with_backoff(narrative_text, extra_instruction=""):
        return facts, None

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", _fake_call_with_backoff)

    graph = _compiled_graph()
    raw = _load_claim("CLM-001")  # envelope fields are fine; only the LLM output is mocked
    config = {"configurable": {"thread_id": "CLM-001-missing"}}

    result = await graph.ainvoke(initial_state("CLM-001-missing", raw), config=config)

    # The graph is paused INSIDE escalation's interrupt() — ainvoke returns
    # the state as of the last completed superstep, with __interrupt__ set.
    assert "__interrupt__" in result
    node_names = [e.node for e in result["audit_log"]]
    assert "policy_retrieval" not in node_names
    assert "eligibility_evaluation" not in node_names
    assert "risk_anomaly" not in node_names

    # Resume as the human, providing a decision; graph should then finish.
    resumed = await graph.ainvoke(Command(resume="manually approved by adjuster"), config=config)
    assert "__interrupt__" not in resumed
    resumed_node_names = [e.node for e in resumed["audit_log"]]
    assert "escalation" in resumed_node_names
