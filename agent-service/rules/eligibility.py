"""Deterministic per-line-item eligibility logic, one function per policy.

ZERO LLM IMPORTS IN THIS FILE. This is enforced by test_adversarial_llm_amount.py
which asserts `openai` / `langchain*` / `mcp_client` never appear in this
module's or payout.py's import graph. See DESIGN.md, "The non-negotiable rule".

Design note (see DESIGN.md fork "structured rule table vs. NLP-parsed clause
prose"): each function below encodes a policy's clauses as code, cross-
referenced by clause_id to the fixture markdown in fixtures/policies/. It does
NOT parse clause text at runtime — the clause text is what's retrieved and
cited for the *human-facing* explanation/evidence panel, while the numbers
this engine actually runs on are authored directly against the same clause
IDs. `test_rule_table_matches_fixtures.py` guards against the table and the
fixture text drifting apart by asserting every clause_id referenced below
actually exists in the loaded policy index.

Design note (see DESIGN.md fork "evidence tags vs. LLM-resolved coverage"):
every branch below reads only `evidence_tags` / `cause_ambiguous` (facts about
what the narrative says) plus claim-structural fields (dates, amounts,
categories). No branch asks "was this covered?" of an LLM output — that
question is answered entirely here.
"""

from __future__ import annotations

from datetime import date

from graph.schemas import ClaimFacts, LineItem, LineItemEligibility, LineItemVerdict, Peril


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _days_between(later: str | None, earlier: str | None) -> int | None:
    d_later, d_earlier = _parse_date(later), _parse_date(earlier)
    if d_later is None or d_earlier is None:
        return None
    return (d_later - d_earlier).days


def resolve_item_peril(item: LineItem, facts: ClaimFacts) -> Peril | None:
    """Returns the line item's own peril, inferring from its category/tags/description
    only if not explicitly set on the item."""
    if item.peril is not None:
        return item.peril

    item_cat = (item.category or "").lower()
    item_desc = (item.description or "").lower()
    tags = set(item.evidence_tags)

    theft_keywords = {
        "jewellery", "jewelry", "valuables", "watch", "watches",
        "jewelry_and_electronics", "electronics", "gadgets", "theft",
        "stolen", "burglary", "break-in"
    }
    if (
        item_cat in theft_keywords
        or any(k in item_cat for k in ("theft", "jewel", "valuab", "watch", "electron"))
        or any(k in item_desc for k in ("stolen", "theft", "break-in", "burglar", "robbery"))
        or "forcible_entry_evidence" in tags
    ):
        return Peril.THEFT

    water_keywords = {"cabinetry", "fixed_furniture", "countertop", "flooring", "plumbing", "water_damage"}
    if (
        item_cat in water_keywords
        or "sudden_discharge" in tags
        or "pre_existing_seepage_mentioned" in tags
        or any(k in item_desc for k in ("water", "pipe", "plumb", "leak", "seep", "seepage", "overflow", "dampness"))
    ):
        return Peril.WATER_DAMAGE

    if "fire" in item_cat or any(k in item_desc for k in ("fire", "burn", "scorch", "smoke")):
        return Peril.FIRE

    if len(facts.perils) == 1:
        return facts.perils[0]

    return None


# ---------------------------------------------------------------------------
# POL-HOME-01
# ---------------------------------------------------------------------------

HOME_DEDUCTIBLE = 5000.0
HOME_DEDUCTIBLE_CLAUSE = "§2.3"
HOME_CABINETRY_CATEGORIES = {"cabinetry", "fixed_furniture", "countertop"}
HOME_VALUABLES_CATEGORIES = {
    "jewellery", "jewelry", "valuables", "watch", "watches",
    "jewelry_and_electronics", "electronics", "gadgets",
}
# POL-HOME-01 covers plumbing discharge / fire / forcible-entry theft — not
# river/flash flood. Live extractors often mis-tag flood as water_damage +
# sudden_discharge; catch that before the plumbing path wrongly allows it.
_HOME_FLOOD_MARKERS = (
    "flood",
    "flash flood",
    "river overflow",
    "overflowed",
    "flooding",
)


