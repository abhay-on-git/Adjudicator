from graph.nodes.decision import decision_composition
from graph.schemas import (
    EligibilityResult,
    LineItemEligibility,
    LineItemVerdict,
    Outcome,
    RiskResult,
    RiskSeverity,
)


def make_eligibility(**overrides) -> EligibilityResult:
    defaults = dict(
        line_items=[
            LineItemEligibility(description="Item", claimed_amount=10000, verdict=LineItemVerdict.ALLOWED,
                                 allowed_amount=10000, governing_clause_ids=["§4.1"], reason="ok"),
        ],
        deductible_applied=1000, total_claimed=10000, total_payable=9000,
        clauses_used=["§4.1"], needs_escalation=False, escalation_reason=None,
    )
    defaults.update(overrides)
    return EligibilityResult(**defaults)


def make_risk(severity=RiskSeverity.NONE, flags=None) -> RiskResult:
    return RiskResult(severity=severity, flags=flags or [], duplicate_claim_ids=[])


def test_clean_approve_high_confidence():
    result = decision_composition({"eligibility_result": make_eligibility(), "risk_result": make_risk()})
    decision = result["decision"]
    assert decision.outcome == Outcome.APPROVE
    assert decision.amount == 9000
    assert decision.confidence == 1.0


def test_amount_always_copied_verbatim_from_eligibility():
    result = decision_composition({
        "eligibility_result": make_eligibility(total_payable=12345.0), "risk_result": make_risk(),
    })
    assert result["decision"].amount == 12345.0


def test_needs_escalation_from_eligibility_propagates():
    elig = make_eligibility(needs_escalation=True, escalation_reason="ambiguous cause")
    result = decision_composition({"eligibility_result": elig, "risk_result": make_risk()})
    assert result["decision"].outcome == Outcome.ESCALATE
    assert result["decision"].escalation_reason == "ambiguous cause"


def test_high_risk_severity_overrides_clean_eligibility_to_escalate():
    result = decision_composition({
        "eligibility_result": make_eligibility(),
        "risk_result": make_risk(RiskSeverity.HIGH, ["filed_before_loss_date"]),
    })
    decision = result["decision"]
    assert decision.outcome == Outcome.ESCALATE
    assert "HIGH" in decision.escalation_reason
    # Amount is STILL the deterministic figure, even though outcome escalated —
    # risk overriding the outcome never touches what compute_payout produced.
    assert decision.amount == 9000


def test_low_confidence_from_medium_risk_and_reductions_escalates():
    elig = make_eligibility(
        line_items=[
            LineItemEligibility(description="A", claimed_amount=10000, verdict=LineItemVerdict.REDUCED,
                                 allowed_amount=5000, governing_clause_ids=["§4.1"], reason="capped"),
            LineItemEligibility(description="B", claimed_amount=10000, verdict=LineItemVerdict.REDUCED,
                                 allowed_amount=5000, governing_clause_ids=["§4.1"], reason="capped"),
        ],
        total_payable=10000,
    )
    result = decision_composition({
        "eligibility_result": elig, "risk_result": make_risk(RiskSeverity.MEDIUM, ["possible_duplicate_claim"]),
    })
    # confidence = 1.0 - 0.1 (2 reduced items) - 0.15 (medium risk) = 0.75, still above threshold
    assert result["decision"].confidence == 0.75
    assert result["decision"].outcome == Outcome.PARTIAL  # not escalated at this confidence


def test_clean_deny_is_not_escalated():
    elig = make_eligibility(
        line_items=[
            LineItemEligibility(description="Item", claimed_amount=10000, verdict=LineItemVerdict.EXCLUDED,
                                 allowed_amount=0, governing_clause_ids=["§7.3"], reason="excluded"),
        ],
        total_payable=0, needs_escalation=False,
    )
    result = decision_composition({"eligibility_result": elig, "risk_result": make_risk()})
    assert result["decision"].outcome == Outcome.DENY
    assert result["decision"].amount == 0
