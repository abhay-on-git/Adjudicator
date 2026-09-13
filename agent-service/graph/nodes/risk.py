"""risk_anomaly — runs in parallel with eligibility_evaluation.

Pure code, no LLM: every check here is a deterministic comparison over
`ClaimFacts` plus prior-claim history. This node only ever produces FLAGS and
a severity — it has no path that can touch the outcome or amount directly;
`decision_composition` decides how much weight a risk flag carries.

`get_claim_history` is a plain internal function for P0 (per the agreed scope
note in DESIGN.md), not an MCP tool — promoted to a real MCP tool in P1 when
the full tool surface is built out. It reads the same claims.json fixture the
golden set uses, standing in for what would be a real claims database.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from graph.schemas import AuditEvent, AuditEventType, RiskResult, RiskSeverity
from graph.state import AdjudicationState

CLAIMS_FIXTURE = Path(__file__).resolve().parent.parent.parent / "fixtures" / "claims" / "claims.json"

DOCUMENTATION_THRESHOLD = 10_000  # mirrors POL-HOME-01 §6.1's own threshold, used generically here


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_claim_history(policy_id: str, exclude_claim_id: str | None = None) -> list[dict]:
    """Prior claims filed under the same policy_id. Plain internal function
    (see module docstring) — not exposed over MCP in P0."""
    all_claims = json.loads(CLAIMS_FIXTURE.read_text(encoding="utf-8"))
    return [
        c for c in all_claims
        if c["policy_id"] == policy_id and c["claim_id"] != exclude_claim_id
    ]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def risk_anomaly(state: AdjudicationState) -> dict:
    facts = state["claim_facts"]
    envelope = state["normalized_envelope"]
    assert facts is not None, "risk_anomaly must run after a successful extraction"

    flags: list[str] = []
    duplicate_ids: list[str] = []

    history = get_claim_history(facts.policy_id, exclude_claim_id=envelope.get("claim_id"))
    for prior in history:
        prior_narrative = prior.get("narrative_text", "")
        if facts.date_of_loss and facts.date_of_loss[:10] in prior_narrative and prior.get("claim_id"):
            # Heuristic: the exact loss date recorded for THIS claim also
            # appears verbatim in a PRIOR claim's raw narrative under the same
            # policy — a plausible duplicate filing of the same event.
            duplicate_ids.append(prior["claim_id"])

    if duplicate_ids:
        flags.append("possible_duplicate_claim")

    filed = _parse_date(envelope.get("filed_date"))
    loss = _parse_date(facts.date_of_loss)
    if filed and loss and filed < loss:
        flags.append("filed_before_loss_date")

    if state.get("injection_flags"):
        flags.append("injection_attempt_detected")

    total_claimed = sum(li.claimed_amount for li in facts.line_items)
    if total_claimed > DOCUMENTATION_THRESHOLD and not facts.documents_mentioned:
        flags.append("high_value_claim_missing_documentation")

    round_amounts = [li.claimed_amount for li in facts.line_items if li.claimed_amount >= 10_000 and li.claimed_amount % 10_000 == 0]
    if round_amounts:
        flags.append("suspiciously_round_amount")

    # `filed_before_loss_date` is HIGH and forces decision_composition to
    # escalate — dates that do not make sense should not auto-settle.
    # `injection_attempt_detected` is MEDIUM on purpose: intake still flags
    # the attempt for the audit trail / eval, but the deterministic eligibility
    # outcome must stand (deny stays deny, approve stays approve). Promoting
    # injection to HIGH would override every injected claim to escalate and
    # defeat the injection-resistance property the suite measures.
    if "filed_before_loss_date" in flags:
        severity = RiskSeverity.HIGH
    elif (
        "injection_attempt_detected" in flags
        or "possible_duplicate_claim" in flags
        or "high_value_claim_missing_documentation" in flags
    ):
        severity = RiskSeverity.MEDIUM
    elif flags:
        severity = RiskSeverity.LOW
    else:
        severity = RiskSeverity.NONE

    result = RiskResult(severity=severity, flags=flags, duplicate_claim_ids=duplicate_ids)

    return {
        "risk_result": result,
        "audit_log": [
            AuditEvent(
                event_type=AuditEventType.RISK_COMPLETE,
                node="risk_anomaly",
                detail=f"severity={severity.value} flags={flags}",
                timestamp=_now_iso(),
            )
        ],
    }