def home_flood_or_overflow_indicated(facts: ClaimFacts, item: LineItem | None = None) -> bool:
    """True when the claim describes uncovered flood/overflow loss."""
    tags: set[str] = set(facts.evidence_tags)
    parts = [facts.narrative_summary or ""]
    items = [item] if item is not None else list(facts.line_items)
    for li in items:
        if li is None:
            continue
        tags.update(li.evidence_tags)
        parts.append(li.description)
        parts.append(li.category)
    if "flood_or_overflow_mentioned" in tags:
        return True
    blob = " ".join(parts).lower()
    return any(marker in blob for marker in _HOME_FLOOD_MARKERS)


def evaluate_line_item_home(item: LineItem, facts: ClaimFacts) -> LineItemEligibility:
    tags = set(item.evidence_tags) | set(facts.evidence_tags)
    item_peril = resolve_item_peril(item, facts)

    if home_flood_or_overflow_indicated(facts, item):
        return LineItemEligibility(
            description=item.description,
            claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.EXCLUDED,
            allowed_amount=0.0,
            governing_clause_ids=[],
            reason="Flood / river-overflow damage is not a covered peril under "
            "POL-HOME-01 (no governing clause); plumbing-discharge cover at "
            "§4.2.1 does not apply.",
        )

    if "intentional_damage_mentioned" in tags:
        return LineItemEligibility(
            description=item.description,
            claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.EXCLUDED,
            allowed_amount=0.0,
            governing_clause_ids=["§7.2"],
            reason="Intentional damage is excluded in full under §7.2.",
        )

    days_since_start = _days_between(facts.date_of_loss, facts.policy_start_date)
    is_item_fire = (item_peril == Peril.FIRE)
    if not is_item_fire and days_since_start is not None and days_since_start < 15:
        return LineItemEligibility(
            description=item.description,
            claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.EXCLUDED,
            allowed_amount=0.0,
            governing_clause_ids=["§3.1.1"],
            reason=f"Loss occurred {days_since_start} days after policy start; the "
            "15-day general waiting period (§3.1.1) applies to non-fire perils.",
        )

    # 1. Fire path (§4.1)
    if item_peril == Peril.FIRE:
        return LineItemEligibility(
            description=item.description,
            claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.ALLOWED,
            allowed_amount=item.claimed_amount,
            governing_clause_ids=["§4.1"],
            reason="Fire damage is covered in full under §4.1.",
        )

    # 2. Theft / burglary path (§5.1, §5.2, §7.3)
    item_tags = set(item.evidence_tags)
    is_theft = (
        item_peril in (Peril.THEFT, Peril.MOTOR_THEFT)
        or item.category in HOME_VALUABLES_CATEGORIES
        or any(k in item.category.lower() for k in ("theft", "jewel", "valuab", "watch", "electron"))
        or any(k in item.description.lower() for k in ("stolen", "theft", "break-in", "burglar", "robbery"))
        or "forcible_entry_evidence" in item_tags
    )
    if is_theft:
        if "forcible_entry_evidence" in tags:
            is_valuable = (
                item.category in HOME_VALUABLES_CATEGORIES
                or any(k in item.category.lower() for k in ("jewel", "valuab", "watch", "electron"))
                or any(k in item.description.lower() for k in ("jewel", "valuab", "watch", "electron", "laptop", "gold", "silver"))
            )
            if is_valuable:
                allowed = min(item.claimed_amount, 100_000.0)
                verdict = LineItemVerdict.ALLOWED if allowed == item.claimed_amount else LineItemVerdict.REDUCED
                return LineItemEligibility(
                    description=item.description,
                    claimed_amount=item.claimed_amount,
                    verdict=verdict,
                    allowed_amount=allowed,
                    governing_clause_ids=["§5.1", "§5.2"],
                    reason="Theft with evidence of forcible entry is covered under §5.1, "
                    "capped at ₹1,00,000 in aggregate for valuables under §5.2.",
                )
            return LineItemEligibility(
                description=item.description,
                claimed_amount=item.claimed_amount,
                verdict=LineItemVerdict.ALLOWED,
                allowed_amount=item.claimed_amount,
                governing_clause_ids=["§5.1"],
                reason="Theft of contents with evidence of forcible entry is covered in full under §5.1.",
            )
        return LineItemEligibility(
            description=item.description,
            claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.EXCLUDED,
            allowed_amount=0.0,
            governing_clause_ids=["§7.3"],
            reason="No evidence of forcible entry; theft without forcible entry is "
            "excluded under §7.3 (see §5.1).",
        )

    # 3. Water damage / cabinetry path — the required exclusion-gates-sub-limit interaction.
    # Scoped to THIS item's own category/tags/peril, not the claim's overall peril list.
    has_seepage_tag = "pre_existing_seepage_mentioned" in tags
    has_sudden_tag = "sudden_discharge" in tags
    is_water = (
        item_peril == Peril.WATER_DAMAGE
        or item.category in HOME_CABINETRY_CATEGORIES
        or "pre_existing_seepage_mentioned" in item_tags
        or "sudden_discharge" in item_tags
        or (has_seepage_tag and item_peril is None)
        or (has_sudden_tag and item_peril is None)
    )
    if is_water:
        if facts.cause_ambiguous or (has_seepage_tag and has_sudden_tag):
            return LineItemEligibility(
                description=item.description,
                claimed_amount=item.claimed_amount,
                verdict=LineItemVerdict.EXCLUDED,
                allowed_amount=0.0,
                governing_clause_ids=["§7.1.4"],
                reason="The narrative does not clearly distinguish a sudden plumbing "
                "discharge (§4.2.1) from pre-existing seepage (§7.1.4); §7.1.4 "
                "requires escalation rather than a guessed outcome for this item.",
            )

        if has_seepage_tag:
            # Exclusion applies -> sub-limit (§4.2.9) never even considered, per its own text.
            return LineItemEligibility(
                description=item.description,
                claimed_amount=item.claimed_amount,
                verdict=LineItemVerdict.EXCLUDED,
                allowed_amount=0.0,
                governing_clause_ids=["§7.1.4"],
                reason="Pre-existing seepage / gradual deterioration is excluded in "
                "full under §7.1.4, regardless of the §4.2.9 cabinetry sub-limit.",
            )

        # Not excluded -> covered under §4.2.1, and the cabinetry sub-limit (§4.2.9)
        # is now in scope precisely BECAUSE the exclusion above did not apply.
        if item.category in HOME_CABINETRY_CATEGORIES:
            allowed = min(item.claimed_amount, 25_000.0)
            verdict = LineItemVerdict.ALLOWED if allowed == item.claimed_amount else LineItemVerdict.REDUCED
            return LineItemEligibility(
                description=item.description,
                claimed_amount=item.claimed_amount,
                verdict=verdict,
                allowed_amount=allowed,
                governing_clause_ids=["§4.2.1", "§4.2.9"],
                reason="Sudden plumbing discharge is covered under §4.2.1; cabinetry "
                "repair is capped at ₹25,000 under §4.2.9.",
            )
        return LineItemEligibility(
            description=item.description,
            claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.ALLOWED,
            allowed_amount=item.claimed_amount,
            governing_clause_ids=["§4.2.1"],
            reason="Sudden and accidental discharge from plumbing is covered in full "
            "under §4.2.1.",
        )

    return LineItemEligibility(
        description=item.description,
        claimed_amount=item.claimed_amount,
        verdict=LineItemVerdict.EXCLUDED,
        allowed_amount=0.0,
        governing_clause_ids=[],
        reason="No matching coverage clause was found for this item's category/peril.",
    )


