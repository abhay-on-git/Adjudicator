"""Unit tests for the deterministic rules engine (rules/payout.py,
rules/eligibility.py). No LLM, no network calls — pure function tests."""

from graph.schemas import ClaimFacts, LineItem, LineItemVerdict, Peril
from rules.payout import compute_payout


def make_facts(**overrides) -> ClaimFacts:
    defaults = dict(
        policy_id="POL-HOME-01",
        policy_start_date="2024-01-01",
        date_of_loss="2024-06-01",
        perils=[Peril.WATER_DAMAGE],
        line_items=[],
        narrative_summary="test",
        evidence_tags=[],
        cause_ambiguous=False,
    )
    defaults.update(overrides)
    return ClaimFacts(**defaults)


def test_clean_approve_cabinetry_sudden_discharge():
    """Required interaction #1 (sub-limit gated by exclusion), non-excluded branch:
    sudden discharge, no seepage tag -> covered under §4.2.1, capped by §4.2.9."""
    facts = make_facts(
        line_items=[
            LineItem(description="Kitchen cabinets", category="cabinetry", claimed_amount=18_000,
                      evidence_tags=["sudden_discharge"]),
        ],
    )
    result = compute_payout(facts, clauses=[])
    assert result.line_items[0].verdict == LineItemVerdict.ALLOWED
    assert result.line_items[0].allowed_amount == 18_000
    assert "§4.2.1" in result.line_items[0].governing_clause_ids
    assert "§4.2.9" in result.line_items[0].governing_clause_ids
    assert result.total_payable == 18_000 - 5000  # deductible §2.3
    assert not result.needs_escalation


def test_cabinetry_capped_by_sublimit():
    facts = make_facts(
        line_items=[
            LineItem(description="Custom cabinetry", category="cabinetry", claimed_amount=71_500,
                      evidence_tags=["sudden_discharge"]),
        ],
    )
    result = compute_payout(facts, clauses=[])
    item = result.line_items[0]
    assert item.verdict == LineItemVerdict.REDUCED
    assert item.allowed_amount == 25_000  # §4.2.9 cap
    assert result.total_payable == 25_000 - 5000


def test_sublimit_gated_by_exclusion_seepage_excludes_in_full():
    """Required interaction #1, excluded branch: pre-existing seepage excludes the
    item ENTIRELY under §7.1.4 — the §4.2.9 sub-limit must not apply at all
    (i.e. allowed_amount is 0, not min(claimed, 25000))."""
    facts = make_facts(
        line_items=[
            LineItem(description="Water-damaged cabinets", category="cabinetry", claimed_amount=71_500,
                      evidence_tags=["pre_existing_seepage_mentioned"]),
        ],
    )
    result = compute_payout(facts, clauses=[])
    item = result.line_items[0]
    assert item.verdict == LineItemVerdict.EXCLUDED
    assert item.allowed_amount == 0.0
    assert item.governing_clause_ids == ["§7.1.4"]
    assert result.total_payable == 0.0


def test_ambiguous_cause_escalates_not_guessed():
    facts = make_facts(cause_ambiguous=True, line_items=[
        LineItem(description="Cabinets", category="cabinetry", claimed_amount=10_000,
                  evidence_tags=["sudden_discharge"]),
    ])
    result = compute_payout(facts, clauses=[])
    assert result.needs_escalation
    assert "§7.1.4" in (result.escalation_reason or "")


def test_ambiguous_cause_escalates_even_when_health_evaluator_has_no_bespoke_branch():
    """The blanket cause_ambiguous -> escalate rule in compute_payout must catch
    policies whose own evaluator has no special ambiguity handling."""
    facts = make_facts(
        policy_id="POL-HEALTH-01",
        policy_start_date="2024-06-01",
        date_of_loss="2024-07-05",
        perils=[Peril.HOSPITALIZATION],
        evidence_tags=["pre_existing_condition_mentioned"],
        cause_ambiguous=True,
        line_items=[LineItem(description="Diagnostics", category="diagnostics", claimed_amount=28_000)],
    )
    result = compute_payout(facts, clauses=[])
    assert result.needs_escalation


