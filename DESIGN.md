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
failure mode. Read tools (`search_policy`, `get_clause`, `get_claim_history`) are
annotated `read_only_hint=True`; the one mutating tool (`flag_for_review`) has no
such hint and is gated behind a confirmation step (P1) rather than called freely.

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

_(filled in as each is hit — placeholders below, replaced with real content)_

1. Streaming transport: Django `StreamingHttpResponse` proxy vs. frontend hitting
   agent-service's SSE endpoint directly. **Resolved by spike** (see below):
   Django proxies. Chosen over a direct-to-agent-service connection because it
   keeps the frontend's contract to one backend (Django) and lets Django persist
   audit-log entries as a side effect of relaying each chunk, without a second
   webhook path.
2. **What exactly does the LLM extract vs. what's given structurally.**
   `policy_id` and `policy_start_date` are treated as structural fields from
   the claim submission form (like a real intake form's policy-number field),
   never asked of the LLM. Only `ExtractedNarrativeFacts` — date of loss,
   perils, line items, evidence tags — is the LLM's structured-output target;
   `ClaimFacts` merges the two deterministically in the extraction node. This
   shrinks the LLM's surface on the single most decision-critical identifier
   (which policy governs) to zero.
3. **Structured rule table vs. NLP-parsed clause prose.** `compute_payout`
   does not parse retrieved clause text at runtime to decide coverage; it
   runs a hand-authored Python rule table keyed by clause ID
   (`rules/eligibility.py`), separately cross-checked against the fixture
   markdown by `tests/test_rule_table_matches_fixtures.py`. Rejected
   alternative: a generic clause-interpreter that reads clause text and
   applies it programmatically — more "automatic," but it would either need
   an LLM in the loop (violating the non-negotiable rule) or a bespoke parser
   fragile to how each clause happens to be worded. A rules engine that reads
   legislation-as-code, verified against source text by tests, is also how
   this is done in real compliance systems.
4. **Evidence tags vs. LLM-resolved coverage.** The extractor may tag a claim
   with controlled-vocabulary facts about what the narrative asserts (e.g.
   `pre_existing_seepage_mentioned`, `accidental_injury`) or admit the text is
   genuinely ambiguous (`cause_ambiguous`). It never resolves what those facts
   mean for coverage — that mapping lives entirely in `rules/eligibility.py`.
   This is the mechanism behind both required clause interactions (§7.1.4
   exclusion gating the §4.2.9 sub-limit; §3.1.5 waiving the §3.1.2 waiting
   period) without the LLM ever touching the coverage question itself.
5. **Groundedness checking lives in `explanation`, not in `compute_payout`.**
   An early draft had `compute_payout` reject its own clause citations if they
   weren't in `retrieved_clauses`. Cut: it conflated two different concerns
   (deterministic computation vs. citation-to-evidence verification) and
   broke unit-testing the engine with a partial/empty clause list. The
   explanation node's groundedness check is the single place assertions are
   verified against retrieved text.

6. **MCP transport: in-process vs. subprocess/stdio.** `mcp_client/client.py`
   connects to the `MCPServer` instance directly in the same Python process
   (the SDK's documented in-memory `Client(server)` pattern) rather than
   spawning it as a separate process over stdio. Given the "Tool granularity
   (MCP)" section above (MCP is a data-layer interface owned by
   agent-service, not an arbitrary external service), a process boundary
   would add operational complexity with no auditability benefit for this
   project. Swapping to stdio/HTTP later only changes how the client
   constructs its `Client`, not any node code.

7. **All conditional routing lives in `build_graph.py`, never inside a node.**
   Every node (`router`, `policy_retrieval`, `extraction`) that can trigger a
   degradation path only *reports* that fact by setting `escalation_reason`
   (or `retrieval_had_coverage_hit=False`) in its returned state dict; the
   three conditional-edge functions in `build_graph.py`
   (`_after_extraction`, `_after_router`, `_after_retrieval`) are the only
   code that decides where the graph goes next. Rejected alternative: let
   each node call `return Command(goto=...)` directly — works in LangGraph,
   but scatters the graph's actual shape across ten files instead of one,
   making the node-hop-count claim (clean claim vs. degraded claim) far
   harder to verify by reading a single place. `graph/build_graph.py`'s
   module docstring documents the exact hop-count for a clean claim (9 node
   executions, never touching `escalation`) as a comment, not just a claim in
   this file, and `AUDIT.md` traces a real one.

(That's eight, three more than the "five real forks" the brief asks for —
leaving all of them in since each was a genuine decision point, not padding.)

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

## What was cut

- **Keyword retrieval's coverage-hit threshold is hand-calibrated, not
  principled.** `policy_retrieval` needs to distinguish "found a genuinely
  relevant clause" from "coincidentally shares a common word like 'damage'
  with an unrelated clause" to make the empty-retrieval degradation path
  (mode 1) actually fire only when it should. The threshold
  (`MIN_COVERAGE_RELEVANCE = 0.5` in `graph/nodes/retrieval.py`) was picked
  by inspecting scores on this fixture set, not derived from anything
  principled — it would need real tuning (or embeddings) against a larger,
  more varied policy corpus. Flagging this now rather than presenting the
  keyword scorer as more robust than it is; see EVIDENCE.md's failure
  analysis for where this could bite in practice.

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
  claimed a clean claim takes "8 node executions" — off by one; the real
  count (verified in `tests/test_build_graph.py` and traced concretely in
  `AUDIT.md`) is 9. Caught only during a final consistency pass across
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
