from graph.nodes.router import router
from graph.schemas import ClaimFacts, ClaimComplexity, LineItem, MissingField, Peril


def make_state(**facts_overrides) -> dict:
    defaults = dict(
        policy_id="POL-HOME-01",
        policy_start_date="2024-01-01",
        date_of_loss="2024-06-01",
        perils=[Peril.WATER_DAMAGE],
        line_items=[LineItem(description="Cabinet", category="cabinetry", claimed_amount=5000)],
        narrative_summary="test",
    )
    defaults.update(facts_overrides)
    return {"claim_facts": ClaimFacts(**defaults)}


def test_clean_single_peril_single_item_is_simple_fast_path():
    result = router(make_state())
    routing = result["routing"]
    assert routing.complexity == ClaimComplexity.SIMPLE
    assert routing.fast_path is True
    assert routing.missing_fields == []


def test_multi_peril_is_complex_not_fast_path():
    result = router(make_state(perils=[Peril.FIRE, Peril.THEFT]))
    routing = result["routing"]
    assert routing.is_multi_peril is True
    assert routing.complexity == ClaimComplexity.COMPLEX
    assert routing.fast_path is False


def test_ambiguous_cause_is_complex():
    result = router(make_state(cause_ambiguous=True))
    assert result["routing"].complexity == ClaimComplexity.COMPLEX


def test_missing_date_flags_and_forces_complex():
    result = router(make_state(date_of_loss=None))
    routing = result["routing"]
    assert MissingField.DATE_OF_LOSS in routing.missing_fields
    assert routing.fast_path is False


def test_missing_line_items_flags():
    result = router(make_state(line_items=[]))
    routing = result["routing"]
    assert MissingField.LINE_ITEMS in routing.missing_fields
    assert MissingField.PERIL not in routing.missing_fields  # perils still present


def test_unknown_policy_flags_invalid():
    result = router(make_state(policy_id="POL-DOES-NOT-EXIST"))
    routing = result["routing"]
    assert routing.policy_valid is False
    assert MissingField.POLICY_ID in routing.missing_fields


def test_three_line_items_is_complex():
    items = [LineItem(description=f"Item {i}", category="other", claimed_amount=1000) for i in range(3)]
    result = router(make_state(line_items=items))
    assert result["routing"].complexity == ClaimComplexity.COMPLEX


def test_two_line_items_is_standard():
    items = [LineItem(description=f"Item {i}", category="other", claimed_amount=1000) for i in range(2)]
    result = router(make_state(line_items=items))
    assert result["routing"].complexity == ClaimComplexity.STANDARD