# ---------------------------------------------------------------------------
# POL-HEALTH-01
# ---------------------------------------------------------------------------

HEALTH_DEDUCTIBLE = 2000.0
HEALTH_DEDUCTIBLE_CLAUSE = "§2.1"
HEALTH_WAITING_PERIOD_DAYS = 24 * 30  # 24 months, approximated in days


def evaluate_line_item_health(item: LineItem, facts: ClaimFacts) -> LineItemEligibility:
    tags = set(item.evidence_tags) | set(facts.evidence_tags)

    if "cosmetic_procedure_mentioned" in tags:
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.EXCLUDED, allowed_amount=0.0,
            governing_clause_ids=["§5.1"],
            reason="Cosmetic procedures are excluded in full under §5.1.",
        )
    if "self_inflicted_injury_mentioned" in tags:
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.EXCLUDED, allowed_amount=0.0,
            governing_clause_ids=["§5.2"],
            reason="Self-inflicted injury is excluded in full under §5.2.",
        )

    days_since_start = _days_between(facts.date_of_loss, facts.policy_start_date)
    pre_existing = "pre_existing_condition_mentioned" in tags
    accidental_waiver = "accidental_injury" in tags

    if pre_existing and not accidental_waiver and days_since_start is not None and days_since_start < HEALTH_WAITING_PERIOD_DAYS:
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.EXCLUDED, allowed_amount=0.0,
            governing_clause_ids=["§3.1.2"],
            reason=f"Pre-existing condition, {days_since_start} days into the policy; "
            "the 24-month waiting period (§3.1.2) has not elapsed and no accidental-"
            "injury waiver (§3.1.5) applies.",
        )

    clause_ids = ["§4.1"]
    if pre_existing and accidental_waiver:
        clause_ids.append("§3.1.5")

    if item.category == "room_rent":
        per_day_cap = 5000.0
        allowed = min(item.claimed_amount, per_day_cap * max(item.quantity, 1))
        verdict = LineItemVerdict.ALLOWED if allowed == item.claimed_amount else LineItemVerdict.REDUCED
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=verdict, allowed_amount=allowed,
            governing_clause_ids=clause_ids + ["§4.3"],
            reason="Room rent is capped at ₹5,000/day under §4.3.",
        )
    if item.category == "surgery":
        allowed = min(item.claimed_amount, 150_000.0)
        verdict = LineItemVerdict.ALLOWED if allowed == item.claimed_amount else LineItemVerdict.REDUCED
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=verdict, allowed_amount=allowed,
            governing_clause_ids=clause_ids + ["§4.4"],
            reason="Surgical procedures are capped at ₹1,50,000 per claim under §4.4.",
        )
    if item.category == "diagnostics":
        allowed = min(item.claimed_amount, 20_000.0)
        verdict = LineItemVerdict.ALLOWED if allowed == item.claimed_amount else LineItemVerdict.REDUCED
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=verdict, allowed_amount=allowed,
            governing_clause_ids=clause_ids + ["§4.4"],
            reason="Diagnostics/scans are capped at ₹20,000 per claim under §4.4.",
        )
    # medicines / other -> no sub-limit beyond overall coverage
    return LineItemEligibility(
        description=item.description, claimed_amount=item.claimed_amount,
        verdict=LineItemVerdict.ALLOWED, allowed_amount=item.claimed_amount,
        governing_clause_ids=clause_ids,
        reason="Hospitalization expense covered in full under §4.1 with no "
        "category-specific sub-limit.",
    )


