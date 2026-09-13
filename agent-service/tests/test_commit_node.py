import pytest

from graph.nodes.commit import _normalize_action, _refresh_ui_spec, commit_decision
from graph.schemas import AuditEventType, Decision, InteractiveAction, Outcome


def _paused_state():
    decision = Decision(outcome=Outcome.APPROVE, amount=3000.0, confidence=1.0)
    ui_spec = {
        "claim_id": "CLM-001",
        "blocks": [
            {
                "type": "outcome",
                "outcome": "approve",
                "amount": 3000.0,
                "confidence": 1.0,
                "escalation_reason": None,
            },
            {
                "type": "interactive_actions",
                "claim_id": "CLM-001",
                "thread_id": "CLM-001",
                "available_actions": ["approve", "override", "request_documents"],
                "resumes_at_node": "commit_decision",
                "is_pending": True,
            },
        ],
    }
    return {
        "claim_id": "CLM-001",
        "decision": decision,
        "ui_spec": ui_spec,
        "escalation_reason": None,
    }


def test_normalize_action_accepts_string_enum_and_dict():
    assert _normalize_action("override").action is InteractiveAction.OVERRIDE
    assert (
        _normalize_action(InteractiveAction.REQUEST_DOCUMENTS).action
        is InteractiveAction.REQUEST_DOCUMENTS
    )
    assert _normalize_action({"action": "approve"}).action is InteractiveAction.APPROVE
    assert _normalize_action("not-an-action").action is InteractiveAction.APPROVE


def test_normalize_action_reads_override_metadata():
    parsed = _normalize_action(
        {"action": "override", "reason": "  Repair estimate supports more.  ", "proposed_amount": 60000}
    )
    assert parsed.action is InteractiveAction.OVERRIDE
    assert parsed.reason == "Repair estimate supports more."
    assert parsed.proposed_amount == 60000


def test_refresh_ui_spec_clears_pending_and_syncs_outcome():
    state = _paused_state()
    overridden = Decision(
        outcome=Outcome.ESCALATE, amount=3000.0, confidence=1.0,
        escalation_reason="Adjuster overrode the computed decision before commit.",
    )
    spec = _refresh_ui_spec(state["ui_spec"], overridden)
    interactive = spec["blocks"][1]
    outcome = spec["blocks"][0]
    assert interactive["is_pending"] is False
    assert interactive["available_actions"] == []
    assert interactive["resumes_at_node"] is None
    assert outcome["outcome"] == "escalate"
    assert outcome["amount"] == 3000.0
    assert outcome["escalation_reason"] == overridden.escalation_reason


@pytest.mark.asyncio
async def test_confirm_approve_commits_without_changing_amount(monkeypatch):
    async def must_not_flag(*args):
        raise AssertionError("approve must not call flag_for_review")

    monkeypatch.setattr("graph.nodes.commit.interrupt", lambda payload: "approve")
    monkeypatch.setattr("graph.nodes.commit.flag_for_review", must_not_flag)
    result = await commit_decision(_paused_state())
    assert result["decision_committed"] is True
    assert result["decision"].outcome == Outcome.APPROVE
    assert result["decision"].amount == 3000.0
    assert result["audit_log"][0].event_type == AuditEventType.DECISION_CONFIRMED
    assert result["ui_spec"]["blocks"][1]["is_pending"] is False


@pytest.mark.asyncio
async def test_override_captures_reason_and_proposed_amount_without_changing_payout(monkeypatch):
    calls = []

    async def fake_flag(claim_id, reason):
        from graph.schemas import ReviewFlagResult

        calls.append((claim_id, reason))
        return ReviewFlagResult(
            flag_id="review-test",
            claim_id=claim_id,
            reason=reason,
            flagged_at="2024-01-01T00:00:00Z",
        )

    monkeypatch.setattr(
        "graph.nodes.commit.interrupt",
        lambda payload: {
            "action": "override",
            "reason": "The contractor estimate supports replacement rather than repair.",
            "proposed_amount": 60000,
        },
    )
    monkeypatch.setattr("graph.nodes.commit.flag_for_review", fake_flag)
    result = await commit_decision(_paused_state())
    assert result["decision_committed"] is False
    assert result["decision"].outcome == Outcome.ESCALATE
    # The proposed amount is audit-only. The engine's original figure remains
    # Decision.amount and no payout rule is called from commit_decision.
    assert result["decision"].amount == 3000.0
    event = result["audit_log"][0]
    assert event.event_type == AuditEventType.DECISION_OVERRIDDEN
    assert event.original_outcome == Outcome.APPROVE
    assert event.original_amount == 3000.0
    assert event.override_reason == (
        "The contractor estimate supports replacement rather than repair."
    )
    assert event.override_proposed_amount == 60000
    assert "60000" in event.detail
    assert "3000.0" in event.detail
    assert calls == [
        (
            "CLM-001",
            "The contractor estimate supports replacement rather than repair.",
        )
    ]
    assert result["audit_log"][1].event_type == AuditEventType.REVIEW_FLAGGED


@pytest.mark.asyncio
async def test_request_documents_downgrades_outcome_not_amount(monkeypatch):
    async def must_not_flag(*args):
        raise AssertionError("request_documents must not call flag_for_review")

    monkeypatch.setattr("graph.nodes.commit.interrupt", lambda payload: "request_documents")
    monkeypatch.setattr("graph.nodes.commit.flag_for_review", must_not_flag)
    result = await commit_decision(_paused_state())
    assert result["decision_committed"] is False
    assert result["decision"].outcome == Outcome.ESCALATE
    assert result["decision"].amount == 3000.0
    assert result["audit_log"][0].event_type == AuditEventType.DECISION_DOCUMENTS_REQUESTED


@pytest.mark.asyncio
async def test_override_without_reason_cannot_fire_review_tool(monkeypatch):
    async def must_not_flag(*args):
        raise AssertionError("invalid override must not call flag_for_review")

    monkeypatch.setattr("graph.nodes.commit.interrupt", lambda payload: "override")
    monkeypatch.setattr("graph.nodes.commit.flag_for_review", must_not_flag)
    with pytest.raises(ValueError, match="non-empty human reason"):
        await commit_decision(_paused_state())
