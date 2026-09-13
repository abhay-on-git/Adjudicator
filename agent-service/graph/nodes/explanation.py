"""explanation — the second LLM-backed node.

Generates the plain-language explanation shown to the claimant/adjuster.
The LLM is given the ALREADY-COMPUTED `Decision`/`EligibilityResult` as
read-only context — its structured-output schema (`ExplanationOutput`, see
graph/schemas.py) has no field that could change the outcome or amount, only
`narrative` (text) and `cited_clause_ids` (for the groundedness check below).

Uses the configured provider via `parse_structured`.

Groundedness check has two barriers: the cited ID must be in
`retrieved_clauses`, and the sentence containing it must have meaningful
keyword/entity overlap with that clause's title/text. Presence failures and
content mismatches are typed separately in `groundedness_violations`.

Degradation: unlike `extraction`, a failure here does NOT escalate the whole
claim — the Decision is already final and deterministic; this node only
affects HOW it's phrased. On repeated API failure, falls back to a
template-generated explanation from the same data instead.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graph.groundedness import verify_citation_content
from graph.nodes.llm_utils import call_with_backoff, parse_structured
from graph.nodes.metrics import timed_node
from graph.schemas import AuditEvent, AuditEventType, ExplanationOutput
from graph.state import AdjudicationState

MAX_API_RETRIES = 2
BACKOFF_SECONDS = [1, 2]

_SYSTEM_PROMPT = """You write a short, plain-language explanation of an insurance \
claim decision for the claimant. The decision (outcome and amount) is FINAL and \
ALREADY COMPUTED — you are explaining it, not deciding it. Do not state a different \
outcome or amount than the one given to you. Cite clause IDs (e.g. "§4.2.9") inline \
wherever you reference a specific rule, and list every clause_id you cite in \
`cited_clause_ids`. Only cite a clause that is in the provided evidence — never \
invent a clause_id."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_context(state: AdjudicationState) -> str:
    decision = state["decision"]
    eligibility = state["eligibility_result"]
    clauses = state.get("retrieved_clauses", [])
    lines = [
        f"Outcome: {decision.outcome.value}",
        f"Amount payable: {decision.amount}",
        f"Confidence: {decision.confidence}",
    ]
    if decision.escalation_reason:
        lines.append(f"Escalation reason: {decision.escalation_reason}")
    lines.append("Line items:")
    for li in eligibility.line_items:
        lines.append(
            f"  - {li.description}: claimed={li.claimed_amount}, verdict={li.verdict.value}, "
            f"allowed={li.allowed_amount}, governing_clauses={li.governing_clause_ids}, reason={li.reason}"
        )
    lines.append(f"Deductible applied: {eligibility.deductible_applied}")
    lines.append("Available evidence clauses (cite ONLY from this list):")
    for c in clauses:
        lines.append(f"  - {c.clause_id} ({c.title}): {c.text[:200]}")
    drop = state.get("context_drop")
    if drop:
        dropped_ids = [d["clause_id"] for d in drop.get("dropped", [])]
        lines.append(
            "Context-budget drop (do not cite these as if they were in evidence; "
            f"they were retrieved then dropped): {dropped_ids}. "
            f"tokens {drop.get('tokens_before')} -> {drop.get('tokens_after')} "
            f"against budget {drop.get('budget')}."
        )
    return "\n".join(lines)


async def _call_llm(context: str) -> ExplanationOutput:
    return await parse_structured(
        system=_SYSTEM_PROMPT,
        user=context,
        text_format=ExplanationOutput,
    )


def _template_fallback(state: AdjudicationState) -> ExplanationOutput:
    decision = state["decision"]
    eligibility = state["eligibility_result"]
    clause_list = ", ".join(eligibility.clauses_used) or "none"
    text = (
        f"Decision: {decision.outcome.value}. Payable amount: {decision.amount}. "
        f"Governing clauses: {clause_list}. "
        "(Automatically generated fallback explanation — the narrative-generation "
        "service was unavailable; the decision above was computed normally.)"
    )
    return ExplanationOutput(narrative=text, cited_clause_ids=eligibility.clauses_used)


@timed_node("explanation", llm=True)
async def explanation(state: AdjudicationState) -> dict:
    context = _build_context(state)
    retrieved_by_id = {c.clause_id: c for c in state.get("retrieved_clauses", [])}

    parsed, api_failure_reason = await call_with_backoff(
        lambda: _call_llm(context), max_retries=MAX_API_RETRIES, backoff_seconds=BACKOFF_SECONDS
    )

    audit_events: list[AuditEvent] = []
    violations: list[str] = []

    if api_failure_reason is not None or parsed is None:
        audit_events.append(
            AuditEvent(
                event_type=AuditEventType.LLM_UNAVAILABLE, node="explanation",
                detail=f"Falling back to template explanation: {api_failure_reason}",
                timestamp=_now_iso(),
            )
        )
        parsed = _template_fallback(state)
        grounded_clause_ids = parsed.cited_clause_ids
    else:
        grounded_clause_ids = []
        for cid in parsed.cited_clause_ids:
            clause = retrieved_by_id.get(cid)
            if clause is None:
                violations.append(f"citation_not_retrieved:{cid}")
                continue
            support = verify_citation_content(parsed.narrative, clause)
            if support.supported:
                grounded_clause_ids.append(cid)
            elif support.violation:
                violations.append(support.violation)
        if violations:
            audit_events.append(
                AuditEvent(
                    event_type=AuditEventType.GROUNDEDNESS_VIOLATION, node="explanation",
                    detail=f"Explanation citation verification failed: {violations}",
                    timestamp=_now_iso(),
                )
            )
        audit_events.append(
            AuditEvent(
                event_type=AuditEventType.EXPLANATION_COMPLETE, node="explanation",
                detail=f"Explanation generated, {len(grounded_clause_ids)} grounded citation(s), "
                f"{len(violations)} violation(s).",
                timestamp=_now_iso(),
            )
        )

    return {
        "explanation_text": parsed.narrative,
        "groundedness_violations": violations,
        "audit_log": audit_events,
    }
