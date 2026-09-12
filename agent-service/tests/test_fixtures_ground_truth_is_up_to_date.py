"""Regenerating ground_truth.json from gold_facts.py + the current rules engine
must reproduce the checked-in file exactly. If this fails, someone edited
gold_facts.py or rules/eligibility.py|payout.py without re-running
fixtures/claims/generate_ground_truth.py — regenerate and re-commit."""

import json
from pathlib import Path

from fixtures.claims.gold_facts import GOLD_FACTS
from graph.schemas import ClaimFacts
from rules.payout import compute_payout, derive_outcome_from_eligibility

GROUND_TRUTH_FILE = Path(__file__).resolve().parent.parent / "fixtures" / "claims" / "ground_truth.json"


def test_ground_truth_matches_current_rules_engine():
    checked_in = json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))
    for claim_id, facts_kwargs in GOLD_FACTS.items():
        facts = ClaimFacts(**facts_kwargs)
        result = compute_payout(facts, clauses=[])
        outcome = derive_outcome_from_eligibility(result)
        expected_amount = None if outcome.value == "escalate" else result.total_payable

        entry = checked_in[claim_id]
        assert entry["expected_outcome"] == outcome.value, f"{claim_id} outcome drifted"
        assert entry["expected_amount"] == expected_amount, f"{claim_id} amount drifted"
        assert entry["expected_clauses"] == result.clauses_used, f"{claim_id} clauses drifted"
