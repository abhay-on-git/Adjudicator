"""commit_decision — confirmation interrupt before a computed decision is final.

Separate from `escalation`: that node pauses because the graph *cannot* decide.
This node pauses because a human must confirm (or override / request documents)
before an otherwise-complete approve/deny/partial is treated as committed.

Uses LangGraph `interrupt()`. On resume the node re-executes from the top and
`interrupt()` returns the human's action. The LLM never runs here; amounts are
not recomputed. Override / request_documents may only downgrade the already-
computed Decision to escalate, never invent a payout.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Never

from langgraph.types import interrupt

from graph.interrupts import KIND_CONFIRMATION
from graph.nodes.metrics import timed_node
from graph.schemas import AuditEvent, AuditEventType, Decision, InteractiveAction, Outcome
from graph.state import AdjudicationState
from mcp_client.client import flag_for_review


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class HumanAction:
    action: InteractiveAction
    reason: str | None = None
    proposed_amount: int | None = None


def _normalize_action(human_response: object) -> HumanAction:
    raw: object = human_response
    reason: str | None = None
    proposed_amount: int | None = None
    if isinstance(human_response, dict):
        raw = human_response.get("action") or human_response.get("human_response") or human_response
        raw_reason = human_response.get("reason")
        if isinstance(raw_reason, str) and raw_reason.strip():
            reason = raw_reason.strip()
        raw_amount = human_response.get("proposed_amount")
        if isinstance(raw_amount, int) and not isinstance(raw_amount, bool) and raw_amount >= 0:
            proposed_amount = raw_amount
    text = getattr(raw, "value", raw)
    try:
        action = InteractiveAction(str(text).strip().lower())
    except ValueError:
        action = InteractiveAction.APPROVE
    return HumanAction(action=action, reason=reason, proposed_amount=proposed_amount)


def _refresh_ui_spec(ui_spec: dict | None, decision: Decision) -> dict | None:
    """Clear pending actions after the confirmation interrupt resolves, and
    keep the outcome block in sync if the human overrode the computed path."""
    if not ui_spec:
        return ui_spec
    spec = deepcopy(ui_spec)
    for block in spec.get("blocks", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "interactive_actions":
            block["is_pending"] = False
            block["available_actions"] = []
            block["resumes_at_node"] = None
        elif block.get("type") == "outcome":
            block["outcome"] = decision.outcome.value
            block["amount"] = decision.amount
            block["confidence"] = decision.confidence
            block["escalation_reason"] = decision.escalation_reason
    return spec


@timed_node("commit_decision")
async def commit_decision(state: AdjudicationState) -> dict:
    decision = state["decision"]
    assert decision is not None, "commit_decision runs only after decision_composition"

    human_response = interrupt(
        {
            "kind": KIND_CONFIRMATION,
            "reason": (
                f"Confirm computed {decision.outcome.value} of {decision.amount} "
                "before this decision is committed."
            ),
            "claim_id": state.get("claim_id"),
            "decision_so_far": decision,
            "available_actions": [a.value for a in InteractiveAction],
        }
    )
    response = _normalize_action(human_response)
    action = response.action

    if action is InteractiveAction.OVERRIDE:
        if response.reason is None:
            raise ValueError("override requires a non-empty human reason")
        override_reason = response.reason
        review_flag = await flag_for_review(state["claim_id"], override_reason)
        committed = decision.model_copy(
            update={
                "outcome": Outcome.ESCALATE,
                "escalation_reason": override_reason,
            }
        )
        event_type = AuditEventType.DECISION_OVERRIDDEN
        proposed_note = (
            f" Proposed amount noted for audit only: {response.proposed_amount}."
            if response.proposed_amount is not None
            else ""
        )
        detail = (
            f"Human overrode computed {decision.outcome.value} of {decision.amount}; "
            f"reason: {override_reason!r}.{proposed_note} "
            "Decision.amount left unchanged; outcome downgraded to escalate."
        )
        committed_flag = False
        escalation_reason = committed.escalation_reason
    elif action is InteractiveAction.REQUEST_DOCUMENTS:
        committed = decision.model_copy(
            update={
                "outcome": Outcome.ESCALATE,
                "escalation_reason": "Adjuster requested additional documents before commit.",
            }
        )
        event_type = AuditEventType.DECISION_DOCUMENTS_REQUESTED
        detail = "Human requested documents; computed amount left unchanged, outcome downgraded to escalate."
        committed_flag = False
        escalation_reason = committed.escalation_reason
    elif action is InteractiveAction.APPROVE:
        committed = decision
        event_type = AuditEventType.DECISION_CONFIRMED
        detail = f"Human confirmed computed {committed.outcome.value} of {committed.amount}."
        committed_flag = True
        escalation_reason = state.get("escalation_reason")
    else:
        never: Never = action
        raise RuntimeError(f"Unhandled interactive action: {never}")

    event_kwargs: dict = {}
    audit_events = []
    if action is InteractiveAction.OVERRIDE:
        event_kwargs = {
            "original_outcome": decision.outcome,
            "original_amount": decision.amount,
            "override_reason": override_reason,
            "override_proposed_amount": response.proposed_amount,
        }
        audit_events.append(
            AuditEvent(
                event_type=AuditEventType.REVIEW_FLAGGED,
                node="commit_decision",
                detail=(
                    f"Human-confirmed override created manual-review flag "
                    f"{review_flag.flag_id}: {review_flag.reason}"
                ),
                timestamp=_now_iso(),
            )
        )

    audit_events.insert(
        0,
        AuditEvent(
            event_type=event_type,
            node="commit_decision",
            detail=detail,
            timestamp=_now_iso(),
            **event_kwargs,
        ),
    )
    return {
        "decision": committed,
        "decision_committed": committed_flag,
        "escalation_reason": escalation_reason,
        "ui_spec": _refresh_ui_spec(state.get("ui_spec"), committed),
        "audit_log": audit_events,
    }
