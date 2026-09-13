# DESIGN.md

Short and dense, per the brief. This file grows commit-by-commit as forks are hit —
it is not reconstructed from memory at the end.

## Service split: Django vs. FastAPI

Django was kept thin and used exactly where the brief asked for it: the DRF API
surface (`POST /api/claims/`, `GET /api/claims/<id>/`) and persistence (claim
records, decisions, audit trail). The agent runtime — the LangGraph graph, its
state schema, checkpointer, and streaming endpoint — was kept in FastAPI, because
LangGraph's streaming, async, and interrupt/checkpoint patterns are a more natural
fit there, and it lets the graph be developed and tested standalone without
spinning up the full Django stack on every iteration. Django calls `/agent-service`
over internal HTTP; this is one runtime split inside a single git repo, not two
separate repos.

**agent-service's HTTP contract** (`agent-service/routes.py`), the concrete
shape of that internal call: `POST /claims/{claim_id}/adjudicate` starts a
run (`claim_id` doubles as the LangGraph `thread_id`), `POST
/claims/{claim_id}/resume` continues one paused in `escalation`, both
streaming SSE (`node_complete` events, one per completed superstep, then a
terminal `escalated` or `done`); `GET /claims/{claim_id}` is a non-streaming
snapshot read for polling/refresh. The graph is compiled once at FastAPI
startup (`main.py`'s `lifespan`) against a file-backed `AsyncSqliteSaver`, so
a resume works even across separate Django→agent-service requests.

**Django's persistence layer** (`backend/claims/models.py`: `Claim`,
`Decision`, `AuditEvent`) is a second, independent copy of the outcome —
built by parsing the SSE event stream as `views.py` relays it byte-for-byte
to the client, not by Django calling back into agent-service's internal
state. This is deliberate: Django's database is the durable
product-of-record (survives even if agent-service's own checkpoint file is
ever cleared), and it's populated by the same bytes the frontend sees,
so there's no separate code path that could drift from what was actually
shown to the user. `GET /api/claims/<id>/` never calls agent-service at all
— it is served entirely from this persisted copy.