def test_waiting_period_waived_by_accidental_injury_condition():
    """Required interaction #2: a health claim within the 24-month pre-existing
    waiting period is normally denied, but the accidental-injury waiver (§3.1.5)
    lifts it entirely for the injury being claimed."""
    facts = make_facts(
        policy_id="POL-HEALTH-01",
        policy_start_date="2024-06-01",
        date_of_loss="2024-07-01",  # 30 days in, well within the 24mo window
        perils=[Peril.ACCIDENTAL_INJURY],
        evidence_tags=["pre_existing_condition_mentioned", "accidental_injury"],
        line_items=[
            LineItem(description="Fracture surgery", category="surgery", claimed_amount=90_000),
        ],
    )
    result = compute_payout(facts, clauses=[])
    item = result.line_items[0]
    assert item.verdict == LineItemVerdict.ALLOWED
    assert item.allowed_amount == 90_000
    assert "§3.1.5" in item.governing_clause_ids
    assert result.total_payable == 90_000 - 2000  # §2.1 deductible


def test_waiting_period_denies_without_waiver():
    facts = make_facts(
        policy_id="POL-HEALTH-01",
        policy_start_date="2024-06-01",
        date_of_loss="2024-07-01",
        perils=[Peril.HOSPITALIZATION],
        evidence_tags=["pre_existing_condition_mentioned"],
        line_items=[
            LineItem(description="Diabetes treatment", category="medicines", claimed_amount=15_000),
        ],
    )
    result = compute_payout(facts, clauses=[])
    item = result.line_items[0]
    assert item.verdict == LineItemVerdict.EXCLUDED
    assert item.governing_clause_ids == ["§3.1.2"]
    assert result.total_payable == 0.0


def test_clean_deny_theft_no_forcible_entry():
    facts = make_facts(
        perils=[Peril.THEFT],
        line_items=[
            LineItem(description="Laptop stolen", category="valuables", claimed_amount=40_000),
        ],
    )
    result = compute_payout(facts, clauses=[])
    item = result.line_items[0]
    assert item.verdict == LineItemVerdict.EXCLUDED
    assert item.governing_clause_ids == ["§7.3"]
    assert result.total_payable == 0.0


def test_unknown_policy_escalates_rather_than_guessing():
    facts = make_facts(policy_id="POL-DOES-NOT-EXIST")
    result = compute_payout(facts, clauses=[])
    assert result.needs_escalation
    assert result.total_payable == 0.0


def test_no_line_items_escalates():
    facts = make_facts(line_items=[])
    result = compute_payout(facts, clauses=[])
    assert result.needs_escalation
    assert "No line items" in (result.escalation_reason or "")


def test_multi_peril_claim_routes_each_item_by_its_own_category_not_claim_peril_list():
    """A claim with fire + theft + one ambiguous water-damage item must not let
    the water_damage peril in facts.perils drag the unrelated fire item into
    the water-damage exclusion branch."""
    facts = make_facts(
        perils=[Peril.FIRE, Peril.THEFT, Peril.WATER_DAMAGE],
        cause_ambiguous=True,  # only true because of the water-stained cabinet item
        evidence_tags=["forcible_entry_evidence"],
        line_items=[
            LineItem(description="Fire cleanup", category="other", claimed_amount=20_000),
            LineItem(description="Water-stained cabinet", category="cabinetry", claimed_amount=18_000),
            LineItem(description="Stolen jewellery", category="valuables", claimed_amount=80_000),
        ],
    )
    result = compute_payout(facts, clauses=[])
    fire_item, cabinet_item, jewellery_item = result.line_items
    assert fire_item.verdict == LineItemVerdict.ALLOWED
    assert fire_item.governing_clause_ids == ["§4.1"]  # not §7.1.4
    assert cabinet_item.verdict == LineItemVerdict.EXCLUDED
    assert jewellery_item.verdict == LineItemVerdict.ALLOWED  # ₹80,000 is within §5.2's ₹1,00,000 cap
    # Claim-wide ambiguity still escalates the whole claim even though only one item is ambiguous.
    assert result.needs_escalation


