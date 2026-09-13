"""decision_composition — merge eligibility, risk, and confidence into one
outcome.

Pure code, no LLM. `amount` and the base `outcome` come from
`EligibilityResult` verbatim (via `derive_outcome_from_eligibility`, the same
function the ground-truth generator uses — see rules/payout.py). This node's
only two jobs beyond that: (1) compute a confidence score from risk severity
and how "clean" the eligibility verdicts were, and (2) decide whether risk or
low confidence should OVERRIDE an otherwise-clean eligibility outcome into an
escalation. Neither job ever touches `amount`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graph.nodes.metrics import timed_node
from graph.schemas import (
    AuditEvent,
    AuditEventType,
    Decision,
    EligibilityResult,
    LineItemVerdict,
    Outcome,
    RiskResult,
    RiskSeverity,
)
from graph.state import AdjudicationState
from rules.payout import derive_outcome_from_eligibility

ESCALATION_CONFIDENCE_THRESHOLD = 0.6

_RISK_PENALTY = {
    RiskSeverity.NONE: 0.0,
    RiskSeverity.LOW: 0.05,
    RiskSeverity.MEDIUM: 0.15,
    RiskSeverity.HIGH: 0.4,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_confidence(eligibility: EligibilityResult, risk: RiskResult) -> float:
    confidence = 1.0
    reduced_count = sum(1 for li in eligibility.line_items if li.verdict == LineItemVerdict.REDUCED)
    confidence -= min(0.05 * reduced_count, 0.2)
    confidence -= _RISK_PENALTY[risk.severity]
    return max(0.0, min(1.0, confidence))


@timed_node("decision_composition")
def decision_composition(state: AdjudicationState) -> dict:
    eligibility = state["eligibility_result"]
    risk = state["risk_result"]
    assert eligibility is not None and risk is not None, (
        "decision_composition must run after both parallel branches complete"
    )

    base_outcome = derive_outcome_from_eligibility(eligibility)
    confidence = compute_confidence(eligibility, risk)

    outcome = base_outcome
    escalation_reason = eligibility.escalation_reason

    if outcome != Outcome.ESCALATE and risk.severity == RiskSeverity.HIGH:
        outcome = Outcome.ESCALATE
        escalation_reason = f"Risk severity HIGH ({', '.join(risk.flags)}) overrides an otherwise-computed outcome."
    elif outcome != Outcome.ESCALATE and confidence < ESCALATION_CONFIDENCE_THRESHOLD:
        outcome = Outcome.ESCALATE
        escalation_reason = (
            f"Confidence {confidence:.2f} is below the {ESCALATION_CONFIDENCE_THRESHOLD} threshold "
            f"(risk severity={risk.severity.value})."
        )

    decision = Decision(
        outcome=outcome,
        amount=eligibility.total_payable,  # verbatim, never recomputed here
        confidence=confidence,
        escalation_reason=escalation_reason,
    )

    return {
        "decision": decision,
        "escalation_reason": escalation_reason,
        "audit_log": [
            AuditEvent(
                event_type=AuditEventType.DECISION_COMPOSED,
                node="decision_composition",
                detail=f"outcome={outcome.value} amount={decision.amount} confidence={confidence:.2f}",
                timestamp=_now_iso(),
            )
        ],
    }
