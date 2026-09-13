"""eligibility_evaluation — apply the rules. Deterministic code, not the LLM.

Thin wrapper: calls the `compute_payout` MCP tool unconditionally with the
extracted facts and retrieved clauses, and records the result. All of the
actual coverage/exclusion/sub-limit/waiting-period logic lives in
rules/eligibility.py + rules/payout.py, reached only through the MCP tool —
this node itself contains no eligibility logic of its own to keep that
single source of truth real, not just documented.

Runs as one half of the parallel eligibility+risk fan-out (see
graph/build_graph.py) — writes only to `eligibility_result`, a key
`risk_anomaly` never touches, so the two parallel tasks never race on the
same state channel.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graph.nodes.metrics import timed_node
from graph.schemas import AuditEvent, AuditEventType
from graph.state import AdjudicationState
from mcp_client.client import compute_payout


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@timed_node("eligibility_evaluation")
async def eligibility_evaluation(state: AdjudicationState) -> dict:
    facts = state["claim_facts"]
    clauses = state.get("retrieved_clauses", [])
    assert facts is not None, "eligibility_evaluation must run after a successful extraction"

    result = await compute_payout(facts, clauses)

    detail = (
        f"total_payable={result.total_payable} total_claimed={result.total_claimed} "
        f"needs_escalation={result.needs_escalation}"
    )
    return {
        "eligibility_result": result,
        "audit_log": [
            AuditEvent(
                event_type=AuditEventType.ELIGIBILITY_COMPLETE,
                node="eligibility_evaluation",
                detail=detail,
                timestamp=_now_iso(),
            )
        ],
    }