def test_deductible_applied_once_per_claim_not_per_item():
    facts = make_facts(
        line_items=[
            LineItem(description="Cabinet A", category="cabinetry", claimed_amount=5_000,
                      evidence_tags=["sudden_discharge"]),
            LineItem(description="Cabinet B", category="cabinetry", claimed_amount=5_000,
                      evidence_tags=["sudden_discharge"]),
        ],
    )
    result = compute_payout(facts, clauses=[])
    assert result.deductible_applied == 5000  # once, not 10000
    assert result.total_payable == 5000  # (5000+5000) - 5000


def test_home_flood_mis_tagged_as_water_damage_still_escalates_uncovered():
    """CLM-022 class: LLM may emit water_damage + sudden_discharge for river
    flood; rules must not approve under §4.2.1."""
    facts = make_facts(
        date_of_loss="2024-08-08",
        perils=[Peril.WATER_DAMAGE],
        narrative_summary="Ground floor flooded when the river overflowed.",
        line_items=[
            LineItem(
                description="Flood damage to flooring and furniture from river overflow",
                category="flooring",
                claimed_amount=90_000,
                evidence_tags=["sudden_discharge"],
            )
        ],
    )
    result = compute_payout(facts, clauses=[])
    assert result.line_items[0].verdict == LineItemVerdict.EXCLUDED
    assert result.total_payable == 0.0
    assert result.needs_escalation
    assert "Flood" in (result.escalation_reason or "")


def test_connected_multi_peril_fire_and_theft_each_match_own_clause():
    """Regression test for CLM-48be7f0da1: a claim with fire and theft where theft occurred
    through a fire-damaged window. Each line item must evaluate strictly against its own peril
    and governing clause (§4.1 for fire, §5.1/§5.2 for theft), with §5.2's ₹1,00,000 sub-limit
    genuinely evaluated against the theft amount, never cross-contaminating with fire (§4.1)."""
    facts = make_facts(
        policy_id="POL-HOME-01",
        policy_start_date="2024-01-10",
        date_of_loss="2024-09-10",
        perils=[Peril.FIRE, Peril.THEFT],
        evidence_tags=[],
        line_items=[
            LineItem(
                description="Fire damage to kitchen and living room",
                category="structural_damage",
                claimed_amount=210_000,
                peril=Peril.FIRE,
            ),
            LineItem(
                description="Jewelry and electronics stolen during break-in through fire-damaged window",
                category="jewelry_and_electronics",
                claimed_amount=85_000,
                peril=Peril.THEFT,
                evidence_tags=["forcible_entry_evidence"],
            ),
        ],
    )
    result = compute_payout(facts, clauses=[])
    fire_item, theft_item = result.line_items

    # Fire item matches §4.1 only
    assert fire_item.verdict == LineItemVerdict.ALLOWED
    assert fire_item.allowed_amount == 210_000.0
    assert fire_item.governing_clause_ids == ["§4.1"]

    # Theft item matches §5.1 and §5.2 only, not §4.1
    assert theft_item.verdict == LineItemVerdict.ALLOWED
    assert theft_item.allowed_amount == 85_000.0
    assert theft_item.governing_clause_ids == ["§5.1", "§5.2"]
    assert "§4.1" not in theft_item.governing_clause_ids
    assert "capped at ₹1,00,000" in theft_item.reason

    # Overall payout: (210,000 + 85,000) - 5,000 deductible = 290,000
    assert result.total_claimed == 295_000.0
    assert result.total_payable == 290_000.0
    assert result.deductible_applied == 5_000.0
    assert set(result.clauses_used) == {"§2.3", "§4.1", "§5.1", "§5.2"}

