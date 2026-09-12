"""policy_retrieval — fetch the clauses that actually govern THIS claim.

Pure code: query strings are templated deterministically from `ClaimFacts`
(peril names, line-item categories/descriptions, plus a fixed "deductible"
query so the deductible clause is always in the evidence set), not composed
by an LLM. Each query calls the `search_policy` MCP tool directly and
unconditionally (see DESIGN.md "Tool granularity (MCP)").

Degradation mode 1 (DESIGN.md): if this node retrieves zero clauses, the
graph's conditional edge (build_graph.py) routes straight to `escalation`
with reason "no governing policy found" — `eligibility_evaluation` must never
run against an empty clause set. This node only reports what it found; the
routing decision belongs to the graph, consistent with every other
control-flow decision in this system.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graph.schemas import AuditEvent, AuditEventType, ClauseRef
from graph.state import AdjudicationState
from mcp_client.client import search_policy


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedup(queries: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            deduped.append(q)
    return deduped


# Below this term-overlap score, a "hit" is more likely a coincidental shared
# word (e.g. every exclusion clause contains "damage") than a genuine match —
# calibrated against the fixture set: real peril/category matches score >=
# 0.62 in practice, incidental overlaps top out around 0.5. See
# tests/test_retrieval_node.py for the case this threshold exists to catch.
MIN_COVERAGE_RELEVANCE = 0.5


def _coverage_queries(facts) -> list[str]:
    """Peril/category queries — these are what determine whether the claim's
    actual PERIL is governed by anything in this policy at all. `Peril.OTHER`
    is deliberately excluded: "other" is not vocabulary that appears in any
    clause, so it would only ever contribute coincidental noise, never a
    genuine signal, to the coverage-hit decision."""
    queries = [p.value.replace("_", " ") for p in facts.perils if p.value != "other"]
    queries += [f"{item.category.replace('_', ' ')} {item.description}" for item in facts.line_items]
    return _dedup(queries)


def _administrative_queries() -> list[str]:
    """Always-relevant queries (deductible, documentation) that should show
    up in the evidence panel regardless of peril, but must NOT count toward
    "this claim's peril is covered" — a policy that covers nothing about a
    claimant's actual loss still has a deductible clause, and citing it
    would be a false signal that retrieval found something relevant."""
    return ["deductible"]


async def policy_retrieval(state: AdjudicationState) -> dict:
    facts = state["claim_facts"]
    assert facts is not None, "policy_retrieval must run after a successful extraction"

    coverage_queries = _coverage_queries(facts)
    admin_queries = _administrative_queries()

    coverage_clauses: list[ClauseRef] = []
    for query in coverage_queries:
        coverage_clauses.extend(await search_policy(query, facts.policy_id, top_k=5))

    admin_clauses: list[ClauseRef] = []
    for query in admin_queries:
        admin_clauses.extend(await search_policy(query, facts.policy_id, top_k=5))

    all_clauses = coverage_clauses + admin_clauses
    has_coverage_hit = any(c.relevance_score > MIN_COVERAGE_RELEVANCE for c in coverage_clauses)
    unique_ids = sorted({(c.policy_id, c.clause_id) for c in all_clauses})

    if has_coverage_hit:
        event_type = AuditEventType.RETRIEVAL_COMPLETE
        detail = (
            f"Retrieved {len(unique_ids)} distinct clause(s) for {facts.policy_id} "
            f"across {len(coverage_queries) + len(admin_queries)} queries "
            f"({len(coverage_queries)} peril/category, {len(admin_queries)} administrative)."
        )
    else:
        event_type = AuditEventType.RETRIEVAL_EMPTY
        detail = (
            f"Zero peril/category-relevant clauses retrieved for {facts.policy_id} "
            f"(perils={[p.value for p in facts.perils]}) — no governing policy text "
            "found for this claim's actual loss, regardless of administrative "
            "clauses (e.g. deductible) that always match."
        )

    result: dict = {
        "retrieved_clauses": all_clauses,
        "retrieval_had_coverage_hit": has_coverage_hit,
        "audit_log": [
            AuditEvent(event_type=event_type, node="policy_retrieval", detail=detail, timestamp=_now_iso())
        ],
    }
    if not has_coverage_hit:
        result["escalation_reason"] = "no governing policy found"
    return result
