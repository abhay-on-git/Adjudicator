"""Per-claim token budget for packed claim context (narrative + facts + clauses).

This is the context-engine budget the brief requires. It is *not* the live
LLM usage number in EVIDENCE.md §6.4 (that figure is extraction + explanation
API tokens). This budget caps the packed text that later nodes — especially
explanation — are allowed to see.

Budget choice (`CLAIM_CONTEXT_TOKEN_BUDGET = 3500`):
  EVIDENCE.md §6.4 slow_path_full mean is 5294 tokens / p95 7195 for a full
  MiniMax-M3 run. A typical packed context (narrative + ClaimFacts JSON +
  retrieved clause bodies) sits well below that — a single-peril home claim
  is a few hundred tokens; a multi-peril pack with a handful of clauses is
  still under ~2k with the 4-chars-per-token estimate. 3500 therefore
  comfortably covers a typical multi-peril claim without clipping it, and
  is still low enough that a many-item / many-peril retrieval will drop.

Token estimate: ``(len(text) + 3) // 4`` (4 characters ≈ 1 token). No
tiktoken dependency, so the drop is deterministic across providers.

Drop order (applied in this sequence, never as silent truncation):

  1. Lowest-relevance clauses first (one at a time; ties drop the later
     list entry). Never drop the last remaining clause.
  2. If still over budget, drop every clause assigned to the
     least-recently-relevant peril. Perils are ordered as in
     ``claim_facts.perils`` (first = most recently relevant / primary).
     Unassigned / administrative clauses are treated as less recent than
     any named peril and drop first in this phase. Repeat from the end of
     the peril list. Never drop the last remaining clause.

Eligibility still runs against the full retrieved set's coverage-hit
signal and the deterministic rule table — this pack only changes which
clause *texts* remain in state for explanation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from graph.schemas import ClaimFacts, ClauseRef, Peril

# See module docstring for why this number, not a cheaper single-peril pack.
CLAIM_CONTEXT_TOKEN_BUDGET = 3500
CHARS_PER_TOKEN = 4
# Must match graph.nodes.retrieval.MIN_COVERAGE_RELEVANCE — duplicated here
# to avoid a retrieval <-> context_budget import cycle.
_LOW_RELEVANCE_CEILING = 0.5


@dataclass(frozen=True)
class DroppedClause:
    clause_id: str
    policy_id: str
    relevance_score: float
    reason: str
    tokens: int


@dataclass
class BudgetPack:
    kept: list[ClauseRef]
    dropped: list[DroppedClause] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    budget: int = CLAIM_CONTEXT_TOKEN_BUDGET
    narrative_tokens: int = 0
    facts_tokens: int = 0


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return (len(text) + (CHARS_PER_TOKEN - 1)) // CHARS_PER_TOKEN


def _clause_blob(clause: ClauseRef) -> str:
    return f"{clause.clause_id} {clause.title} {clause.text}"


def _facts_blob(facts: ClaimFacts) -> str:
    return facts.model_dump_json()


def packed_tokens(narrative: str, facts: ClaimFacts, clauses: list[ClauseRef]) -> int:
    return (
        estimate_tokens(narrative)
        + estimate_tokens(_facts_blob(facts))
        + sum(estimate_tokens(_clause_blob(c)) for c in clauses)
    )


def _assign_peril(clause: ClauseRef, perils: list[Peril]) -> Peril | None:
    """Best lexical overlap against the peril's search vocabulary; None if
    the clause looks administrative (deductible, etc.) rather than peril-specific."""
    from graph.nodes.retrieval import MIN_COVERAGE_RELEVANCE, PERIL_SEARCH_QUERIES

    text = f"{clause.title} {clause.text}".lower()
    best: tuple[int, Peril] | None = None
    for peril in perils:
        query = PERIL_SEARCH_QUERIES.get(peril, peril.value.replace("_", " "))
        score = sum(1 for token in query.lower().split() if token and token in text)
        if score == 0:
            continue
        if best is None or score > best[0]:
            best = (score, peril)
    return None if best is None else best[1]


def apply_drop_policy(
    narrative: str,
    facts: ClaimFacts,
    clauses: list[ClauseRef],
    budget: int | None = None,
) -> BudgetPack:
    """Return `kept` clauses that fit `budget`, with an explicit drop log.

    Coverage-hit routing must already have been decided on the *untrimmed*
    retrieval; callers should not use an empty `kept` list to trigger
    degradation mode 1.
    """
    if budget is None:
        budget = CLAIM_CONTEXT_TOKEN_BUDGET
    kept = list(clauses)
    dropped: list[DroppedClause] = []
    narrative_tokens = estimate_tokens(narrative)
    facts_tokens = estimate_tokens(_facts_blob(facts))
    tokens_before = packed_tokens(narrative, facts, clauses)

    def current_tokens() -> int:
        return packed_tokens(narrative, facts, kept)

    # Phase 1 — lowest-relevance clauses first, but only incidental ones
    # (score at or below the retrieval coverage-hit floor). High-relevance
    # governing clauses are left for phase 2 so a multi-peril claim drops a
    # whole secondary peril rather than silently stripping one governing
    # clause at a time.
    while current_tokens() > budget and len(kept) > 1:
        idx = min(range(len(kept)), key=lambda i: (kept[i].relevance_score, -i))
        if kept[idx].relevance_score > _LOW_RELEVANCE_CEILING:
            break
        victim = kept.pop(idx)
        dropped.append(
            DroppedClause(
                clause_id=victim.clause_id,
                policy_id=victim.policy_id,
                relevance_score=victim.relevance_score,
                reason="lowest_relevance",
                tokens=estimate_tokens(_clause_blob(victim)),
            )
        )

    # Phase 2 — least-recently-relevant peril groups.
    perils = list(facts.perils)
    phase2_order: list[Peril | None] = [None, *reversed(perils)]
    for peril in phase2_order:
        if current_tokens() <= budget:
            break
        group = [c for c in kept if _assign_peril(c, perils) is peril]
        if not group:
            continue
        remaining = [c for c in kept if c not in group]
        if not remaining:
            continue
        for victim in group:
            dropped.append(
                DroppedClause(
                    clause_id=victim.clause_id,
                    policy_id=victim.policy_id,
                    relevance_score=victim.relevance_score,
                    reason=(
                        "least_recent_peril:unassigned"
                        if peril is None
                        else f"least_recent_peril:{peril.value}"
                    ),
                    tokens=estimate_tokens(_clause_blob(victim)),
                )
            )
        kept = remaining

    return BudgetPack(
        kept=kept,
        dropped=dropped,
        tokens_before=tokens_before,
        tokens_after=current_tokens(),
        budget=budget,
        narrative_tokens=narrative_tokens,
        facts_tokens=facts_tokens,
    )


def pack_audit_detail(pack: BudgetPack) -> str:
    dropped_ids = [d.clause_id for d in dropped_reasons(pack)]
    return (
        f"Context budget {pack.budget} tokens; packed {pack.tokens_before} -> "
        f"{pack.tokens_after} after dropping {len(pack.dropped)} clause(s) "
        f"{dropped_ids} (narrative={pack.narrative_tokens}, facts={pack.facts_tokens}). "
        f"Reasons: {[(d.clause_id, d.reason, d.tokens) for d in pack.dropped]}."
    )


def dropped_reasons(pack: BudgetPack) -> list[DroppedClause]:
    return pack.dropped
