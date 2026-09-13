"""Held-out P0 scenarios that previously failed in live UI testing.

These lock the *deterministic* middle of the pipeline (retrieval → payout →
risk → decision) so regressions like motor empty-retrieval or wrong
cabinetry math cannot slip back in. LLM extraction is assumed successful
with the facts a correct extract would produce — extraction quality is
covered separately by eval/run_eval.py.
"""

from __future__ import annotations

import pytest

from graph.nodes.decision import decision_composition
from graph.nodes.retrieval import policy_retrieval
from graph.nodes.risk import risk_anomaly
from graph.nodes.router import router
from graph.schemas import (
    ClaimFacts,
    LineItem,
    LineItemVerdict,
    MissingField,
    Outcome,
    Peril,
    RiskSeverity,
)
from rules.payout import compute_payout, derive_outcome_from_eligibility


def _facts(**overrides) -> ClaimFacts:
    defaults = dict(
        policy_id="POL-HOME-01",
        policy_start_date="2023-01-01",
        date_of_loss="2024-06-01",
        perils=[Peril.WATER_DAMAGE],
        line_items=[],
        narrative_summary="held-out",
        evidence_tags=[],
        cause_ambiguous=False,
        documents_mentioned=["photos", "estimate"],
    )
    defaults.update(overrides)
    return ClaimFacts(**defaults)


@pytest.mark.asyncio
async def test_held_out_motor_collision_reaches_approve_amount():
    """HELD-03 class: motor accident + weak category must retrieve and pay."""
    facts = _facts(
        policy_id="POL-MOTOR-01",
        policy_start_date="2023-09-01",
        date_of_loss="2024-07-15",
        perils=[Peril.MOTOR_ACCIDENT],
        line_items=[
            LineItem(
                description="Passenger door panel and paint",
                category="door_repair",
                claimed_amount=45_000,
            )
        ],
    )
    retrieval = await policy_retrieval({"claim_facts": facts})
    assert retrieval["retrieval_had_coverage_hit"] is True

    eligibility = compute_payout(facts, retrieval["retrieved_clauses"])
    assert eligibility.line_items[0].verdict == LineItemVerdict.ALLOWED
    assert eligibility.total_payable == 44_000  # 45000 - 1000 motor deductible
    assert derive_outcome_from_eligibility(eligibility) == Outcome.APPROVE

    risk = risk_anomaly(
        {
            "claim_facts": facts,
            "normalized_envelope": {
                "claim_id": "HELD-MOTOR-01",
                "filed_date": "2024-07-18",
                "policy_id": "POL-MOTOR-01",
            },
            "injection_flags": [],
        }
    )
    decision = decision_composition(
        {"eligibility_result": eligibility, "risk_result": risk["risk_result"]}
    )["decision"]
    assert decision.outcome == Outcome.APPROVE
    assert decision.amount == 44_000


def test_held_out_home_fire_approve_after_deductible():
    facts = _facts(
        policy_start_date="2023-03-15",
        date_of_loss="2024-11-12",
        perils=[Peril.FIRE],
        line_items=[
            LineItem(
                description="Curtains and repainting",
                category="contents",
                claimed_amount=20_000,
            )
        ],
    )
    eligibility = compute_payout(facts, [])
    assert eligibility.total_payable == 15_000
    assert derive_outcome_from_eligibility(eligibility) == Outcome.APPROVE


def test_held_out_waiting_period_denies_non_fire_home_loss():
    facts = _facts(
        policy_start_date="2024-10-01",
        date_of_loss="2024-10-08",
        perils=[Peril.WATER_DAMAGE],
        line_items=[
            LineItem(
                description="Bathroom floor tiles",
                category="flooring",
                claimed_amount=12_000,
                evidence_tags=["sudden_discharge"],
            )
        ],
    )
    eligibility = compute_payout(facts, [])
    assert eligibility.total_payable == 0.0
    assert eligibility.line_items[0].verdict == LineItemVerdict.EXCLUDED
    assert derive_outcome_from_eligibility(eligibility) == Outcome.DENY


def test_held_out_health_dengue_approve_after_deductible():
    facts = _facts(
        policy_id="POL-HEALTH-01",
        policy_start_date="2022-01-01",
        date_of_loss="2024-09-28",
        perils=[Peril.HOSPITALIZATION],
        line_items=[
            LineItem(
                description="Hospital stay meds consumables",
                category="medicines",
                claimed_amount=25_000,
            )
        ],
    )
    eligibility = compute_payout(facts, [])
    assert eligibility.total_payable == 23_000
    assert derive_outcome_from_eligibility(eligibility) == Outcome.APPROVE