**Frontend** (`frontend/`, Vite + React + TypeScript) talks to Django only,
via relative `/api/...` paths — Vite's dev server proxies `/api` to Django
(`vite.config.ts`), so no CORS package was needed for local dev, and the
same relative paths work unchanged if this is ever served from behind
Django's origin in production. `src/api.ts::streamSSE` parses the SSE body
via the Fetch API's `ReadableStream`, buffering decoded text and yielding
one event per blank-line-terminated block — the same parsing shape as
`backend/claims/views.py::_parse_sse_block`, so the three places that speak
this wire format (agent-service, Django, frontend) all agree independently
rather than sharing a fragile shared parser. `BlockRenderer.tsx` renders
each of the 5 real block types (`outcome`, `clause_evidence`,
`line_item_breakdown`, `interactive_actions`, `risk_signal`) via a genuinely
exhaustive switch (a `never`-returning `assertNever` in `default`, per the
workspace's typescript-exhaustive-switch rule) over a `KnownBlock` subtype;
anything with an unrecognized `type` (contract drift, not an LLM
hallucination — see `ui_composition`'s own docstring for why that
distinction matters) falls back to `UnknownBlockView`, which renders raw
JSON instead of crashing the page.

Environment note: this machine's Node (`v20.18.2`) is one minor version
behind what the newest Vite 8/oxlint releases require
(`^20.19.0 || >=22.12.0`), which surfaced as native-binding load failures
for both `vite build` (rolldown-vite) and `npm run lint` (oxlint). Pinned
`vite`/`@vitejs/plugin-react` to the last classic (Rollup/esbuild-based, no
native rolldown dependency) major versions instead of upgrading Node system-
wide — `npm run dev`, `npm run build`, and `npx tsc --noEmit` all verified
working on this pin. `npm run lint` (oxlint) still needs a newer Node; not
blocking since it was never part of the P0 checklist.

## Tool granularity (MCP)

MCP tools here are a **deterministic data-layer interface that graph nodes call
directly and unconditionally** (e.g. `eligibility_evaluation` always calls
`compute_payout`; `policy_retrieval` always calls `search_policy`). They are not
exposed to the LLM as a free-choice tool-calling surface the model decides whether
or when to invoke — the graph's control flow, not the model, decides which tool
runs and when. This distinction matters for auditability: if the LLM could choose
whether to call `compute_payout`, an omitted call would be an unlogged silent
failure mode. MCP read tools (`search_policy`, `get_clause`, `compute_payout`)
are annotated `read_only_hint=True`; `get_claim_history` remains a plain
read-only internal function. The mutating MCP tool (`flag_for_review`) is
annotated read-write and is gated behind the confirmation interrupt
(`commit_decision`) rather than called freely.

## Degradation policy (required architecture property, not P1)

Four failure modes are defined explicitly, each producing a specific `audit_log`
entry and, where applicable, an escalation with a named reason rather than a
silent default:

| Failure | Detection | Behavior | Escalation reason |
|---|---|---|---|
| `policy_retrieval` returns zero clauses | `retrieved_clauses` empty after query | Route straight to `escalation`; `eligibility_evaluation` never runs against an empty clause set | `"no governing policy found"` |
| `extraction` fails schema validation | Pydantic `ValidationError` on structured-output parse | Retry once with a stricter re-prompt (explicit schema + prior error appended); if still invalid, escalate. Malformed facts never propagate to `router`/retrieval/eligibility | `"extraction failed"` |
| LLM call errors or is rate-limited | Exception/429 from the OpenAI client in any LLM-backed node (`extraction`, `explanation`) | Retry with backoff, 2–3 attempts; if still failing, escalate | `"adjudication service unavailable"` |
| Escalation is never answered | N/A — a property of the checkpointer, not a node | Claim stays `PENDING` indefinitely; surfaced as a visibly flagged item in the claim list. No auto-timeout, no auto-resolve — an unresolved escalation defaulting to any outcome would silently violate "a human decides" | N/A (no escalation *reason*; this is what happens after one) |

Each of the first three has a dedicated fixture claim / forced-failure test so
`EVIDENCE.md` reports on these from real triggered runs, not description.

## Five real forks in the road

These are the five that changed the architecture. Additional technical
decisions that did not displace one of these are listed after.

### 1. Django-fronts-a-separate-agent-service, vs. LangGraph embedded in Django

**Chose:** LangGraph, the MCP client, and the checkpointer live in a standalone
FastAPI service (`agent-service`). Django owns the API surface, persistence, and
the streaming proxy, and calls the agent service over internal HTTP.

**Rejected:** Embedding the graph directly inside Django views.

**Why:** Two of the spec's required properties — streaming and checkpoint-based
resumability — are naturally suited to an async Python service and awkward to
retrofit onto Django's request/response model without ASGI/Channels work we didn't
have prior experience with. Keeping the graph standalone also let us iterate on
node logic directly (via scripts/tests) without spinning up the full Django stack
on every change — a real velocity difference given the timeline. The spec
explicitly names this as a legitimate, defensible split, which we took as
permission rather than a shortcut.

**Cost of this choice:** one extra internal HTTP hop, and two processes to run
instead of one. Acceptable given the above.

### 2. Which parts of the data layer get MCP-wrapped, vs. plain internal functions

**Chose:** `search_policy`, `get_clause`, and `compute_payout` are real MCP tools
with explicit read-only/idempotent annotations. `get_claim_history` stays a plain
internal function. `flag_for_review` is MCP, read-write, gated behind a valid,
reasoned Override — it cannot fire on its own.

**Rejected:** Wrapping every data-layer call in MCP for surface-area completeness,
or conversely doing the minimum literal "one tool" the P0 floor asked for.

**Why:** MCP earns its place where the tool boundary matters for auditability or
mutation safety — retrieval and payout computation are exactly the load-bearing,
citation-and-computation-critical calls the brief cares about being traceable and
deterministic. `get_claim_history` is a read of internal records with no
governance/citation role, so MCP-wrapping it added protocol overhead without
adding auditability. `flag_for_review` being MCP and explicitly gated by a human
action (not LLM-invokable, not free-standing) is the direct answer to "how do read
and write tools differ structurally" — the mutating tool is reachable only through
the same interrupt/confirmation pathway a human decision already goes through.

### 3. Extraction determinism: pinning temperature=0 after finding it wasn't set

**Chose:** Pin both extraction and explanation LLM calls to temperature=0, after
discovering they'd been running on the API default (effectively 1.0).

**Rejected:** Leaving temperature unset, or treating high variance purely as an
"identity bias" finding without checking the more basic cause first.

**Why:** Early consistency testing (Gap 1) showed several claims where every
variant type — including line-item reordering, which shouldn't matter semantically
— flipped the outcome identically. That pattern pointed to base-run instability,
not identity-driven bias. Checking temperature confirmed it: unset, defaulting to
1.0. Pinning to 0 dropped cross-variant drift from 26.7% to 21.3%, and same-claim
repeat-run drift (a genuine noise-floor measurement we added specifically to
interpret this) came in at 4.2%. This is also philosophically consistent with the
system's core principle — if the rules engine must be deterministic, the extraction
step feeding it should be as deterministic as the model allows, not left to
whatever the API defaults to.

**What's still open:** temperature=0 did not fully eliminate variance on complex,
multi-item extraction (see EVIDENCE.md §6.2 — CLM-013/014/019 residual drift). We
diagnose this as a real limit of the current extraction model on itemization tasks,
not something prompting alone fixed.

### 4. What "Override" is allowed to change, and what it only records

**Chose:** Override never lets a typed number become the system's payout of
record. `Decision.amount` remains whatever `compute_payout` produced. The
adjuster's proposed replacement figure and required reason are captured as
separate, clearly-labeled audit fields (`override_proposed_amount`,
`override_reason`) — visible for human review downstream, structurally incapable
of feeding back into `compute_payout` or the automated decision.

**Rejected:** Letting an override's typed amount directly replace `Decision.amount`
in the same flow.

**Why:** This is the same non-negotiable principle applied to a place it's easy to
miss — the danger isn't only "can the LLM produce a number," it's "can any
free-text human input become the system's payout of record without going through
the deterministic engine." Allowing a typed override amount to silently become the
new `Decision.amount` would reopen exactly the hole the architecture is built to
close, just via a human instead of a model. We initially shipped Override *without*
capturing a reason or proposed amount at all — a real gap, caught in review, fixed
by adding the audit fields without touching how `Decision.amount` is produced.

### 5. What "governs" a claim for eligibility vs. what's available to cite in the
explanation

**Chose:** After `eligibility_evaluation` runs, an `evidence_reconciliation` step
takes the exact clause IDs actually used to compute the decision and guarantees
they're present in `retrieved_clauses` — fetching any missing ones directly via
`get_clause` — before `explanation` runs.

**Rejected:** Trusting that whatever `policy_retrieval`'s top-k search returned
would always include every clause eligibility actually used.

**Why:** We found a real case (CLM-018) where eligibility correctly applied a
diagnostic cap clause that fell outside the retrieval step's top-k cutoff — meaning
the decision was correct, but the explanation had no way to cite the clause that
actually drove it. That's a subtler and more serious problem than a wrong citation:
it's a decision that's right but not fully auditable. The fix guarantees the set of
citable evidence always contains, at minimum, everything that actually governed the
outcome, independent of retrieval ranking. Groundedness measurement went from
94.4% to 100% after this fix, and — more importantly — the fix targets the actual
root cause (a coverage gap between two clause sets) rather than the symptom.

## Additional decisions (not one of the five)

These were real forks; they sit here so the five above stay the ones that
changed the product architecture rather than a padded list.

- **Streaming transport: Django proxy vs. frontend hitting agent-service
  directly.** Django proxies. Chosen because it keeps the frontend's contract
  to one backend and lets Django persist audit-log entries as a side effect of
  relaying each chunk. Confirmed by the isolated 5-chunk spike (see below).
- **What the LLM extracts vs. what's given structurally.** `policy_id` and
  `policy_start_date` come from the submission form; only
  `ExtractedNarrativeFacts` is the LLM's structured-output target. `ClaimFacts`
  merges the two in the extraction node.
- **Structured rule table vs. NLP-parsed clause prose.** `compute_payout` runs
  a hand-authored Python rule table keyed by clause ID
  (`rules/eligibility.py`), cross-checked against fixture markdown. A generic
  clause-interpreter would need an LLM in the loop or a fragile parser.
- **Evidence tags vs. LLM-resolved coverage.** The extractor tags what the
  narrative asserts; `rules/eligibility.py` maps those tags to coverage. This
  is how §7.1.4 gates §4.2.9 and §3.1.5 waives §3.1.2 without the LLM deciding
  coverage.
- **Groundedness checking lives in `explanation`, not in `compute_payout`.**
  Mixing the two conflated computation with citation verification and broke
  engine unit tests with a partial clause list. The checker is deterministic
  and emits `citation_not_retrieved`, `citation_not_in_narrative`, and
  `content_mismatch`.
- **MCP transport: in-process vs. subprocess/stdio.** In-process
  `Client(server)` — a process boundary would add operational complexity with
  no auditability benefit here. Swapping later only changes how the client is
  constructed.
- **All conditional routing lives in `build_graph.py`, never inside a node.**
  Nodes report facts; `_after_extraction`, `_after_router`, `_after_retrieval`,
  `_after_evidence_reconciliation`, and `_after_ui` decide the next hop. A
  clean claim is 10 completed nodes then a confirmation pause; resume with
  approve is the 11th execution (`AUDIT.md` traces one).
- **Confirmation interrupt is a separate node from escalation.** `escalation`
  pauses when the graph cannot decide; `commit_decision` pauses when it can.
  Eval auto-resumes confirmation with `approve`; it does not auto-resume
  escalation.

## Token budget (context pack)

Per-claim packed context (narrative + facts JSON + retrieved clause bodies) is
capped at `CLAIM_CONTEXT_TOKEN_BUDGET` (3500, ~4 chars/token) in
`graph/context_budget.py`. Drop order is explicit, not truncation: (1) clauses
at or below the coverage-hit relevance floor, lowest score first; (2) if still
over, drop the least-recently-relevant peril's clauses (`claim_facts.perils`
last-to-first; unassigned/admin first). Never drop the last remaining clause.
A drop writes `CONTEXT_BUDGET_DROP` to `audit_log` and `context_drop` on state
so explanation knows what left the evidence set.

