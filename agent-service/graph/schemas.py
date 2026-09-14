"""Typed schemas shared across the graph.

READ THIS BEFORE ADDING A FIELD TO `ClaimFacts` OR ANY OTHER LLM-FACING MODEL:

The non-negotiable rule (see DESIGN.md / master instructions) is that the LLM
never produces a decision (approve/deny/partial/escalate) or a payout figure.
`ExtractedNarrativeFacts` below is the *only* schema an LLM is allowed to
populate via structured output (`ClaimFacts` = that output plus fields merged
in deterministically by code — see its docstring). It has:
  - `line_items[].claimed_amount` — the amount the CLAIMANT says they lost, as
    stated in their narrative. This is an extracted FACT about the claim, not
    a decision. It flows into `compute_payout` as an input, never as an output.
  - `evidence_tags` / `cause_ambiguous` — facts about what the narrative TEXT
    says or fails to resolve, not coverage verdicts. The deterministic rule
    tables in rules/eligibility.py decide what a tag means; the extractor only
    ever reports "the text asserts X" or "the text doesn't resolve X".
  - No `decision`, `outcome`, `approved_amount`, `payout`, or similarly-named
    field anywhere. If you are about to add one: stop, this is the exact
    design smell called out in the master instructions — flag it instead.

`EligibilityResult`, `RiskResult`, `Decision`, and `PayoutResult` are all
produced by deterministic Python (see rules/payout.py, rules/eligibility.py,
graph/nodes/decision.py) and are never LLM structured-output targets.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Peril(str, Enum):
    FIRE = "fire"
    WATER_DAMAGE = "water_damage"
    THEFT = "theft"
    HOSPITALIZATION = "hospitalization"
    ACCIDENTAL_INJURY = "accidental_injury"
    MOTOR_ACCIDENT = "motor_accident"
    MOTOR_THEFT = "motor_theft"
    TRIP_CANCELLATION = "trip_cancellation"
    BAGGAGE_LOSS = "baggage_loss"
    MEDICAL_ABROAD = "medical_abroad"
    OTHER = "other"


class LineItem(BaseModel):
    """A single claimed item/expense, as extracted from the claimant's own
    narrative. `claimed_amount` is what the claimant says, not what is owed —
    it is a fact to be checked against policy rules downstream, not a
    pre-approved figure."""

    description: str
    category: str = Field(
        description="Free-text category the extractor infers, e.g. 'cabinetry', "
        "'room_rent', 'accessory'. Used by eligibility_evaluation to find the "
        "matching sub-limit clause; does not itself carry a coverage verdict."
    )
    claimed_amount: float = Field(ge=0)
    quantity: float = Field(
        default=1,
        description="Units the claimed_amount is denominated over where relevant "
        "(e.g. nights for room_rent). Defaults to 1 (i.e. claimed_amount is already "
        "the total) when not applicable.",
    )
    evidence_tags: list[str] = Field(
        default_factory=list,
        description="Controlled-vocabulary tags naming what the NARRATIVE says about "
        "this item's cause (e.g. 'sudden_discharge', 'pre_existing_seepage_mentioned', "
        "'forcible_entry_evidence'). A tag records that the text asserts something — "
        "it is a fact-about-the-text, not a coverage verdict. The deterministic rule "
        "table (rules/eligibility.py) maps tags to exclusion/sub-limit/waiver outcomes; "
        "the extractor never resolves the coverage question itself. See "
        "DESIGN.md fork 'evidence tags vs. LLM-resolved coverage'.",
    )
    peril: Peril | None = Field(
        default=None,
        description="The specific peril governing this line item (e.g. 'fire', 'theft', 'water_damage'). "
        "In multi-peril claims, each line item must specify its own peril.",
    )


class ExtractedNarrativeFacts(BaseModel):
    """The ACTUAL structured-output schema passed to the LLM. Deliberately
    excludes `policy_id` and `policy_start_date`: those are given structurally
    by the claim submission form (like a real intake form's policy-number
    field), not narrated in free text, so there is no reason to let the LLM
    touch the single most decision-critical identifier at all. The extraction
    node merges this LLM output with the structural fields into `ClaimFacts`
    below — see DESIGN.md fork 'what the LLM extracts vs. what's given
    structurally'."""

    # extra="forbid": if the LLM's structured-output JSON contains a field this
    # schema doesn't declare (e.g. a smuggled "decision" or "suggested_amount"),
    # pydantic raises ValidationError at parse time rather than silently
    # dropping or accepting it. This is one of the structural barriers the
    # adversarial test in tests/test_adversarial_llm_amount.py exercises.
    model_config = ConfigDict(extra="forbid")

    date_of_loss: str | None = Field(
        default=None, description="ISO date if extractable, else None -> missing_fields flag."
    )
    perils: list[Peril] = Field(default_factory=list)
    line_items: list[LineItem] = Field(default_factory=list)
    narrative_summary: str = Field(
        description="A short, neutral paraphrase of the claimant's account. Must not "
        "contain the extractor's own opinion on coverage or an outcome."
    )
    documents_mentioned: list[str] = Field(default_factory=list)
    claimant_stated_prior_claims: bool | None = None
    evidence_tags: list[str] = Field(
        default_factory=list,
        description="Claim-wide evidence tags (not tied to one line item), e.g. "
        "'accidental_injury', 'pre_existing_condition_mentioned', "
        "'named_partner_booking'. Same fact-not-verdict contract as LineItem.evidence_tags.",
    )
    cause_ambiguous: bool = Field(
        default=False,
        description="True when the narrative itself does not clearly distinguish between "
        "two possible causes that would be treated differently under the policy (e.g. "
        "sudden plumbing discharge vs. long-term seepage). This is a fact about the "
        "TEXT's clarity, not a judgment call on coverage — the extractor is reporting "
        "'the claimant's own words don't resolve this', which several policies (e.g. "
        "POL-HOME-01 §7.1.4) explicitly say must escalate rather than be guessed.",
    )