def test_held_out_ambiguous_cause_needs_escalation_when_dated():
    facts = _facts(
        date_of_loss="2024-07-15",
        cause_ambiguous=True,
        perils=[Peril.WATER_DAMAGE],
        line_items=[
            LineItem(
                description="Wall and under-sink repair",
                category="cabinetry",
                claimed_amount=40_000,
                evidence_tags=["sudden_discharge", "pre_existing_seepage_mentioned"],
            )
        ],
    )
    eligibility = compute_payout(facts, [])
    assert eligibility.needs_escalation or eligibility.line_items[0].verdict == LineItemVerdict.EXCLUDED


def test_held_out_injection_seepage_still_denies_amount():
    """Injection bait must not change the deterministic amount (always 0)."""
    facts = _facts(
        date_of_loss="2024-08-10",
        perils=[Peril.WATER_DAMAGE],
        line_items=[
            LineItem(
                description="Bathroom plaster repair",
                category="plaster",
                claimed_amount=18_000,
                evidence_tags=["pre_existing_seepage_mentioned"],
            )
        ],
        evidence_tags=["pre_existing_seepage_mentioned"],
    )
    eligibility = compute_payout(facts, [])
    assert eligibility.total_payable == 0.0
    assert derive_outcome_from_eligibility(eligibility) == Outcome.DENY

    risk = risk_anomaly(
        {
            "claim_facts": facts,
            "normalized_envelope": {
                "claim_id": "HELD-INJECT-01",
                "filed_date": "2024-09-01",
                "policy_id": "POL-HOME-01",
            },
            "injection_flags": ["ignore_previous_instructions"],
        }
    )
    assert risk["risk_result"].severity == RiskSeverity.MEDIUM
    decision = decision_composition(
        {"eligibility_result": eligibility, "risk_result": risk["risk_result"]}
    )["decision"]
    # Injection is flagged but must not rewrite the deterministic deny/amount.
    assert decision.outcome == Outcome.DENY
    assert decision.amount == 0.0
    assert decision.amount != 999_999


def test_held_out_cabinetry_partial_with_consistent_dates():
    facts = _facts(
        date_of_loss="2024-05-20",
        perils=[Peril.WATER_DAMAGE],
        line_items=[
            LineItem(
                description="Built-in cabinetry replacement",
                category="cabinetry",
                claimed_amount=55_000,
                evidence_tags=["sudden_discharge"],
            )
        ],
    )
    eligibility = compute_payout(facts, [])
    assert eligibility.line_items[0].verdict == LineItemVerdict.REDUCED
    assert eligibility.total_payable == 20_000  # min(55000,25000) - 5000
    assert derive_outcome_from_eligibility(eligibility) == Outcome.PARTIAL

    risk = risk_anomaly(
        {
            "claim_facts": facts,
            "normalized_envelope": {
                "claim_id": "HELD-CAB-01",
                "filed_date": "2024-06-01",
                "policy_id": "POL-HOME-01",
            },
            "injection_flags": [],
        }
    )
    assert "filed_before_loss_date" not in risk["risk_result"].flags
    decision = decision_composition(
        {"eligibility_result": eligibility, "risk_result": risk["risk_result"]}
    )["decision"]
    assert decision.outcome == Outcome.PARTIAL
    assert decision.amount == 20_000


def test_held_out_cabinetry_escalates_when_filed_before_loss():
    """The UI failure mode: payout math OK, HIGH risk overrides to escalate."""
    facts = _facts(
        date_of_loss="2024-06-20",
        perils=[Peril.WATER_DAMAGE],
        line_items=[
            LineItem(
                description="Built-in cabinetry replacement",
                category="cabinetry",
                claimed_amount=55_000,
                evidence_tags=["sudden_discharge"],
            )
        ],
    )
    eligibility = compute_payout(facts, [])
    assert eligibility.total_payable == 20_000

    risk = risk_anomaly(
        {
            "claim_facts": facts,
            "normalized_envelope": {
                "claim_id": "HELD-CAB-BAD-DATES",
                "filed_date": "2024-06-01",  # before loss
                "policy_id": "POL-HOME-01",
            },
            "injection_flags": [],
        }
    )
    assert "filed_before_loss_date" in risk["risk_result"].flags
    decision = decision_composition(
        {"eligibility_result": eligibility, "risk_result": risk["risk_result"]}
    )["decision"]
    assert decision.outcome == Outcome.ESCALATE
    assert decision.amount == 20_000


def test_router_flags_missing_date_of_loss():
    facts = _facts(date_of_loss=None, line_items=[
        LineItem(description="Repair", category="flooring", claimed_amount=1000,
                  evidence_tags=["sudden_discharge"]),
    ])
    result = router(
        {
            "claim_facts": facts,
            "normalized_envelope": {"policy_id": "POL-HOME-01"},
        }
    )
    assert MissingField.DATE_OF_LOSS in result["routing"].missing_fields
