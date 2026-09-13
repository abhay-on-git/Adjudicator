"""evidence_reconciliation — guarantee decision-driving clauses are citable.

Keyword retrieval is for discovery and may omit a low-ranked clause at
``top_k``. Eligibility, however, uses a deterministic rule table and records
the exact clause IDs that drove every verdict and adjustment. This node runs
after eligibility's fan-in and before explanation. For each missing
``EligibilityResult.clauses_used`` ID it calls the exact-lookup ``get_clause``
MCP tool and returns only those missing clauses; the state's
``merge_clauses_by_id`` reducer unions them into ``retrieved_clauses``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graph.nodes.metrics import timed_node
from graph.schemas import AuditEvent, AuditEventType
from graph.state import AdjudicationState
from mcp_client.client import get_clause


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@timed_node("evidence_reconciliation")
async def evidence_reconciliation(state: AdjudicationState) -> dict:
    eligibility = state["eligibility_result"]
    facts = state["claim_facts"]
    assert eligibility is not None, "evidence_reconciliation runs after eligibility"
    assert facts is not None, "evidence_reconciliation requires claim facts"

    retrieved_ids = {clause.clause_id for clause in state.get("retrieved_clauses", [])}
    required_ids = list(dict.fromkeys(eligibility.clauses_used))
    missing_ids = [clause_id for clause_id in required_ids if clause_id not in retrieved_ids]

    fetched = []
    unresolved = []
    for clause_id in missing_ids:
        clause = await get_clause(facts.policy_id, clause_id)
        if clause is None:
            unresolved.append(clause_id)
        else:
            fetched.append(clause)

    if unresolved:
        detail = (
            f"Could not resolve decision-driving clause(s) {unresolved} for "
            f"{facts.policy_id}; explanation blocked rather than citing absent evidence."
        )
        return {
            "retrieved_clauses": fetched,
            "evidence_reconciliation_failed": True,
            "escalation_reason": "decision-driving policy evidence unavailable",
            "audit_log": [
                AuditEvent(
                    event_type=AuditEventType.EVIDENCE_RECONCILIATION_FAILED,
                    node="evidence_reconciliation",
                    detail=detail,
                    timestamp=_now_iso(),
                )
            ],
        }

    detail = (
        f"Verified {len(required_ids)} decision-driving clause(s); exact-fetched "
        f"{[clause.clause_id for clause in fetched]} missing from ranked retrieval."
    )
    return {
        "retrieved_clauses": fetched,
        "evidence_reconciliation_failed": False,
        "audit_log": [
            AuditEvent(
                event_type=AuditEventType.EVIDENCE_RECONCILED,
                node="evidence_reconciliation",
                detail=detail,
                timestamp=_now_iso(),
            )
        ],
    }
