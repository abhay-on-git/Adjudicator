"""Generates ground_truth.json from gold_facts.py by running the actual
compute_payout engine (not hand-typed numbers) — see gold_facts.py docstring.

Run: .venv\\Scripts\\python.exe fixtures\\claims\\generate_ground_truth.py
(from the agent-service directory, so `graph`/`rules` resolve as packages.)
"""

from __future__ import annotations

import json
from pathlib import Path

from fixtures.claims.gold_facts import GOLD_FACTS
from graph.schemas import ClaimFacts
from rules.payout import compute_payout, derive_outcome_from_eligibility

CLAIMS_FILE = Path(__file__).resolve().parent / "claims.json"
OUTPUT_FILE = Path(__file__).resolve().parent / "ground_truth.json"

# Claims with no well-defined gold facts by design (see gold_facts.py) —
# ground truth is written directly rather than derived from compute_payout.
DIRECT_ESCALATIONS = {
    "CLM-020": "Claim narrative gives no date, no peril, and no line items — "
    "missing_fields should route straight to escalation without ever "
    "reaching eligibility_evaluation.",
    "CLM-021": "No hospitalization dates and no itemized bill available yet — "
    "missing_fields should route straight to escalation.",
    "CLM-022": "Flood/river-overflow damage is not covered by any clause in "
    "POL-HOME-01 — search_policy should return zero relevant clauses, "
    "triggering the 'no governing policy found' degradation path.",
    "CLM-023": "Narrative is not parseable into a coherent claim — extraction "
    "should fail schema validation (or produce facts too incomplete to "
    "adjudicate) and escalate with 'extraction failed'.",
}


def main() -> None:
    claims = json.loads(CLAIMS_FILE.read_text(encoding="utf-8"))
    claim_types = {c["claim_id"]: c["claim_type"] for c in claims}

    ground_truth: dict[str, dict] = {}

    for claim_id, facts_kwargs in GOLD_FACTS.items():
        facts = ClaimFacts(**facts_kwargs)
        result = compute_payout(facts, clauses=[])
        outcome = derive_outcome_from_eligibility(result)
        ground_truth[claim_id] = {
            "claim_type": claim_types.get(claim_id),
            "expected_outcome": outcome.value,
            "expected_amount": None if outcome.value == "escalate" else result.total_payable,
            "expected_clauses": result.clauses_used,
            "expected_escalation_reason": result.escalation_reason,
        }

    for claim_id, reason in DIRECT_ESCALATIONS.items():
        ground_truth[claim_id] = {
            "claim_type": claim_types.get(claim_id),
            "expected_outcome": "escalate",
            "expected_amount": None,
            "expected_clauses": [],
            "expected_escalation_reason": reason,
        }

    # Sanity check: every claim in claims.json has ground truth, and vice versa.
    missing_gt = set(claim_types) - set(ground_truth)
    extra_gt = set(ground_truth) - set(claim_types)
    if missing_gt or extra_gt:
        raise SystemExit(f"Mismatch between claims.json and ground truth. Missing: {missing_gt}, Extra: {extra_gt}")

    ordered = {cid: ground_truth[cid] for cid in sorted(ground_truth, key=lambda x: int(x.split("-")[1]))}
    OUTPUT_FILE.write_text(json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(ordered)} ground-truth entries to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