class ExplanationOutput(BaseModel):
    """The second (and last) LLM structured-output schema. Same contract as
    ExtractedNarrativeFacts: no `decision`/`outcome`/`amount`-shaped field.
    The LLM is given the ALREADY-COMPUTED outcome/amount as read-only context
    to explain in plain language — it cannot change what it's explaining,
    only how it's phrased and which clause IDs it points at."""

    model_config = ConfigDict(extra="forbid")

    narrative: str = Field(description="Plain-language explanation of the decision, citing clause_ids inline.")
    cited_clause_ids: list[str] = Field(
        description="Every clause_id the narrative actually references, for the groundedness check "
        "to verify against retrieved_clauses. Must not include a clause_id not truly cited in the text."
    )


class ClaimFacts(ExtractedNarrativeFacts):
    """Full claim facts used downstream: the LLM's `ExtractedNarrativeFacts`
    plus the structural fields the extraction node merges in deterministically
    (copied from the submission, never asked of the LLM). This is still not a
    decision/amount-bearing schema — see module docstring."""

    policy_id: str
    policy_start_date: str


class MissingField(str, Enum):
    POLICY_ID = "policy_id"
    DATE_OF_LOSS = "date_of_loss"
    PERIL = "peril"
    LINE_ITEMS = "line_items"


class ClaimComplexity(str, Enum):
    SIMPLE = "simple"
    STANDARD = "standard"
    COMPLEX = "complex"


class RoutingDecision(BaseModel):
    """Pure-code output of the router node. Never LLM-populated."""

    complexity: ClaimComplexity
    is_multi_peril: bool
    fast_path: bool
    missing_fields: list[MissingField] = Field(default_factory=list)
    policy_valid: bool


class ClauseRef(BaseModel):
    """A retrieved clause, always carrying the clause_id every explanation
    must cite. Produced by the deterministic `search_policy` MCP tool."""

    policy_id: str
    clause_id: str
    title: str
    text: str
    relevance_score: float


class LineItemVerdict(str, Enum):
    ALLOWED = "allowed"
    REDUCED = "reduced"
    EXCLUDED = "excluded"


class LineItemEligibility(BaseModel):
    description: str
    claimed_amount: float
    verdict: LineItemVerdict
    allowed_amount: float
    governing_clause_ids: list[str]
    reason: str


class EligibilityResult(BaseModel):
    """Deterministic output of compute_payout via the MCP tool. Never touched
    by an LLM. `total_payable` here is the number-of-record used downstream."""

    line_items: list[LineItemEligibility]
    deductible_applied: float
    total_claimed: float
    total_payable: float
    clauses_used: list[str]
    needs_escalation: bool = False
    escalation_reason: str | None = None


class RiskSeverity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskResult(BaseModel):
    """Deterministic output of the risk_anomaly node. Never LLM-populated."""

    severity: RiskSeverity
    flags: list[str] = Field(default_factory=list)
    duplicate_claim_ids: list[str] = Field(default_factory=list)