# ---------------------------------------------------------------------------
# POL-MOTOR-01
# ---------------------------------------------------------------------------

MOTOR_DEDUCTIBLE = 1000.0
MOTOR_DEDUCTIBLE_CLAUSE = "§2.1"


def evaluate_line_item_motor(item: LineItem, facts: ClaimFacts) -> LineItemEligibility:
    tags = set(item.evidence_tags) | set(facts.evidence_tags)

    for tag, clause in (
        ("driving_under_influence_mentioned", "§4.1"),
        ("no_valid_license_mentioned", "§4.2"),
        ("racing_mentioned", "§4.3"),
    ):
        if tag in tags:
            return LineItemEligibility(
                description=item.description, claimed_amount=item.claimed_amount,
                verdict=LineItemVerdict.EXCLUDED, allowed_amount=0.0,
                governing_clause_ids=[clause],
                reason=f"Excluded under {clause}.",
            )

    if item.category == "theft":
        if "police_report_filed" not in tags:
            return LineItemEligibility(
                description=item.description, claimed_amount=item.claimed_amount,
                verdict=LineItemVerdict.EXCLUDED, allowed_amount=0.0,
                governing_clause_ids=["§6.1"],
                reason="No police report (FIR) evidenced; §6.1 requires one for theft "
                "claims — flagged for escalation rather than auto-denial.",
            )
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.ALLOWED, allowed_amount=item.claimed_amount,
            governing_clause_ids=["§3.2"],
            reason="Total theft with a filed police report is covered under §3.2.",
        )

    if item.category == "accessory":
        allowed = min(item.claimed_amount, 15_000.0)
        verdict = LineItemVerdict.ALLOWED if allowed == item.claimed_amount else LineItemVerdict.REDUCED
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=verdict, allowed_amount=allowed,
            governing_clause_ids=["§3.1", "§3.3"],
            reason="Non-standard accessories are capped at ₹15,000 under §3.3.",
        )

    return LineItemEligibility(
        description=item.description, claimed_amount=item.claimed_amount,
        verdict=LineItemVerdict.ALLOWED, allowed_amount=item.claimed_amount,
        governing_clause_ids=["§3.1"],
        reason="Accidental damage is covered under §3.1.",
    )


