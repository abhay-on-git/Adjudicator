"""LangGraph state schema for the adjudication graph.

Every field below documents WHY it uses the reducer it uses — append-only,
overwrite (LangGraph's default `LastValue` channel, one write per superstep),
or a custom merge. This is read closely per the master instructions; nothing
here is implicit.

Reference: LangGraph applies all writes from all tasks in a superstep to their
target channel via that channel's `update()`. Two parallel tasks writing the
SAME key in one superstep will raise (a bare/overwrite key only accepts one
write per superstep) — this is why eligibility_evaluation and risk_anomaly,
which run in parallel, write to two DIFFERENT keys (`eligibility_result`,
`risk_result`) rather than a shared key. No reducer is needed to make the
fan-in safe; keeping the keys disjoint does that.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from graph.schemas import (
    AuditEvent,
    ClaimFacts,
    ClauseRef,
    Decision,
    EligibilityResult,
    RiskResult,
    RoutingDecision,
)


def merge_clauses_by_id(
    left: list[ClauseRef], right: list[ClauseRef]
) -> list[ClauseRef]:
    """Union two clause lists, deduping by (policy_id, clause_id), keeping the
    higher-relevance copy on conflict.

    Why not overwrite: a second retrieval pass (e.g. a multi-peril claim that
    triggers a follow-up query for its second peril) must not drop the first
    peril's clauses. Why not plain append: the same clause can legitimately be
    retrieved twice for two different queries in one claim, and duplicating it
    in the evidence panel would look like sloppy retrieval, not real evidence.
    """
    by_key: dict[tuple[str, str], ClauseRef] = {
        (c.policy_id, c.clause_id): c for c in left
    }
    for c in right:
        key = (c.policy_id, c.clause_id)
        existing = by_key.get(key)
        if existing is None or c.relevance_score > existing.relevance_score:
            by_key[key] = c
    return list(by_key.values())


def merge_node_metrics(
    left: dict[str, dict], right: dict[str, dict]
) -> dict[str, dict]:
    """Merge per-node metrics dicts by node name.

    Why not overwrite: `node_metrics` is written by many different nodes
    across the run (each contributes its own {tokens, cost_usd, latency_ms}
    entry keyed by its own node name). A plain overwrite reducer would let
    the last node's write clobber every earlier node's numbers, destroying
    the per-claim cost/latency breakdown EVIDENCE.md needs.
    """
    merged = dict(left)
    merged.update(right)
    return merged


class AdjudicationState(TypedDict):
    # --- Identity & raw input -----------------------------------------
    claim_id: str  # overwrite: set once at intake, the checkpointer's thread_id
    raw_input: dict  # overwrite: verbatim submission, kept whole for AUDIT.md

    # --- Intake -----------------------------------------------------------
    normalized_envelope: dict  # overwrite: pure function of raw_input, idempotent to re-run
    injection_flags: Annotated[list[str], operator.add]
    # append: multiple nodes (intake, risk) can each independently flag a
    # suspicious pattern; the injection-resistance evidence needs the full
    # detection history, not just the most recent node's view.

    # --- Extraction ---------------------------------------------------
    claim_facts: ClaimFacts | None  # overwrite: single source of truth; a retry replaces, not accumulates
    extraction_attempts: int  # overwrite: simple retry counter for the degradation policy

    # --- Routing ------------------------------------------------------
    routing: RoutingDecision | None  # overwrite: derived fresh from claim_facts every run

    # --- Retrieval ------------------------------------------------------
    retrieved_clauses: Annotated[list[ClauseRef], merge_clauses_by_id]
    retrieval_had_coverage_hit: bool
    # overwrite: a dedicated boolean signal (rather than re-deriving from
    # retrieved_clauses or parsing audit_log strings) for the graph's
    # conditional edge to decide "escalate — no governing policy found"
    # (degradation mode 1) without re-implementing policy_retrieval's own
    # coverage-vs-administrative distinction downstream.

    # --- Parallel fan-out: eligibility + risk (disjoint keys, see module docstring)
    eligibility_result: EligibilityResult | None  # overwrite: deterministic, single source of truth
    risk_result: RiskResult | None  # overwrite: deterministic, single source of truth

    # --- Decision -------------------------------------------------------
    decision: Decision | None  # overwrite: one deterministic composition step

    # --- Explanation ------------------------------------------------------
    explanation_text: str  # overwrite: regenerated whole on each run, not accumulated
    groundedness_violations: Annotated[list[str], operator.add]
    # append: accumulates across the run so the groundedness evidence report
    # reflects every violation ever detected for this claim, not just the
    # last explanation pass.

    # --- Escalation -------------------------------------------------------
    escalation_reason: str | None  # overwrite: at most one active escalation reason at a time

    # --- Audit trail --------------------------------------------------------
    audit_log: Annotated[list[AuditEvent], operator.add]
    # append, strictly monotonic: this IS the audit trail AUDIT.md reads.
    # Dropping or overwriting an entry would make a completed claim
    # unauditable by definition — every node appends, none may clear it.

    # --- Metrics ----------------------------------------------------------
    node_metrics: Annotated[dict[str, dict], merge_node_metrics]

    # --- UI ------------------------------------------------------------
    ui_spec: dict | None  # overwrite: final render artifact, one per completed run


def initial_state(claim_id: str, raw_input: dict) -> AdjudicationState:
    """Fully-populated initial state for a new run. LangGraph's reducers
    (operator.add, merge_clauses_by_id, merge_node_metrics) need SOMETHING to
    merge onto in the first superstep — a TypedDict has no runtime defaults,
    so every accumulating field is explicitly seeded empty here rather than
    left absent."""
    return AdjudicationState(
        claim_id=claim_id,
        raw_input=raw_input,
        normalized_envelope={},
        injection_flags=[],
        claim_facts=None,
        extraction_attempts=0,
        routing=None,
        retrieved_clauses=[],
        retrieval_had_coverage_hit=False,
        eligibility_result=None,
        risk_result=None,
        decision=None,
        explanation_text="",
        groundedness_violations=[],
        escalation_reason=None,
        audit_log=[],
        node_metrics={},
        ui_spec=None,
    )
