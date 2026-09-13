"""compute_payout — THE deterministic rules engine entry point.

ZERO LLM IMPORTS IN THIS FILE (see rules/eligibility.py module docstring and
tests/test_adversarial_llm_amount.py, which asserts this at import-graph level,
not just by convention).

This function is wrapped by the MCP `compute_payout` tool (mcp_server/server.py)
and is the ONLY place a rupee figure is produced anywhere in this system.
`decision_composition` (graph/nodes/decision.py) copies `total_payable` from
this function's output verbatim; it may downgrade the outcome to ESCALATE
based on risk/confidence, but it has no code path that can alter the amount.
"""

from __future__ import annotations

from graph.schemas import ClaimFacts, ClauseRef, EligibilityResult, LineItemVerdict, Outcome
from rules.eligibility import POLICY_EVALUATORS, home_flood_or_overflow_indicated


def derive_outcome_from_eligibility(result: EligibilityResult) -> Outcome:
    """Verdict-based outcome derivation, shared by decision_composition
    (graph/nodes/decision.py) and the ground-truth generator so both agree on
    what "approve" vs "partial" means.

    Deliberately NOT amount-based (e.g. "total_payable == total_claimed"):
    almost every claim's total_payable is reduced by the standard per-claim
    deductible, which would make nearly every claim look "partial" under an
    amount-equality test even when every line item was fully allowed. The
    deductible is a normal cost of the policy, not a partial-approval signal
    — so the outcome is derived from each line item's VERDICT instead.
    """
    if result.needs_escalation or not result.line_items:
        return Outcome.ESCALATE
    verdicts = {li.verdict for li in result.line_items}
    if verdicts == {LineItemVerdict.ALLOWED}:
        return Outcome.APPROVE
    if verdicts == {LineItemVerdict.EXCLUDED}:
        return Outcome.DENY
    return Outcome.PARTIAL


def compute_payout(facts: ClaimFacts, clauses: list[ClauseRef]) -> EligibilityResult:
    """Deterministic eligibility + payout computation.

    `clauses` (the retrieved policy text) is accepted for interface parity with
    the spec's `compute_payout(facts, clauses)` signature, but this function's
    own arithmetic does not depend on the retrieved TEXT — the rule tables in
    rules/eligibility.py are authored directly against clause IDs (see that
    module's docstring for why, and tests/test_rule_table_matches_fixtures.py
    for the drift guard between the table and the fixture text). Whether the
    clause IDs cited here were actually present in `retrieved_clauses` is a
    groundedness question, checked separately by the `explanation` node
    against the full evidence panel — deliberately not duplicated here, so
    this function stays unit-testable with an empty or partial clause list.
    """
    evaluator_entry = POLICY_EVALUATORS.get(facts.policy_id)
    if evaluator_entry is None:
        return EligibilityResult(
            line_items=[],
            deductible_applied=0.0,
            total_claimed=sum(li.claimed_amount for li in facts.line_items),
            total_payable=0.0,
            clauses_used=[],
            needs_escalation=True,
            escalation_reason=f"Unknown policy_id '{facts.policy_id}' — no rule table registered.",
        )

    evaluate_item, deductible_amount, deductible_clause = evaluator_entry

    if not facts.line_items:
        return EligibilityResult(
            line_items=[],
            deductible_applied=0.0,
            total_claimed=0.0,
            total_payable=0.0,
            clauses_used=[],
            needs_escalation=True,
            escalation_reason="No line items were extracted from the claim; nothing to adjudicate.",
        )

    line_results = [evaluate_item(item, facts) for item in facts.line_items]

    total_claimed = sum(r.claimed_amount for r in line_results)
    gross_payable = sum(r.allowed_amount for r in line_results)
    any_payable = gross_payable > 0
    deductible_applied = min(deductible_amount, gross_payable) if any_payable else 0.0
    total_payable = max(gross_payable - deductible_applied, 0.0)

    clauses_used = sorted({cid for r in line_results for cid in r.governing_clause_ids})
    if any_payable:
        clauses_used.append(deductible_clause)
        clauses_used = sorted(set(clauses_used))

    # A claim-wide `cause_ambiguous` fact (the extractor reporting "the narrative
    # itself doesn't resolve this") always escalates the whole claim, regardless
    # of policy. Several policies' own text requires this for their specific
    # ambiguity (e.g. POL-HOME-01 §7.1.4); rather than re-deriving that per
    # policy, one blanket rule here covers all of them, including cases (like
    # POL-HEALTH-01) whose evaluator has no bespoke ambiguity branch of its own.
    needs_escalation = facts.cause_ambiguous
    escalation_reason = None
    if needs_escalation:
        cited_clauses = sorted(
            {cid for r in line_results if r.verdict == LineItemVerdict.EXCLUDED for cid in r.governing_clause_ids}
        )
        clause_note = f" (see {', '.join(cited_clauses)})" if cited_clauses else ""
        escalation_reason = (
            "The claimant's account does not clearly resolve the cause of loss"
            f"{clause_note}; escalating for manual review rather than guessing an outcome."
        )
    elif facts.policy_id == "POL-HOME-01" and home_flood_or_overflow_indicated(facts):
        # Prefer escalate (not a silent deny) so the empty-coverage degradation
        # path's intent is preserved even when the LLM mis-tags flood as
        # water_damage and retrieval still returns plumbing clauses.
        needs_escalation = True
        total_payable = 0.0
        deductible_applied = 0.0
        escalation_reason = (
            "Flood/river-overflow damage is not covered by any clause in "
            "POL-HOME-01 — no governing policy applies for this loss."
        )

    return EligibilityResult(
        line_items=line_results,
        deductible_applied=deductible_applied,
        total_claimed=total_claimed,
        total_payable=total_payable,
        clauses_used=clauses_used,
        needs_escalation=needs_escalation,
        escalation_reason=escalation_reason,
    )
