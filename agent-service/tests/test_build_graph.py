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
from graph.nodes import evidence as evidence_module
from graph.nodes import explanation as explanation_module
from graph.nodes import extraction as extraction_module
from graph.nodes import retrieval as retrieval_module
from graph.schemas import (
    AuditEventType,
    ClauseRef,
    ExplanationOutput,
    ExtractedNarrativeFacts,
    LineItem,
    Outcome,
    Peril,
)
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
async def test_clean_claim_pauses_for_confirmation_then_commits(
    mock_extraction_clm001, mock_explanation_template
):
    mock_explanation_template([])
    graph = _compiled_graph()
    raw = _load_claim("CLM-001")
    config = {"configurable": {"thread_id": "CLM-001"}}

    paused = await graph.ainvoke(initial_state("CLM-001", raw), config=config)

    assert "__interrupt__" in paused
    assert paused["decision"].outcome != Outcome.ESCALATE
    assert paused["decision_committed"] is False
    node_names = [e.node for e in paused["audit_log"]]
    assert "escalation" not in node_names
    assert "commit_decision" not in node_names  # interrupt fires before the node returns
    for expected in [
        "intake_normalize", "extraction", "router", "policy_retrieval",
        "eligibility_evaluation", "risk_anomaly", "decision_composition",
        "evidence_reconciliation", "explanation", "ui_composition",
    ]:
        assert node_names.count(expected) == 1, f"{expected} ran {node_names.count(expected)} times"

    interactive = next(b for b in paused["ui_spec"]["blocks"] if b["type"] == "interactive_actions")
    assert interactive["is_pending"] is True
    assert interactive["resumes_at_node"] == "commit_decision"

    resumed = await graph.ainvoke(Command(resume="approve"), config=config)
    assert "__interrupt__" not in resumed
    assert resumed["decision_committed"] is True
    assert resumed["decision"].amount == paused["decision"].amount
    resumed_nodes = [e.node for e in resumed["audit_log"]]
    assert resumed_nodes.count("commit_decision") == 1
    assert "escalation" not in resumed_nodes


@pytest.mark.asyncio
async def test_missing_governing_clause_is_exact_fetched_before_explanation(monkeypatch):
    facts = ExtractedNarrativeFacts(
        date_of_loss="2024-07-05",
        perils=[Peril.HOSPITALIZATION],
        line_items=[
            LineItem(
                description="Diagnostic tests",
                category="diagnostics",
                claimed_amount=28000,
            )
        ],
        narrative_summary="Hospitalized for diagnostic tests.",
    )

    async def fake_extraction(narrative_text, extra_instruction=""):
        return facts, None

    # Simulate a small top_k result that omits eligibility's §4.4.
    async def fake_search_policy(query, policy_id, top_k=5):
        return [
            ClauseRef(
                policy_id=policy_id,
                clause_id="§4.1",
                title="Hospitalization — Covered",
                text="In-patient hospitalization is covered.",
                relevance_score=1.0,
            ),
            ClauseRef(
                policy_id=policy_id,
                clause_id="§2.1",
                title="Standard Deductible",
                text="A ₹2,000 deductible applies.",
                relevance_score=1.0,
            ),
        ]

    exact_calls = []

    async def fake_get_clause(policy_id, clause_id):
        exact_calls.append((policy_id, clause_id))
        return ClauseRef(
            policy_id=policy_id,
            clause_id=clause_id,
            title="Treatment Category Sub-limits",
            text="Diagnostic tests and scans are capped at ₹20,000 per claim.",
            relevance_score=1.0,
        )

    explanation_contexts = []

    async def fake_explanation(context):
        explanation_contexts.append(context)
        return ExplanationOutput(
            narrative="Diagnostic tests are capped at ₹20,000 under §4.4.",
            cited_clause_ids=["§4.4"],
        )

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", fake_extraction)
    monkeypatch.setattr(retrieval_module, "search_policy", fake_search_policy)
    monkeypatch.setattr(evidence_module, "get_clause", fake_get_clause)
    monkeypatch.setattr(explanation_module, "_call_llm", fake_explanation)

    raw = {
        **_load_claim("CLM-018"),
        "claim_id": "CLM-018-RECONCILE",
    }
    config = {"configurable": {"thread_id": "CLM-018-RECONCILE"}}
    result = await _compiled_graph().ainvoke(
        initial_state("CLM-018-RECONCILE", raw),
        config=config,
    )

    assert ("POL-HEALTH-01", "§4.4") in exact_calls
    assert explanation_contexts and "§4.4 (Treatment Category Sub-limits)" in explanation_contexts[0]
    assert "citation_not_retrieved:§4.4" not in result["groundedness_violations"]
    assert "evidence_reconciliation" in [event.node for event in result["audit_log"]]


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


@pytest.mark.asyncio
async def test_over_budget_multi_peril_claim_drops_context_and_still_decides(
    monkeypatch, mock_explanation_template
):
    """Engineered many-line-item / multi-peril claim under a tiny budget:
    drop policy must fire, and the graph must still produce a Decision
    (not crash, not silently truncate without an audit event)."""
    import graph.context_budget as context_budget

    monkeypatch.setattr(context_budget, "CLAIM_CONTEXT_TOKEN_BUDGET", 120)
    facts = ExtractedNarrativeFacts(
        date_of_loss="2024-07-30",
        perils=[Peril.WATER_DAMAGE, Peril.FIRE, Peril.THEFT],
        line_items=[
            LineItem(description="Kitchen flooring repair", category="flooring",
                      claimed_amount=8000, evidence_tags=["sudden_discharge"]),
            LineItem(description="Cabinet fire scorch", category="cabinetry", claimed_amount=15000),
            LineItem(description="Stolen jewelry", category="jewelry", claimed_amount=20000,
                      evidence_tags=["forcible_entry_evidence"]),
        ],
        narrative_summary="Pipe burst, a stove fire, and a break-in the same week.",
    )

    async def _fake_call_with_backoff(narrative_text, extra_instruction=""):
        return facts, None

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", _fake_call_with_backoff)
    mock_explanation_template([])

    graph = _compiled_graph()
    raw = _load_claim("CLM-001")
    raw = {
        **raw,
        "claim_id": "CLM-BUDGET-001",
        "narrative_text": raw["narrative_text"] + " Also a kitchen fire scorched cabinets and jewelry was stolen.",
    }
    config = {"configurable": {"thread_id": "CLM-BUDGET-001"}}
    paused = await graph.ainvoke(initial_state("CLM-BUDGET-001", raw), config=config)

    assert paused.get("decision") is not None
    drop_events = [
        e for e in paused["audit_log"] if e.event_type == AuditEventType.CONTEXT_BUDGET_DROP
    ]
    assert drop_events, "expected an explicit context_budget_drop audit entry"
    assert paused.get("context_drop") is not None
    assert paused["context_drop"]["dropped"]
    assert paused["context_drop"]["tokens_before"] > paused["context_drop"]["tokens_after"]

    resumed = await graph.ainvoke(Command(resume="approve"), config=config)
    assert resumed["decision"] is not None
    assert resumed["decision_committed"] is True
