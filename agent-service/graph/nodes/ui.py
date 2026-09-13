"""ui_composition — emit the structured decision spec React renders from.

Pure code, no LLM. Templates the already-computed Decision/EligibilityResult
/RiskResult/retrieved_clauses into typed UI blocks (graph/schemas.py), then
dumps them to plain dicts. Because this node is deterministic Python, it can
only ever emit the block types defined in schemas.py by construction — the
frontend's "unrecognized block type" fallback exists for contract drift
across the service boundary, not because this node might hallucinate one.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graph.nodes.metrics import timed_node
from graph.schemas import (
    AuditEvent,
    AuditEventType,
    ClauseEvidenceBlock,
    DecisionUISpec,
    EvidenceClause,
    InteractiveAction,
    InteractiveActionsBlock,
    LineItemBreakdownBlock,
    LineItemBreakdownRow,
    OutcomeBlock,
    RiskSignalBlock,
)
from graph.state import AdjudicationState


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@timed_node("ui_composition")
def ui_composition(state: AdjudicationState) -> dict:
    decision = state["decision"]
    eligibility = state["eligibility_result"]
    risk = state["risk_result"]
    retrieved = state.get("retrieved_clauses", [])
    claim_id = state["claim_id"]
    assert decision is not None and eligibility is not None and risk is not None

    cited_ids = {cid for li in eligibility.line_items for cid in li.governing_clause_ids}
    evidence_clauses = [
        EvidenceClause(clause_id=c.clause_id, policy_id=c.policy_id, title=c.title, text=c.text)
        for c in retrieved
        if c.clause_id in cited_ids
    ]

    blocks = [
        OutcomeBlock(
            outcome=decision.outcome, amount=decision.amount, confidence=decision.confidence,
            escalation_reason=decision.escalation_reason, narrative=state.get("explanation_text", ""),
        ).model_dump(mode="json"),
        ClauseEvidenceBlock(clauses=evidence_clauses).model_dump(mode="json"),
        LineItemBreakdownBlock(
            rows=[
                LineItemBreakdownRow(
                    description=li.description, claimed_amount=li.claimed_amount,
                    allowed_amount=li.allowed_amount, verdict=li.verdict,
                    governing_clause_ids=li.governing_clause_ids, reason=li.reason,
                )
                for li in eligibility.line_items
            ],
            deductible_applied=eligibility.deductible_applied,
        ).model_dump(mode="json"),
        (
            InteractiveActionsBlock(
                claim_id=claim_id, thread_id=claim_id,
                available_actions=[
                    InteractiveAction.APPROVE, InteractiveAction.OVERRIDE, InteractiveAction.REQUEST_DOCUMENTS,
                ],
                resumes_at_node="escalation",
                is_pending=True,
            )
            if decision.outcome.value == "escalate"
            else InteractiveActionsBlock(
                claim_id=claim_id, thread_id=claim_id,
                available_actions=[
                    InteractiveAction.APPROVE, InteractiveAction.OVERRIDE, InteractiveAction.REQUEST_DOCUMENTS,
                ],
                resumes_at_node="commit_decision",
                is_pending=True,
            )
        ).model_dump(mode="json"),
        RiskSignalBlock(
            severity=risk.severity,
            flags=risk.flags,
            duplicate_claim_ids=risk.duplicate_claim_ids,
        ).model_dump(mode="json"),
    ]

    ui_spec = DecisionUISpec(claim_id=claim_id, blocks=blocks)

    return {
        "ui_spec": ui_spec.model_dump(mode="json"),
        "audit_log": [
            AuditEvent(
                event_type=AuditEventType.UI_COMPOSED,
                node="ui_composition",
                detail=f"{len(blocks)} blocks composed for claim {claim_id}.",
                timestamp=_now_iso(),
            )
        ],
    }
