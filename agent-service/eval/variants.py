"""Irrelevant-variation generators for eval consistency.

Each variant keeps the SAME loss facts (date, peril, cause framing, amounts)
and only changes identity/presentation fields the spec treats as irrelevant:
claimant name, gender, city, phrasing, and the order line items are mentioned.

`PHRASING_VARIANTS` remains the hand-authored paraphrase set (used when a
claim has one). Other variant types are generated deterministically so the
matrix can cover 15 claims without another LLM in the loop.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

# 15 golden-set claims spanning approve / deny / partial / escalate / injection.
# Skips unparseable CLM-023 (no stable name/city/gender to swap) and the two
# missing-info stubs whose narratives barely mention identity.
VARIANT_SAMPLE: list[str] = [
    "CLM-001",  # clean_approve
    "CLM-002",  # clean_approve
    "CLM-003",  # clean_approve
    "CLM-007",  # clean_deny
    "CLM-009",  # clean_deny
    "CLM-010",  # clean_deny
    "CLM-011",  # clean_deny
    "CLM-012",  # partial
    "CLM-013",  # partial (multi line-item)
    "CLM-014",  # partial (multi line-item)
    "CLM-016",  # partial
    "CLM-017",  # escalate_ambiguous
    "CLM-019",  # escalate_multi_peril
    "CLM-024",  # injection_test
    "CLM-025",  # injection_test
]

# Gap 2 — 5–6 claims across the main outcome types.
STABILITY_SAMPLE: list[str] = [
    "CLM-001",  # clean_approve
    "CLM-007",  # clean_deny
    "CLM-012",  # partial
    "CLM-017",  # escalate_ambiguous
    "CLM-019",  # escalate_multi_peril
    "CLM-024",  # injection_test (deny-under-injection)
]

STABILITY_RUNS = 5

FEMALE_NAMES = ["Anjali Iyer", "Kavya Menon", "Shreya Banerjee", "Leela Krishnan"]
MALE_NAMES = ["Vikram Shah", "Rohit Deshpande", "Aditya Banerjee", "Nikhil Kulkarni"]
CITIES = [
    "Jaipur", "Ahmedabad", "Indore", "Bhopal", "Coimbatore",
    "Vadodara", "Mysuru", "Ranchi", "Guwahati", "Kanpur",
]

VARIANT_TYPES = ("name", "gender", "city", "phrasing", "line_item_order")


@dataclass(frozen=True)
class ClaimVariant:
    variant_type: str
    claim: dict


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

# claim_ids run N times with the IDENTICAL narrative (no paraphrasing) to
# measure raw LLM non-determinism (temperature > 0 between identical calls),
# separate from the phrasing-robustness question above.
REPEAT_CONSISTENCY_SAMPLE: list[str] = ["CLM-001", "CLM-012", "CLM-024"]


def _replace_literal(text: str, old: str, new: str) -> str:
    if not old or old == new:
        return text
    return re.sub(re.escape(old), new, text)


def _pick_name(gender: str, current: str) -> str:
    pool = FEMALE_NAMES if gender == "female" else MALE_NAMES
    for name in pool:
        if name != current:
            return name
    return pool[0]


def _pick_city(current: str) -> str:
    for city in CITIES:
        if city.lower() != current.lower():
            return city
    return "Jaipur"


def _swap_gender_tokens(text: str, from_gender: str) -> str:
    """Swap obvious gendered tokens. First-person claims often have none;
    callers also swap the proper name so the narrative still signals gender.
    """
    if from_gender == "female":
        pairs = [
            (r"\bherself\b", "himself"),
            (r"\bHerself\b", "Himself"),
            (r"\bMs\.\b", "Mr."),
            (r"\bMrs\.\b", "Mr."),
            (r"\bshe\b", "he"),
            (r"\bShe\b", "He"),
            (r"\bher\b", "his"),
            (r"\bHer\b", "His"),
            (r"\bwoman\b", "man"),
            (r"\bWoman\b", "Man"),
        ]
    else:
        pairs = [
            (r"\bhimself\b", "herself"),
            (r"\bHimself\b", "Herself"),
            (r"\bMr\.\b", "Ms."),
            (r"\bhe\b", "she"),
            (r"\bHe\b", "She"),
            (r"\bhis\b", "her"),
            (r"\bHis\b", "Her"),
            (r"\bhim\b", "her"),
            (r"\bHim\b", "Her"),
            (r"\bman\b", "woman"),
            (r"\bMan\b", "Woman"),
        ]
    out = text
    for pattern, repl in pairs:
        out = re.sub(pattern, repl, out)
    return out


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


def name_variant(claim: dict) -> ClaimVariant:
    gender = claim.get("claimant_gender") or "female"
    current = claim.get("claimant_name") or ""
    new_name = _pick_name(gender, current)
    cloned = copy.deepcopy(claim)
    cloned["claimant_name"] = new_name
    cloned["narrative_text"] = _replace_literal(cloned["narrative_text"], current, new_name)
    return ClaimVariant("name", cloned)


def gender_variant(claim: dict) -> ClaimVariant:
    current_gender = claim.get("claimant_gender") or "female"
    new_gender = "male" if current_gender == "female" else "female"
    current_name = claim.get("claimant_name") or ""
    new_name = _pick_name(new_gender, current_name)
    cloned = copy.deepcopy(claim)
    cloned["claimant_gender"] = new_gender
    cloned["claimant_name"] = new_name
    narrative = _replace_literal(cloned["narrative_text"], current_name, new_name)
    cloned["narrative_text"] = _swap_gender_tokens(narrative, current_gender)
    return ClaimVariant("gender", cloned)


def city_variant(claim: dict) -> ClaimVariant:
    current = claim.get("claimant_city") or ""
    new_city = _pick_city(current)
    cloned = copy.deepcopy(claim)
    cloned["claimant_city"] = new_city
    cloned["narrative_text"] = _replace_literal(cloned["narrative_text"], current, new_city)
    return ClaimVariant("city", cloned)


def phrasing_variant(claim: dict) -> ClaimVariant:
    claim_id = claim["claim_id"]
    cloned = copy.deepcopy(claim)
    authored = PHRASING_VARIANTS.get(claim_id)
    if authored:
        cloned["narrative_text"] = authored[0]
    else:
        sentences = _split_sentences(cloned["narrative_text"])
        if len(sentences) > 1:
            cloned["narrative_text"] = " ".join(sentences[1:] + sentences[:1])
        else:
            cloned["narrative_text"] = f"Claim report. {cloned['narrative_text']}"
    return ClaimVariant("phrasing", cloned)


def line_item_order_variant(claim: dict) -> ClaimVariant:
    """Reverse sentence order so mentioned amounts/items appear in reverse.

    Single-sentence claims cannot reorder items; we still emit a variant so
    every type is represented.
    """
    cloned = copy.deepcopy(claim)
    sentences = _split_sentences(cloned["narrative_text"])
    if len(sentences) > 1:
        cloned["narrative_text"] = " ".join(reversed(sentences))
    return ClaimVariant("line_item_order", cloned)


def build_variants(claim: dict) -> list[ClaimVariant]:
    return [
        name_variant(claim),
        gender_variant(claim),
        city_variant(claim),
        phrasing_variant(claim),
        line_item_order_variant(claim),
    ]
