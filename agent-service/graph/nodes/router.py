"""router — claim type and complexity -> which downstream path.

Pure code, no LLM: routing is a cheap deterministic classification over
already-structured `ClaimFacts`, not a judgment call that needs a model.

This node is also where the "real divergence" requirement lives: it sets
`missing_fields`/`policy_valid`, which the compiled graph's conditional edge
uses to short-circuit straight to `escalation` for a missing-info claim
(skipping policy_retrieval/eligibility/risk entirely — see DESIGN.md's
"Degradation policy" table, mode 2 is enforced by `extraction` itself failing
validation; this node's missing_fields check catches the case where
extraction succeeded but genuinely couldn't find a required fact, e.g. no
date or no line items in the narrative at all).
"""

from __future__ import annotations

from datetime import datetime, timezone

from graph.nodes.metrics import timed_node
from graph.schemas import (
    AuditEvent,
    AuditEventType,
    ClaimComplexity,
    MissingField,
    RoutingDecision,
)
from graph.state import AdjudicationState
from rules.eligibility import POLICY_EVALUATORS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@timed_node("router")
def router(state: AdjudicationState) -> dict:
    facts = state["claim_facts"]
    assert facts is not None, "router must run after a successful extraction"

    missing: list[MissingField] = []
    policy_valid = facts.policy_id in POLICY_EVALUATORS
    if not policy_valid:
        missing.append(MissingField.POLICY_ID)
    if not facts.date_of_loss:
        missing.append(MissingField.DATE_OF_LOSS)
    if not facts.perils:
        missing.append(MissingField.PERIL)
    if not facts.line_items:
        missing.append(MissingField.LINE_ITEMS)

    is_multi_peril = len(facts.perils) > 1
    if missing:
        complexity = ClaimComplexity.COMPLEX  # will escalate anyway; label reflects "needs a human"
    elif is_multi_peril or facts.cause_ambiguous or len(facts.line_items) >= 3:
        complexity = ClaimComplexity.COMPLEX
    elif len(facts.line_items) == 2:
        complexity = ClaimComplexity.STANDARD
    else:
        complexity = ClaimComplexity.SIMPLE

    fast_path = complexity == ClaimComplexity.SIMPLE and not missing

    routing = RoutingDecision(
        complexity=complexity,
        is_multi_peril=is_multi_peril,
        fast_path=fast_path,
        missing_fields=missing,
        policy_valid=policy_valid,
    )

    detail = f"complexity={complexity.value} fast_path={fast_path} missing_fields={[m.value for m in missing]}"
    result: dict = {
        "routing": routing,
        "audit_log": [
            AuditEvent(
                event_type=AuditEventType.ROUTING_COMPLETE,
                node="router",
                detail=detail,
                timestamp=_now_iso(),
            )
        ],
    }
    if missing:
        # The graph's conditional edge (build_graph.py) reads this key to
        # route straight to escalation, same mechanism extraction/
        # policy_retrieval use for their own degradation modes.
        result["escalation_reason"] = f"Missing required field(s): {[m.value for m in missing]}."
    return result
