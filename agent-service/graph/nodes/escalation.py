"""escalation — pause for a human when confidence is low or information is
missing.

Uses LangGraph's `interrupt()`, which requires a checkpointer (build_graph.py
compiles with one) and re-executes this node from the top on resume — see
that function's own docstring for why the resume value is read at the very
start, before anything else runs.

This node handles ONE of the two interrupt uses the spec asks for
(escalation for low confidence / missing info / degradation). The second use
(confirmation before any decision is committed) is P1 — see DESIGN.md open
question #2 — and will be a separate node (`commit_decision`) added later,
not folded into this one, so each interrupt's purpose stays legible in the
graph shape.
"""

from __future__ import annotations

from datetime import datetime, timezone

from langgraph.types import interrupt

from graph.schemas import AuditEvent, AuditEventType
from graph.state import AdjudicationState


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def escalation(state: AdjudicationState) -> dict:
    reason = state.get("escalation_reason") or "Escalated for manual review."

    # First call: raises GraphInterrupt, pausing the graph here. On resume
    # (Command(resume=...)), this node re-executes from the top and `interrupt()`
    # returns the human's response instead of raising.
    human_response = interrupt(
        {
            "reason": reason,
            "claim_id": state.get("claim_id"),
            "decision_so_far": state.get("decision"),
        }
    )

    return {
        "audit_log": [
            AuditEvent(
                event_type=AuditEventType.ESCALATION_RESOLVED,
                node="escalation",
                detail=f"Escalation resolved by human: {human_response!r}",
                timestamp=_now_iso(),
            )
        ],
    }