class Outcome(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    PARTIAL = "partial"
    ESCALATE = "escalate"


class Decision(BaseModel):
    """Deterministic output of decision_composition. The `amount` field is
    copied verbatim from EligibilityResult.total_payable — this node may only
    downgrade an outcome to ESCALATE based on risk/confidence, never touch
    the figure."""

    outcome: Outcome
    amount: float
    confidence: float = Field(ge=0.0, le=1.0)
    escalation_reason: str | None = None


class AuditEventType(str, Enum):
    INTAKE_COMPLETE = "intake_complete"
    EXTRACTION_COMPLETE = "extraction_complete"
    EXTRACTION_RETRY = "extraction_retry"
    ROUTING_COMPLETE = "routing_complete"
    RETRIEVAL_COMPLETE = "retrieval_complete"
    RETRIEVAL_EMPTY = "retrieval_empty"
    ELIGIBILITY_COMPLETE = "eligibility_complete"
    EVIDENCE_RECONCILED = "evidence_reconciled"
    EVIDENCE_RECONCILIATION_FAILED = "evidence_reconciliation_failed"
    RISK_COMPLETE = "risk_complete"
    DECISION_COMPOSED = "decision_composed"
    ESCALATED = "escalated"
    ESCALATION_RESOLVED = "escalation_resolved"
    LLM_RETRY = "llm_retry"
    LLM_UNAVAILABLE = "llm_unavailable"
    EXPLANATION_COMPLETE = "explanation_complete"
    GROUNDEDNESS_VIOLATION = "groundedness_violation"
    CONTEXT_BUDGET_DROP = "context_budget_drop"
    UI_COMPOSED = "ui_composed"
    DECISION_CONFIRMED = "decision_confirmed"
    DECISION_OVERRIDDEN = "decision_overridden"
    DECISION_DOCUMENTS_REQUESTED = "decision_documents_requested"
    REVIEW_FLAGGED = "review_flagged"


class AuditEvent(BaseModel):
    event_type: AuditEventType
    node: str
    detail: str
    timestamp: str
    # Structured override metadata. These stay on the audit event rather than
    # Decision so a human's proposed figure can never become payout-of-record.
    original_outcome: Outcome | None = None
    original_amount: float | None = None
    override_reason: str | None = None
    override_proposed_amount: int | None = Field(default=None, ge=0)


class ReviewFlagResult(BaseModel):
    flag_id: str
    claim_id: str
    reason: str = Field(min_length=1)
    flagged_at: str


# ---------------------------------------------------------------------------
# UI composition — the structured decision spec React renders from.
#
# ui_composition is pure code (see its module docstring), so on the backend
# these block types are the only ones that can ever be emitted, by
# construction. The frontend still validates + has a fallback for an
# unrecognized block type (see spec's "your model will invent one
# eventually") because the CONTRACT between the two services should not
# assume the backend can never change out from under the frontend — schema
# drift across a service boundary is a real failure mode even when neither
# side involves an LLM.
# ---------------------------------------------------------------------------


class OutcomeBlock(BaseModel):
    type: str = "outcome"
    outcome: Outcome
    amount: float
    confidence: float
    escalation_reason: str | None = None
    narrative: str = ""


class EvidenceClause(BaseModel):
    clause_id: str
    policy_id: str
    title: str
    text: str


class ClauseEvidenceBlock(BaseModel):
    type: str = "clause_evidence"
    clauses: list[EvidenceClause]


class LineItemBreakdownRow(BaseModel):
    description: str
    claimed_amount: float
    allowed_amount: float
    verdict: LineItemVerdict
    governing_clause_ids: list[str]
    reason: str


class LineItemBreakdownBlock(BaseModel):
    type: str = "line_item_breakdown"
    rows: list[LineItemBreakdownRow]
    deductible_applied: float


class InteractiveAction(str, Enum):
    APPROVE = "approve"
    OVERRIDE = "override"
    REQUEST_DOCUMENTS = "request_documents"


class InteractiveActionsBlock(BaseModel):
    type: str = "interactive_actions"
    claim_id: str
    thread_id: str
    available_actions: list[InteractiveAction] = Field(
        default_factory=list,
        description="Empty once a decision is committed (or there is nothing to resume). "
        "Non-empty while paused at `commit_decision` (confirmation) or `escalation`.",
    )
    resumes_at_node: str | None = Field(
        default=None,
        description="Which node the graph resumes at when one of these actions is "
        "submitted: `commit_decision` for confirmation, `escalation` for missing-"
        "info / degradation pauses. None when there is no pending interrupt.",
    )
    is_pending: bool = False


class RiskSignalBlock(BaseModel):
    """Fifth typed UI block: surfaces WHY a claim was flagged, separately
    from the outcome block's confidence number. Always present; empty flags
    is a clean "no anomalies" state, not an omitted panel."""

    type: str = "risk_signal"
    severity: RiskSeverity
    flags: list[str]
    duplicate_claim_ids: list[str] = Field(default_factory=list)


class DecisionUISpec(BaseModel):
    claim_id: str
    blocks: list[dict]  # each dict validates against one of the block models above;
    # kept as list[dict] rather than a discriminated union here so a node can
    # append a block type this file doesn't know about yet without a schema
    # change on the backend — the frontend is where an unrecognized block
    # type is actually handled (see module docstring above).
