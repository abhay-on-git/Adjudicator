"""Hand-authored narrative paraphrases used by run_eval.py's phrasing-
consistency check: each variant describes the SAME underlying facts (same
date, peril, sudden/ambiguous framing, amount) as the corresponding fixture
in fixtures/claims/claims.json, just worded differently. A robust pipeline
should reach the same outcome/amount regardless of phrasing; if it doesn't,
that's a real finding about extraction fragility, not noise.

Kept small and curated (3 claims, 2 variants each) rather than every claim,
since each variant costs a real LLM call and this is meant to be a targeted
robustness probe, not an exhaustive one — see EVIDENCE.md for the results
and what they imply.
"""

from __future__ import annotations

# claim_id -> [variant narrative_text, ...]. The claim's OTHER fields
# (policy_id, dates, claimant_*) are reused unchanged from claims.json.
PHRASING_VARIANTS: dict[str, list[str]] = {
    "CLM-001": [
        "This is Priya Nair from Pune. The pipe under my kitchen sink suddenly "
        "burst on July 30, 2024, spraying water for roughly ten minutes before "
        "I managed to shut it off. The flooring near the sink got soaked. I've "
        "got same-day photos and a plumber's estimate. Flooring repair cost is "
        "\u20b98,000. Nothing like this had ever happened before — it came out "
        "of nowhere.",
        "Writing to report a claim. On 30/07/2024, a sudden pipe burst occurred "
        "beneath my kitchen sink at my home in Pune (I'm Priya Nair). Water "
        "sprayed for about ten minutes until I shut the valve, soaking the "
        "nearby flooring. I have photographic evidence from that day plus a "
        "plumber's repair quote of \u20b98,000. There was absolutely no prior "
        "leak — this was completely unexpected.",
    ],
    "CLM-012": [
        "I'm Suresh Pillai from Kochi. On August 10, 2024, the pipe under my "
        "kitchen sink burst all of a sudden and soaked my built-in cabinets. "
        "It was definitely sudden, not a gradual leak. My contractor quoted "
        "\u20b971,500 to replace the cabinetry. I have photos and the estimate "
        "ready.",
        "Reporting a claim: on 10/08/2024 my kitchen sink pipe suddenly burst, "
        "drenching the built-in cabinets. This was a one-off sudden event, not "
        "slow seepage. Cabinetry replacement quote from my contractor is "
        "\u20b971,500, and I have photos plus the estimate to support this.",
    ],
    "CLM-017": [
        "Ritu Bhatia here, from Nagpur. On September 1, 2024 I noticed my "
        "cabinets near the sink were damp and warped. I genuinely can't tell "
        "if this happened suddenly or built up gradually over time since I "
        "rarely check under there. Either is possible. The repair quote for "
        "the cabinetry is \u20b930,000.",
        "This is Ritu Bhatia writing from Nagpur. My under-sink cabinets turned "
        "up damp and warped on 1 September 2024. I'm unsure whether it was a "
        "sudden event or a slow, ongoing issue — could genuinely be either "
        "since I don't inspect that area often. Cabinetry repair estimate is "
        "\u20b930,000.",
    ],
}

# claim_ids run twice with the IDENTICAL narrative (no paraphrasing) to
# measure raw LLM non-determinism (temperature > 0 between identical calls),
# separate from the phrasing-robustness question above. One clean-approve,
# one partial, one injection case — a small but varied sample.
REPEAT_CONSISTENCY_SAMPLE: list[str] = ["CLM-001", "CLM-012", "CLM-024"]
