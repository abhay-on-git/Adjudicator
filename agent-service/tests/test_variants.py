"""Unit tests for deterministic variant generators — no LLM, no graph."""

from eval.variants import (
    VARIANT_SAMPLE,
    VARIANT_TYPES,
    build_variants,
    city_variant,
    gender_variant,
    line_item_order_variant,
    name_variant,
    phrasing_variant,
)


BASE = {
    "claim_id": "CLM-001",
    "claimant_name": "Priya Nair",
    "claimant_gender": "female",
    "claimant_city": "Pune",
    "narrative_text": (
        "My name is Priya Nair, writing from Pune. On 30 July 2024 a pipe burst. "
        "I'm claiming ₹8,000 for flooring repair."
    ),
}


def test_name_variant_replaces_name_in_field_and_narrative():
    variant = name_variant(BASE)
    assert variant.variant_type == "name"
    assert variant.claim["claimant_name"] != "Priya Nair"
    assert "Priya Nair" not in variant.claim["narrative_text"]
    assert variant.claim["claimant_name"] in variant.claim["narrative_text"]
    assert variant.claim["claimant_city"] == "Pune"
    assert "₹8,000" in variant.claim["narrative_text"]


def test_city_variant_replaces_city_only():
    variant = city_variant(BASE)
    assert variant.claim["claimant_city"] != "Pune"
    assert "Pune" not in variant.claim["narrative_text"]
    assert variant.claim["claimant_name"] == "Priya Nair"


def test_gender_variant_swaps_name_and_gender_field():
    variant = gender_variant(BASE)
    assert variant.claim["claimant_gender"] == "male"
    assert variant.claim["claimant_name"] != "Priya Nair"
    assert "Priya Nair" not in variant.claim["narrative_text"]


def test_phrasing_variant_keeps_amount():
    variant = phrasing_variant(BASE)
    assert "8,000" in variant.claim["narrative_text"]


def test_line_item_order_reverses_sentences():
    variant = line_item_order_variant(BASE)
    assert variant.claim["narrative_text"].startswith("I'm claiming")
    assert "pipe burst" in variant.claim["narrative_text"]


def test_build_variants_emits_all_five_types():
    variants = build_variants(BASE)
    assert [v.variant_type for v in variants] == list(VARIANT_TYPES)


def test_variant_sample_is_15_claims():
    assert len(VARIANT_SAMPLE) == 15
    assert len(set(VARIANT_SAMPLE)) == 15
