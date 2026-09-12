# AUDIT.md — One Claim Traced End to End

This traces **CLM-001** (a clean single-peril water-damage claim) through the
actual compiled graph (`agent-service/graph/build_graph.py`), captured by
running it for real against `InMemorySaver` with `extraction`/`explanation`'s
LLM calls monkeypatched at the exact seam the node unit tests use (see
`tests/test_build_graph.py`) — **`.env` still has no real `OPENAI_API_KEY`**
(the user will add one; see `DESIGN.md`), so this is the closest honest
substitute available right now. Everything downstream of the mocked
extraction/explanation calls — routing, retrieval, both parallel branches,
the deterministic payout engine, decision composition, and UI
composition — is real, unmocked code. Once a real key is in place, re-running
this exact trace (same claim, no code changes needed) is the first live
smoke test called for in the pending-tasks list.

## Input

```json
{
  "claim_id": "CLM-001",
  "policy_id": "POL-HOME-01",
  "policy_start_date": "2024-01-10",
  "filed_date": "2024-08-02",
  "claimant_name": "Priya Nair",
  "claimant_gender": "female",
  "claimant_city": "Pune",
  "narrative_text": "My name is Priya Nair, writing from Pune. On 30 July 2024 a pipe under my kitchen sink burst suddenly and water sprayed everywhere for about ten minutes before I could shut the valve. It soaked the flooring near the sink. I have photos from that same day and a plumber's repair estimate. I'm claiming ₹8,000 for flooring repair. There was no prior leak here at all, this happened out of nowhere."
}
```

Mocked LLM outputs used for this trace (standing in for the real model):
- `extraction`: `date_of_loss=2024-07-30`, `perils=[water_damage]`, one line
  item ("Kitchen flooring repair", claimed ₹8,000, tag `sudden_discharge`),
  `cause_ambiguous=false`.
- `explanation`: a short approval narrative citing `§2.3` and `§4.2.1`.

## Node-by-node trace

The claim took **9 node executions**, never touching `escalation` — this is
the "clean claim, fewer hops" property `build_graph.py` is designed to
guarantee (see that file's own docstring for the contrast with a
missing-field or multi-peril claim).

| # | Node | What happened |
|---|------|----------------|
| 1 | `intake_normalize` | Narrative normalized (395 chars), no injection-shaped patterns flagged. |
| 2 | `extraction` | LLM (mocked) extracted 1 line item, peril `water_damage`, `cause_ambiguous=False`. `policy_id`/`policy_start_date` merged in from the structural envelope, never asked of the LLM. |
| 3 | `router` | `complexity=simple`, `fast_path=True`, `missing_fields=[]` — routes to `policy_retrieval`, not `escalation`. |
| 4 | `policy_retrieval` | Retrieved 6 distinct clauses for POL-HOME-01 across 3 queries (2 peril/category-specific, 1 administrative). Coverage hit confirmed → routes to the parallel fan-out, not `escalation`. |
| 5 | `eligibility_evaluation` | Real MCP `compute_payout` call: `total_claimed=8000.0`, `total_payable=3000.0` (₹5,000 deductible applied per `§2.3`), `needs_escalation=False`. |
| 6 | `risk_anomaly` | `severity=none`, no flags — no duplicate claims, filed after loss date, no injection detected, amount not suspiciously round or undocumented. |
| 7 | `decision_composition` | `outcome=approve`, `amount=3000.0`, `confidence=1.00` (no risk penalty, no missing-info penalty). |
| 8 | `explanation` | LLM (mocked) narrative generated; both cited clause IDs (`§2.3`, `§4.2.1`) verified present in `retrieved_clauses` — 0 groundedness violations. |
| 9 | `ui_composition` | 5 UI blocks composed. |

## Final state

**Decision:** `approve`, ₹3,000.00, confidence 1.00, no escalation reason.

**Eligibility result:**
```json
{
  "line_items": [
    {
      "description": "Kitchen flooring repair",
      "claimed_amount": 8000.0,
      "verdict": "allowed",
      "allowed_amount": 8000.0,
      "governing_clause_ids": ["§4.2.1"],
      "reason": "Sudden and accidental discharge from plumbing is covered in full under §4.2.1."
    }
  ],
  "deductible_applied": 5000.0,
  "total_claimed": 8000.0,
  "total_payable": 3000.0,
  "clauses_used": ["§2.3", "§4.2.1"],
  "needs_escalation": false
}
```

**Risk result:** `severity=none`, `flags=[]`, `duplicate_claim_ids=[]`.

**UI spec** (`ui_composition`'s output — the exact 5 blocks the frontend
renders):

1. **`outcome`** — `approve`, ₹3,000, confidence 1.00, narrative: "Approved
   in full. The pipe burst was sudden and accidental, which is covered under
   §4.2.1 water damage cover, with the standard §2.3 deductible already
   reflected in the low claimed amount."
2. **`clause_evidence`** — `§4.2.1` ("Sudden and Accidental Discharge from
   Plumbing — Covered"), full clause text included.
3. **`line_item_breakdown`** — one row: claimed ₹8,000, allowed ₹8,000,
   verdict `allowed`, governing clause `§4.2.1`, deductible applied ₹5,000
   (this is why `total_payable` is ₹3,000 even though the line item itself
   was allowed in full — the deductible is a claim-level adjustment, not a
   per-item one).
4. **`interactive_actions`** — `available_actions=[]`, `resumes_at_node=null`,
   `is_pending=false`: nothing pending, this decision is already final (see
   `graph/schemas.py::InteractiveActionsBlock` for why non-escalated claims
   get an empty-but-present block rather than one offering actions that
   wouldn't do anything).
5. **`risk_signal`** — `severity=none`, `flags=[]`.

## What this trace demonstrates

- **The LLM never touched the outcome or amount.** `extraction`'s output
  schema (`ExtractedNarrativeFacts`) has no field for either; `explanation`
  received the already-computed `Decision`/`EligibilityResult` as read-only
  context and could only affect the wording, not the numbers. The
  `deductible_applied=5000.0` and `total_payable=3000.0` figures came
  entirely from `rules/payout.py`'s deterministic arithmetic against the
  rule table in `rules/eligibility.py`, keyed by clause ID.
- **Both required MCP tools ran for real** — `search_policy` (inside
  `policy_retrieval`) and `compute_payout` (inside `eligibility_evaluation`)
  — over the actual MCP protocol (`mcp_client/client.py`), not a stub.
- **Groundedness held**: both clause IDs the (mocked) explanation cited were
  actually in `retrieved_clauses`; a citation to a clause never retrieved
  would have been logged as a `GROUNDEDNESS_VIOLATION` audit event and
  dropped from the shown citations (see `graph/nodes/explanation.py`).
- **The audit trail is append-only and complete**: every node that ran left
  exactly one `AuditEvent`, in execution order, with no gaps — this is what
  `audit_log`'s `operator.add` reducer (see `graph/state.py`) guarantees
  structurally, not by convention.