# ---------------------------------------------------------------------------
# POL-TRAVEL-01
# ---------------------------------------------------------------------------

TRAVEL_DEDUCTIBLE = 1500.0
TRAVEL_DEDUCTIBLE_CLAUSE = "§2.1"


def evaluate_line_item_travel(item: LineItem, facts: ClaimFacts) -> LineItemEligibility:
    tags = set(item.evidence_tags) | set(facts.evidence_tags)

    days_since_start = _days_between(facts.date_of_loss, facts.policy_start_date)
    if (
        "named_partner_booking" not in tags
        and days_since_start is not None
        and days_since_start < 2
    ):
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.EXCLUDED, allowed_amount=0.0,
            governing_clause_ids=["§3.1.1"],
            reason="Loss occurred within the 48-hour activation period (§3.1.1) and "
            "no named-partner booking waiver (§3.1.3) applies.",
        )

    if item.category == "trip_cancellation":
        if "known_risk_before_booking" in tags:
            return LineItemEligibility(
                description=item.description, claimed_amount=item.claimed_amount,
                verdict=LineItemVerdict.EXCLUDED, allowed_amount=0.0,
                governing_clause_ids=["§5.1"],
                reason="The cancelling event was publicly known before booking; "
                "excluded under §5.1.",
            )
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.ALLOWED, allowed_amount=item.claimed_amount,
            governing_clause_ids=["§4.1"],
            reason="Trip cancellation for a covered reason is covered under §4.1.",
        )

    if item.category == "baggage":
        allowed = min(item.claimed_amount, 5_000.0)
        verdict = LineItemVerdict.ALLOWED if allowed == item.claimed_amount else LineItemVerdict.REDUCED
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=verdict, allowed_amount=allowed,
            governing_clause_ids=["§4.2", "§4.4"],
            reason="Baggage loss/delay is covered under §4.2, capped at ₹5,000 per "
            "single item under §4.4.",
        )

    if item.category == "medical_abroad":
        if "adventure_sports_mentioned" in tags:
            return LineItemEligibility(
                description=item.description, claimed_amount=item.claimed_amount,
                verdict=LineItemVerdict.EXCLUDED, allowed_amount=0.0,
                governing_clause_ids=["§5.2"],
                reason="Injury from undeclared adventure sports is excluded under §5.2.",
            )
        return LineItemEligibility(
            description=item.description, claimed_amount=item.claimed_amount,
            verdict=LineItemVerdict.ALLOWED, allowed_amount=item.claimed_amount,
            governing_clause_ids=["§4.3"],
            reason="Emergency medical treatment abroad is covered under §4.3.",
        )

    return LineItemEligibility(
        description=item.description, claimed_amount=item.claimed_amount,
        verdict=LineItemVerdict.EXCLUDED, allowed_amount=0.0,
        governing_clause_ids=[],
        reason="No matching coverage clause was found for this item's category.",
    )


POLICY_EVALUATORS = {
    "POL-HOME-01": (evaluate_line_item_home, HOME_DEDUCTIBLE, HOME_DEDUCTIBLE_CLAUSE),
    "POL-HEALTH-01": (evaluate_line_item_health, HEALTH_DEDUCTIBLE, HEALTH_DEDUCTIBLE_CLAUSE),
    "POL-MOTOR-01": (evaluate_line_item_motor, MOTOR_DEDUCTIBLE, MOTOR_DEDUCTIBLE_CLAUSE),
    "POL-TRAVEL-01": (evaluate_line_item_travel, TRAVEL_DEDUCTIBLE, TRAVEL_DEDUCTIBLE_CLAUSE),
}
