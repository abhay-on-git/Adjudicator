from graph.context_budget import (
    CLAIM_CONTEXT_TOKEN_BUDGET,
    apply_drop_policy,
    estimate_tokens,
    packed_tokens,
)
from graph.schemas import ClaimFacts, ClauseRef, LineItem, Peril


def _facts(perils):
    return ClaimFacts(
        policy_id="POL-HOME-01",
        policy_start_date="2024-01-01",
        date_of_loss="2024-06-01",
        perils=perils,
        line_items=[
            LineItem(description="Kitchen flooring", category="flooring", claimed_amount=8000),
            LineItem(description="Cabinet fire scorch", category="cabinetry", claimed_amount=12000),
        ],
        narrative_summary="Pipe burst and a small fire.",
    )


def _clause(clause_id, score, text, title="Clause"):
    return ClauseRef(
        policy_id="POL-HOME-01",
        clause_id=clause_id,
        title=title,
        text=text,
        relevance_score=score,
    )


def test_estimate_tokens_is_four_chars_per_token():
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("") == 0


def test_phase1_drops_lowest_relevance_first():
    narrative = "x" * 40
    facts = _facts([Peril.WATER_DAMAGE])
    low = _clause("§7.4", 0.1, "war exclusion " + ("noise " * 80), title="War")
    high = _clause(
        "§4.2.1",
        0.9,
        "sudden accidental discharge plumbing covered " + ("keep " * 20),
        title="Discharge",
    )
    pack = apply_drop_policy(narrative, facts, [high, low], budget=80)
    assert pack.dropped
    assert pack.dropped[0].clause_id == "§7.4"
    assert pack.dropped[0].reason == "lowest_relevance"
    assert [c.clause_id for c in pack.kept] == ["§4.2.1"]
    assert pack.tokens_before > pack.tokens_after


def test_phase2_drops_least_recent_peril_group_when_all_scores_are_high():
    narrative = "x" * 20
    facts = _facts([Peril.WATER_DAMAGE, Peril.FIRE])  # fire is least-recently-relevant
    water = _clause(
        "§4.2.1",
        0.9,
        "sudden accidental discharge plumbing " + ("water " * 60),
        title="Water",
    )
    fire = _clause(
        "§4.1",
        0.9,
        "fire damage covered " + ("fire " * 60),
        title="Fire",
    )
    pack = apply_drop_policy(narrative, facts, [water, fire], budget=80)
    dropped_ids = [d.clause_id for d in pack.dropped]
    assert "§4.1" in dropped_ids
    assert any(d.reason == "least_recent_peril:fire" for d in pack.dropped)
    assert "§4.2.1" in {c.clause_id for c in pack.kept}
    assert pack.tokens_before > pack.tokens_after


def test_never_drops_the_last_clause_even_if_still_over_budget():
    facts = _facts([Peril.WATER_DAMAGE])
    huge = _clause("§4.2.1", 0.9, "sudden accidental discharge plumbing " + ("x" * 4000))
    pack = apply_drop_policy("n", facts, [huge], budget=10)
    assert pack.kept == [huge]
    assert pack.dropped == []
    assert pack.tokens_after > pack.budget


def test_default_budget_covers_a_small_single_peril_pack_without_dropping():
    facts = _facts([Peril.WATER_DAMAGE])
    clause = _clause("§4.2.1", 0.9, "sudden accidental discharge plumbing is covered.")
    pack = apply_drop_policy("short narrative", facts, [clause])
    assert pack.budget == CLAIM_CONTEXT_TOKEN_BUDGET
    assert pack.dropped == []
    assert packed_tokens("short narrative", facts, [clause]) == pack.tokens_after
