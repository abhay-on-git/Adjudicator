"""escalation — pause for human review when confidence is low, risk flagged, or information missing.

Uses LangGraph's `interrupt()`. On resume, this node re-executes with the human reviewer's action:
- APPROVE: reviewer approves the claim -> outcome becomes Outcome.APPROVE (green signal),
  amount committed, decision recorded in audit log.
- OVERRIDE / REJECT: reviewer denies or overrides the claim -> outcome becomes Outcome.DENY
  (red signal) or custom approved amount.
- REQUEST_DOCUMENTS: reviewer requests missing documents -> outcome records documents requested,
  and allows document submission.
- SUBMIT_DOCUMENTS: claimant submits requested documents -> ready for final approval.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from langgraph.types import interrupt

from graph.interrupts import KIND_ESCALATION
from graph.nodes.metrics import timed_node
from graph.schemas import AuditEvent, AuditEventType, Decision, InteractiveAction, Outcome
from graph.state import AdjudicationState


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _refresh_or_synthesize_ui_spec(
    ui_spec: dict | None,
    claim_id: str,
    decision: Decision,
    is_pending: bool = False,
    available_actions: list[str] | None = None,
    requested_docs: list[str] | None = None,
) -> dict:
    if not ui_spec:
        blocks = [
            {
                "type": "outcome",
                "outcome": decision.outcome.value,
                "amount": decision.amount,
                "confidence": decision.confidence,
                "escalation_reason": decision.escalation_reason,
                "narrative": (
                    f"Claim reviewed by human adjuster: {decision.outcome.value.upper()}."
                    if decision.outcome in (Outcome.APPROVE, Outcome.DENY)
                    else (decision.escalation_reason or "Escalated for review.")
                ),
            },
            {
                "type": "interactive_actions",
                "claim_id": claim_id,
                "thread_id": claim_id,
                "available_actions": available_actions or [],
                "resumes_at_node": "escalation" if is_pending else None,
                "is_pending": is_pending,
                "requested_documents": requested_docs or [],
            },
        ]
        return {"blocks": blocks}

    spec = deepcopy(ui_spec)
    has_outcome = False
    for block in spec.get("blocks", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "outcome":
            has_outcome = True
            block["outcome"] = decision.outcome.value
            block["amount"] = decision.amount
            block["confidence"] = decision.confidence
            block["escalation_reason"] = decision.escalation_reason
            if decision.outcome == Outcome.APPROVE:
                block["narrative"] = "Claim reviewed and approved by human adjuster."
            elif decision.outcome == Outcome.DENY:
                block["narrative"] = f"Claim rejected by human reviewer. Reason: {decision.escalation_reason}"
        elif block.get("type") == "interactive_actions":
            block["is_pending"] = is_pending
            block["available_actions"] = available_actions or []
            block["resumes_at_node"] = "escalation" if is_pending else None
            if requested_docs:
                block["requested_documents"] = requested_docs

    if not has_outcome:
        spec["blocks"].insert(
            0,
            {
                "type": "outcome",
                "outcome": decision.outcome.value,
                "amount": decision.amount,
                "confidence": decision.confidence,
                "escalation_reason": decision.escalation_reason,
                "narrative": f"Claim reviewed: {decision.outcome.value.upper()}.",
            },
        )

    return spec


@timed_node("escalation")
def escalation(state: AdjudicationState) -> dict:
    reason = state.get("escalation_reason") or "Escalated for manual review."
    claim_id = state.get("claim_id", "")
    current_decision = state.get("decision")

    ui_spec_state = state.get("ui_spec")
    actions_block = next(
        (
            b
            for b in (ui_spec_state.get("blocks", []) if isinstance(ui_spec_state, dict) else [])
            if isinstance(b, dict) and b.get("type") == "interactive_actions"
        ),
        None,
    )
    available_actions = (
        actions_block.get("available_actions")
        if actions_block and actions_block.get("available_actions")
        else ["approve", "override", "request_documents"]
    )
    pending_docs = actions_block.get("requested_documents") if actions_block else []

    # Pauses graph execution with interrupt()
    human_response = interrupt(
        {
            "kind": KIND_ESCALATION,
            "reason": reason,
            "claim_id": claim_id,
            "decision_so_far": current_decision,
            "available_actions": available_actions,
            "requested_documents": pending_docs,
        }
    )

    # On resume: parse human response
    action = "approve"
    action_reason = ""
    override_outcome = "approve"
    proposed_amount: float | None = None
    requested_docs: list[str] = []

    if isinstance(human_response, dict):
        raw_action = str(human_response.get("action", "")).lower()
        action_reason = str(human_response.get("reason", "")).strip()
        override_outcome = str(human_response.get("override_outcome", "")).lower()
        raw_amt = human_response.get("proposed_amount")
        if raw_amt is not None:
            try:
                proposed_amount = float(raw_amt)
            except (ValueError, TypeError):
                proposed_amount = None
        raw_docs = human_response.get("requested_documents")
        if isinstance(raw_docs, list):
            requested_docs = [str(d) for d in raw_docs if d]

        if "deny" in raw_action or "reject" in raw_action or override_outcome in ("deny", "reject"):
            action = "deny"
        elif "request_document" in raw_action:
            action = "request_documents"
        elif "submit_document" in raw_action:
            action = "submit_documents"
        elif "override" in raw_action:
            action = "override"
        else:
            action = "approve"
    elif isinstance(human_response, str):
        text = human_response.strip().lower()
        if "deny" in text or "reject" in text:
            action = "deny"
            action_reason = human_response
        elif "request_doc" in text:
            action = "request_documents"
            action_reason = human_response
        elif "submit_doc" in text:
            action = "submit_documents"
            action_reason = human_response
        elif "override" in text:
            action = "override"
            action_reason = human_response
        else:
            action = "approve"
            action_reason = human_response

    audit_events: list[AuditEvent] = []
    decision_committed = True

    if action == "approve":
        # Review successful -> approve claim (Green signal)
        if current_decision and current_decision.amount > 0:
            approved_amount = current_decision.amount
        else:
            facts = state.get("claim_facts")
            approved_amount = (
                sum(li.claimed_amount for li in facts.line_items)
                if facts and facts.line_items
                else 0.0
            )
        new_decision = Decision(
            outcome=Outcome.APPROVE,
            amount=approved_amount,
            confidence=1.0,
            escalation_reason=None,
        )
        audit_events.append(
            AuditEvent(
                event_type=AuditEventType.DECISION_CONFIRMED,
                node="escalation",
                detail=f"Human review successful: claim approved for {approved_amount}.",
                timestamp=_now_iso(),
            )
        )
        ui_spec = _refresh_or_synthesize_ui_spec(
            state.get("ui_spec"), claim_id, new_decision, is_pending=False
        )

    elif action == "deny":
        # Review rejected / denied -> deny claim (Red signal)
        deny_reason = action_reason or "Claim denied upon human review."
        new_decision = Decision(
            outcome=Outcome.DENY,
            amount=0.0,
            confidence=1.0,
            escalation_reason=deny_reason,
        )
        audit_events.append(
            AuditEvent(
                event_type=AuditEventType.DECISION_OVERRIDDEN,
                node="escalation",
                detail=f"Human review rejected claim: {deny_reason}",
                timestamp=_now_iso(),
                original_outcome=current_decision.outcome if current_decision else None,
                original_amount=current_decision.amount if current_decision else None,
                override_reason=deny_reason,
                override_proposed_amount=0,
            )
        )
        ui_spec = _refresh_or_synthesize_ui_spec(
            state.get("ui_spec"), claim_id, new_decision, is_pending=False
        )

    elif action == "override":
        if override_outcome in ("deny", "reject") or (proposed_amount == 0 and "reject" in action_reason.lower()):
            new_decision = Decision(
                outcome=Outcome.DENY,
                amount=0.0,
                confidence=1.0,
                escalation_reason=action_reason or "Claim denied by reviewer override.",
            )
        else:
            amt = (
                proposed_amount
                if proposed_amount is not None
                else (current_decision.amount if current_decision else 0.0)
            )
            new_decision = Decision(
                outcome=Outcome.APPROVE,
                amount=amt,
                confidence=1.0,
                escalation_reason=action_reason,
            )
        audit_events.append(
            AuditEvent(
                event_type=AuditEventType.DECISION_OVERRIDDEN,
                node="escalation",
                detail=f"Human review overridden: {new_decision.outcome.value} of {new_decision.amount}. Reason: {action_reason}",
                timestamp=_now_iso(),
                original_outcome=current_decision.outcome if current_decision else None,
                original_amount=current_decision.amount if current_decision else None,
                override_reason=action_reason,
                override_proposed_amount=int(new_decision.amount),
            )
        )
        ui_spec = _refresh_or_synthesize_ui_spec(
            state.get("ui_spec"), claim_id, new_decision, is_pending=False
        )

    elif action == "request_documents":
        doc_summary = ", ".join(requested_docs) if requested_docs else (action_reason or "Additional documentation")
        new_decision = Decision(
            outcome=Outcome.ESCALATE,
            amount=current_decision.amount if current_decision else 0.0,
            confidence=current_decision.confidence if current_decision else 0.5,
            escalation_reason=f"Documents requested from claimant: {doc_summary}",
        )
        audit_events.append(
            AuditEvent(
                event_type=AuditEventType.DECISION_DOCUMENTS_REQUESTED,
                node="escalation",
                detail=f"Reviewer requested documents: {doc_summary}",
                timestamp=_now_iso(),
            )
        )
        ui_spec = _refresh_or_synthesize_ui_spec(
            state.get("ui_spec"),
            claim_id,
            new_decision,
            is_pending=True,
            available_actions=["submit_documents", "approve", "override"],
            requested_docs=requested_docs or [doc_summary],
        )
        decision_committed = False

    elif action == "submit_documents":
        # Claimant submitted requested docs -> ready for approval
        sub_note = action_reason or "Requested documents submitted by claimant."
        if current_decision and current_decision.amount > 0:
            approved_amount = current_decision.amount
        else:
            facts = state.get("claim_facts")
            approved_amount = (
                sum(li.claimed_amount for li in facts.line_items)
                if facts and facts.line_items
                else 0.0
            )
        new_decision = Decision(
            outcome=Outcome.APPROVE,
            amount=approved_amount,
            confidence=1.0,
            escalation_reason=f"Documents verified: {sub_note}",
        )
        audit_events.append(
            AuditEvent(
                event_type=AuditEventType.DECISION_CONFIRMED,
                node="escalation",
                detail=f"Documents submitted and verified: claim approved for {approved_amount}.",
                timestamp=_now_iso(),
            )
        )
        ui_spec = _refresh_or_synthesize_ui_spec(
            state.get("ui_spec"), claim_id, new_decision, is_pending=False
        )
    else:
        new_decision = current_decision or Decision(
            outcome=Outcome.APPROVE, amount=0.0, confidence=1.0
        )
        ui_spec = _refresh_or_synthesize_ui_spec(
            state.get("ui_spec"), claim_id, new_decision, is_pending=False
        )

    return {
        "decision": new_decision,
        "decision_committed": decision_committed,
        "escalation_reason": new_decision.escalation_reason,
        "ui_spec": ui_spec,
        "audit_log": audit_events,
    }