## Streaming spike (result)

Isolated test: `agent-service/main.py::/spike/stream` yields 5 SSE chunks, ~1s
apart, each stamped with a server-side send time. `backend/claims/views.py::
spike_stream_proxy` relays it through `django.http.StreamingHttpResponse` fed by
an `httpx.stream(...).iter_raw()` generator, adding its own receive timestamp per
chunk. A standalone client (`agent-service/spike_client_test.py`) hit the Django
URL and logged the wall-clock gap between consecutive chunks.

Result — chunks arrived incrementally, not buffered:

```
[t= 0.477s | gap=0.477s] chunk 0 sent_at=...662 | django_proxied_at=...662
[t= 1.492s | gap=1.015s] chunk 1 sent_at=...677 | django_proxied_at=...678
[t= 2.507s | gap=1.016s] chunk 2 sent_at=...693 | django_proxied_at=...694
[t= 3.519s | gap=1.012s] chunk 3 sent_at=...705 | django_proxied_at=...705
[t= 4.529s | gap=1.010s] chunk 4 sent_at=...715 | django_proxied_at=...715
Total wall time: 5.546s
```

Per-chunk gaps are ~1.01s (matching the server's `asyncio.sleep(1)`), and
`django_proxied_at` is within a millisecond of `sent_at` for every chunk — Django
is not batching. Had it buffered, all 5 lines would have arrived at once around
t≈5s with ~0s gaps between them. Conclusion: `StreamingHttpResponse` + `httpx.stream`
on Django's dev server streams correctly end-to-end; proceeding with the Django-
proxy design as planned, no ASGI/Channels rework needed for P0.

Re-measured 2026-09-14 through the live Django proxy (`spike_client_test.py` →
`/api/spike/stream/`): five chunks, gaps 1.003–1.011s, total 5.884s. Same
incremental result; the real claim SSE path (`node_complete` /
`awaiting_confirmation` / `escalated` / `done`) stays on this proxy.

## Eval harness (`agent-service/eval/run_eval.py`)

Runs all 25 fixtures through the REAL compiled graph (real MCP tools, real
LLM) and reports four things: outcome accuracy against `ground_truth.json`;
groundedness rate (fraction of runs with zero fabricated-citation
violations); injection resistance (CLM-024/025: does the final
outcome/amount match ground truth despite the embedded instructions, i.e.
did the structural barrier actually hold under adversarial input, not just
in the synthetic unit test); and two flavors of consistency-variance
(`eval/variants.py`) — same-text-twice (raw LLM non-determinism) and
same-facts-reworded (extraction's robustness to phrasing), each on a small
curated sample rather than all 25, since every variant run is a real paid
LLM call.

All the *scoring* functions (`score_accuracy`, `score_groundedness`,
`score_injection_resistance`, `score_consistency`) are pure — they take
`RunResult` records, not a live graph — so `tests/test_run_eval.py` verifies
the scoring logic itself with synthetic data and a fake graph object, with
no API key needed. Only actually *running* `eval/run_eval.py` end-to-end
needs the real key; see EVIDENCE.md for those results once it's been run.

## What we cut, explicitly

- **LangSmith tracing (P2):** Requires an external account/service, which
  conflicts directly with the brief's own constraint ("no external services beyond
  the LLM, no accounts"). Not pursued for this reason, not for lack of time.
- **Multi-turn adjuster follow-up (P1):** cut. The fifth UI block (`risk_signal`)
  is in; conversation-style follow-up is not. Real engineering cost (reference
  resolution, conversation state) for a UX improvement, not a trust/auditability
  one — lowest-leverage item on the P1 list given the time available.
- **Calibration and a second prompt/retrieval variant (P2):** not attempted;
  flagged as the next thing worth doing with more time, not evidence we chose to
  skip measuring.
- **Keyword retrieval's coverage-hit threshold is hand-calibrated, not
  principled.** `MIN_COVERAGE_RELEVANCE = 0.5` was picked by inspecting scores
  on this fixture set. It would need real tuning (or embeddings) against a
  larger corpus. Flagging this rather than presenting the keyword scorer as
  more robust than it is.

## Where AI was delegated, and where it was wrong

Every line in this repo was written by an AI agent (me) working under the
master instructions, with the user reviewing checkpoints and making the
actual decisions at each fork above. Being honest about where that went
wrong, not just where it went right:

- **Stale training-data assumptions about fast-moving libraries, twice.**
  I initially wrote `mcp_server/server.py` against `from mcp.server.fastmcp
  import FastMCP` — training-data-era API — and it failed at import with
  `ModuleNotFoundError` because `mcp==2.2.0` renamed `FastMCP` to
  `MCPServer`. Same pattern with `CallToolResult`'s field names
  (assumed camelCase, actual is snake_case) and how different tool return
  shapes get wrapped in `structured_content`. All caught by actually running
  the code and inspecting real objects, not by having gotten it right the
  first time. The master instructions specifically flagged LangGraph/MCP as
  needing current-docs verification given the training cutoff; I should
  have (and eventually did) extend the same skepticism to `openai` on my
  own initiative rather than needing the pattern to repeat first.
- **A design mistake that silently broke a whole degradation mode.** The
  empty-retrieval degradation path (`policy_retrieval` finding zero
  clauses) never actually fired in early testing because a fixed
  `"deductible"` query was included for every claim and matched clause
  §2.3 regardless of peril — so `retrieved_clauses` was never truly empty
  even for genuinely uncovered perils. This wasn't caught by a failing
  test; it was caught by noticing a required degradation mode had zero
  fixtures actually exercising it, which should have been checked earlier,
  right when that fixture was written, not after the retrieval node was
  "done."
- **A routing bug from checking the wrong scope.** `evaluate_line_item_home`
  checked the CLAIM-level `perils` list to decide whether a line item fell
  into the water-damage exclusion branch, so a fire-cleanup line item on a
  multi-peril claim got routed through water-damage exclusion logic just
  because the claim ALSO had an unrelated water-damage peril. Should have
  checked the item's own category/tags from the start — conflating
  claim-level and item-level scope is exactly the kind of bug a
  human-in-the-loop review is supposed to catch before it ships, and here
  it was caught by a test that specifically probed multi-peril routing, not
  by getting the logic right on read-through.
- **Introduced a phantom graph node in a downstream module before the
  graph existed.** `ui.py` referenced `resumes_at_node="commit_decision"`
  for non-escalated decisions — a node that was never built (P0 has no
  confirmation interrupt; that's explicitly P1). This only surfaced when
  `build_graph.py` was actually wired and there was no such node to point
  at. Building `ui_composition` before `build_graph.py` existed meant there
  was nothing to check that reference against at the time; the fix was
  correct but should have been flagged as a forward reference needing
  reconciliation the moment it was written, not discovered a session later.
- **Reached for the lazy version of a fix before the correct one.** When
  the checkpointer's serializer started warning on every custom type in our
  state, my first instinct was `allowed_msgpack_modules=True` — the
  "make the warning go away" option, which (on inspection of the actual
  source) still emits the warning, just marks it as intentionally allowed.
  Fixed properly by reading `jsonplus.py`'s source and passing the exact
  list of our own schema classes instead. A pattern worth naming: the
  first fix that compiles isn't always the first fix that's actually
  correct, and checking library internals rather than trusting a
  plausible-looking keyword argument is the more reliable path.
- **A stale number in my own documentation.** `build_graph.py`'s docstring
  claimed a clean claim takes "8 node executions" — off by one, then off
  again after `evidence_reconciliation` was added; the real count (verified
  in `tests/test_build_graph.py` and traced in `AUDIT.md`) is 10 completed
  nodes before the confirmation pause. Caught only during a consistency pass
  across
  `DESIGN.md`/`README.md`/`AUDIT.md`, not when it was first written. A
  reminder that documentation claims need the same "verify, don't assume"
  discipline as code, especially when the same fact gets restated in
  multiple files that can quietly drift out of sync with each other.
- **Didn't anticipate an environment constraint until it broke a build.**
  Scaffolded the frontend with the latest Vite (8.x, rolldown-based) without
  checking it against this machine's installed Node version first;
  `vite build` failed on a missing native binding, traced to Node
  `v20.18.2` being one minor version behind Vite 8/oxlint's stated minimum.
  Fixed by pinning to the last classic (non-rolldown) Vite major version
  rather than touching the system Node install — but checking `node
  --version` against a new tool's stated requirements before scaffolding
  would have avoided the detour entirely.
