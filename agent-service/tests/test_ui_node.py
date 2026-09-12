from graph.nodes.ui import ui_composition
from graph.schemas import (
    ClauseRef,
    Decision,
    EligibilityResult,
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
                                 allowed_amount=25000, governing_clause_ids=["§4.2.1", "§4.2.9"], reason="capped"),
        ],
        deductible_applied=5000, total_claimed=71500, total_payable=20000,
        clauses_used=["§2.3", "§4.2.1", "§4.2.9"], needs_escalation=False,
    )
    decision = Decision(outcome=Outcome.PARTIAL, amount=20000, confidence=0.9)
    risk = RiskResult(severity=RiskSeverity.NONE, flags=[])
    clauses = [
        ClauseRef(policy_id="POL-HOME-01", clause_id="§4.2.1", title="Sudden Discharge", text="text1",
                   relevance_score=1.0),
        ClauseRef(policy_id="POL-HOME-01", clause_id="§4.2.9", title="Cabinetry Sub-limit", text="text2",
                   relevance_score=1.0),
        ClauseRef(policy_id="POL-HOME-01", clause_id="§7.4", title="War Exclusion", text="irrelevant",
                   relevance_score=0.1),
    ]
    return {
        "decision": decision, "eligibility_result": eligibility, "risk_result": risk,
        "retrieved_clauses": clauses, "claim_id": "CLM-012",
    }


def test_produces_five_block_types():
    result = ui_composition(make_state())
    spec = result["ui_spec"]
    block_types = [b["type"] for b in spec["blocks"]]
    assert block_types == [
        "outcome", "clause_evidence", "line_item_breakdown", "interactive_actions", "risk_signal",
    ]


def test_evidence_panel_only_includes_clauses_actually_governing_a_line_item():
    result = ui_composition(make_state())
    evidence_block = result["ui_spec"]["blocks"][1]
    clause_ids = {c["clause_id"] for c in evidence_block["clauses"]}
    assert clause_ids == {"§4.2.1", "§4.2.9"}
    assert "§7.4" not in clause_ids  # retrieved, but not cited by any line item


def test_outcome_block_matches_decision():
    result = ui_composition(make_state())
    outcome_block = result["ui_spec"]["blocks"][0]
    assert outcome_block["outcome"] == "partial"
    assert outcome_block["amount"] == 20000
    assert outcome_block["confidence"] == 0.9


def test_interactive_block_has_no_pending_action_for_non_escalated_decision():
    """P0 has no confirmation interrupt — a PARTIAL/APPROVE/DENY decision is
    already final, so there is nothing to resume."""
    result = ui_composition(make_state())
    interactive_block = result["ui_spec"]["blocks"][3]
    assert interactive_block["is_pending"] is False
    assert interactive_block["resumes_at_node"] is None
    assert interactive_block["available_actions"] == []


def test_interactive_block_is_pending_and_resumable_for_escalated_decision():
    state = make_state()
    state["decision"] = Decision(outcome=Outcome.ESCALATE, amount=0, confidence=0.3,
                                  escalation_reason="ambiguous cause")
    result = ui_composition(state)
    interactive_block = result["ui_spec"]["blocks"][3]
    assert interactive_block["is_pending"] is True
    assert interactive_block["resumes_at_node"] == "escalation"
    assert set(interactive_block["available_actions"]) == {"approve", "override", "request_documents"}
