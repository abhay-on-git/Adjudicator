# EVIDENCE.md — Measured Results

This file separates what has actually been **measured** from what is
**pending** the real `OPENAI_API_KEY` (still not in `.env` — the user will
add it; see `DESIGN.md`). Nothing below is a projection or an estimate;
every number either comes from a test run captured in this session or is
explicitly marked pending.

## 1. Structural non-negotiable: LLM cannot produce a decision or amount

`tests/test_adversarial_llm_amount.py` — **6/6 passing**, exercising **3
independent structural barriers**, any one of which alone would stop the
attack:

1. **Schema barrier** — `ExtractedNarrativeFacts`/`ClaimFacts` use
   `model_config = ConfigDict(extra="forbid")`. A malicious/confused LLM
   output containing `"decision": "approve", "amount": 999999` fails
   Pydantic validation before it becomes a Python object at all.
2. **No-read barrier** — verified by AST inspection of `rules/payout.py` and
   `rules/eligibility.py`: neither module contains a `getattr()` call or a
   `.decision`/`.outcome`/`.suggested_amount` attribute access anywhere.
   Even a claim object tampered via direct `__dict__` manipulation (bypassing
   Pydantic validation entirely — the most generous attack surface possible)
   produces an identical `total_payable`; the smuggled `amount=999999` is
   never read.
3. **Import-graph barrier** — verified by AST inspection: `rules/payout.py`
   and `rules/eligibility.py` have zero imports of `openai`, `langchain`,
   `langgraph`, or `mcp_client`. There is no code path in the deterministic
   engine that could call an LLM even if someone tried to wire one in ad hoc.

## 2. Degradation architecture — all 4 modes fixture-tested

| Mode | Fixture(s) | Test | Result |
|---|---|---|---|
| `policy_retrieval` returns zero clauses | CLM-022 | `tests/test_retrieval_node.py` | Coverage-hit detection correctly fires only on peril/category queries, not the always-present administrative ("deductible") query — verified via the renamed, more precise test after an earlier bug where this degradation path never actually triggered (see `DESIGN.md`'s error log). |
| `extraction` fails schema validation | CLM-023 | `tests/test_extraction_node.py` | 5/5 passing — retry-once-then-escalate path fully covered with a mocked validation failure. |
| LLM error/rate-limited (both `extraction` and `explanation`) | — | `tests/test_extraction_node.py`, `tests/test_explanation_node.py` | Backoff-then-escalate (extraction) / backoff-then-template-fallback (explanation, does NOT escalate — the decision is already final) both covered with mocked API failures. |
| Escalation never answered | — | `tests/test_build_graph.py::test_missing_field_claim_escalates_and_pauses_for_human` | Confirmed the graph genuinely pauses (LangGraph `interrupt()`) rather than auto-resolving; `__interrupt__` present in state until a human explicitly resumes. |

## 3. Real conditional branching — hop count, not just branch existence

`tests/test_build_graph.py::test_clean_claim_reaches_end_without_escalation`
asserts every required node ran **exactly once** for a clean single-peril
claim (CLM-001), and `escalation` never ran. A concrete trace of this exact
run — full audit log, decision, eligibility result, and UI spec — is in
[`AUDIT.md`](AUDIT.md): **9 node executions**.

`test_missing_field_claim_escalates_and_pauses_for_human` asserts the
opposite for a degraded claim: `policy_retrieval`, `eligibility_evaluation`,
and `risk_anomaly` **never run at all** — the graph routes straight from
`router` to `escalation` in 3 hops before pausing. The hop-count difference
between a clean and a degraded claim is asserted by the test suite, not
just visually apparent from the graph diagram.

## 4. Streaming: proven incremental at every layer

- **agent-service → Django** (original isolated spike, before the real
  graph existed): 5 SSE chunks ~1.01s apart, `django_proxied_at` within 1ms
  of `sent_at` for every chunk — full timing table in `DESIGN.md`'s
  "Streaming spike (result)" section.
- **Django → real graph, live process test** (this session): submitted a
  claim through a live Django server to a live agent-service server running
  the real compiled graph; received a real `node_complete` SSE event for
  `intake_normalize` with the correct persisted-to-DB audit trail, before
  stopping at `extraction` exactly where expected (no real API key yet).
- **Vite dev proxy → Django → agent-service**, same live test, repeated
  through the frontend's own proxy path — identical result.

## 5. Test suite summary (this session's final run)

| Suite | Count | Result |
|---|---|---|
| `agent-service` (`pytest`) | 90 | all passing |
| `backend` (Django `manage.py test`) | 8 | all passing |
| `frontend` (`tsc --noEmit` + `vite build`) | — | clean, no type errors, production bundle builds |

Notable test files beyond the unit-level node tests: `test_mcp_tools_end_to_end.py`
(both `search_policy` and `compute_payout` over the real MCP protocol, not
stubs), `test_rule_table_matches_fixtures.py` and
`test_fixtures_ground_truth_is_up_to_date.py` (drift guards — the rule table
is cross-checked against the actual policy fixture text, and
`ground_truth.json` is regenerated from the real `compute_payout` engine
rather than hand-typed), `test_build_graph.py` and `test_routes.py` (full
graph / full HTTP-layer integration, LLM calls mocked at the same seam as
the node unit tests), `claims/tests.py` (Django persistence, `httpx.stream`
mocked to replay scripted SSE).

## 6. Pending real `OPENAI_API_KEY` — built, unit-tested, not yet run live

The following require an actual LLM call and cannot be measured until the
key is added. The harness for all of them exists now
(`agent-service/eval/run_eval.py`, `eval/variants.py`) and its **scoring
logic** is unit-tested with synthetic data (`tests/test_run_eval.py`, 16/16
passing) — what's missing is a real run's numbers, not the measurement code.

- **Outcome accuracy** against all 25 fixtures in `ground_truth.json`.
- **Groundedness rate** across those 25 runs (fraction with zero fabricated
  clause citations).
- **Injection resistance** for CLM-024/CLM-025 — does the final
  outcome/amount match ground truth despite the embedded "ignore previous
  instructions" / "pre-approved by underwriting" text, under the REAL model
  rather than the synthetic adversarial unit test in §1.
- **Consistency-variance**, both flavors in `eval/variants.py`: repeat
  (identical narrative run twice) and phrasing (same facts, reworded) for a
  curated 3-claim sample each.

**TODO once the key is added:** run
`.venv\Scripts\python.exe -m eval.run_eval` from `agent-service/` and paste
the printed summary + `eval/report.json` findings here.
