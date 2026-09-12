"""Wires the 10 nodes into the compiled LangGraph StateGraph.

Node count matches the spec's required table exactly: intake_normalize,
extraction, router, policy_retrieval, eligibility_evaluation, risk_anomaly,
decision_composition, explanation, escalation, ui_composition.

Control flow (all conditional routing lives HERE, not inside nodes — see
every node module's docstring: nodes report facts, the graph decides):

    START -> intake_normalize -> extraction
    extraction --[escalation_reason set]--> escalation
    extraction --[else]--> router
    router --[missing_fields]--> escalation
    router --[else]--> policy_retrieval
    policy_retrieval --[no coverage hit]--> escalation
    policy_retrieval --[else]--> {eligibility_evaluation, risk_anomaly}  (parallel fan-out)
    {eligibility_evaluation, risk_anomaly} --> decision_composition       (fan-in)
    decision_composition -> explanation -> ui_composition
    ui_composition --[outcome == escalate]--> escalation
    ui_composition --[else]--> END
    escalation -> END   (after interrupt() returns on resume)

A clean single-peril claim never touches risk_anomaly's duplicate-history
scan differently, but DOES skip stright past escalation entirely if nothing
triggers it — a clean single-line claim is
intake_normalize -> extraction -> router -> policy_retrieval ->
{eligibility_evaluation, risk_anomaly} -> decision_composition ->
explanation -> ui_composition -> END: 9 node executions (counting both
parallel branches). A degraded claim short-circuits to `escalation` from
`extraction`, `router`, or `policy_retrieval` instead, in as few as 3 hops —
see tests/test_build_graph.py for both cases asserted directly, and
AUDIT.md for a concrete 9-hop trace of a real clean claim (CLM-001).

Mermaid source (regenerate via
`build_graph().compile().get_graph().draw_mermaid()`; a rendered PNG is
embedded in the root README.md):

    graph TD;
        __start__ --> intake_normalize;
        intake_normalize --> extraction;
        extraction -.-> router;
        extraction -.-> escalation;
        router -.-> policy_retrieval;
        router -.-> escalation;
        policy_retrieval -.-> eligibility_evaluation;
        policy_retrieval -.-> risk_anomaly;
        policy_retrieval -.-> escalation;
        eligibility_evaluation --> decision_composition;
        risk_anomaly --> decision_composition;
        decision_composition --> explanation;
        explanation --> ui_composition;
        ui_composition -.-> escalation;
        ui_composition -.-> __end__;
        escalation --> __end__;
"""

from __future__ import annotations

import inspect

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from graph import schemas as _schemas_module
from graph.nodes.decision import decision_composition
from graph.nodes.eligibility import eligibility_evaluation
from graph.nodes.escalation import escalation
from graph.nodes.explanation import explanation
from graph.nodes.extraction import extraction
from graph.nodes.intake import intake_normalize
from graph.nodes.retrieval import policy_retrieval
from graph.nodes.risk import risk_anomaly
from graph.nodes.router import router
from graph.nodes.ui import ui_composition
from graph.schemas import Outcome
from graph.state import AdjudicationState

# Every Pydantic model / Enum defined in graph/schemas.py, gathered
# automatically rather than hand-listed so a newly added schema type is
# covered without editing this file. Passed to the checkpointer's serde
# below as an explicit allowlist — see compile_graph()'s docstring for why
# this is needed at all (our state stores these types directly, and
# langgraph's msgpack serde otherwise warns-and-allows on every unregistered
# type, forever, for every checkpoint read/write).
_CHECKPOINT_SCHEMA_TYPES = [
    obj
    for _name, obj in vars(_schemas_module).items()
    if inspect.isclass(obj) and obj.__module__ == _schemas_module.__name__
]


def _after_extraction(state: AdjudicationState) -> str:
    return "escalation" if state.get("escalation_reason") else "router"


def _after_router(state: AdjudicationState) -> str:
    routing = state["routing"]
    return "escalation" if routing.missing_fields else "policy_retrieval"


def _after_retrieval(state: AdjudicationState) -> list[str] | str:
    if not state["retrieval_had_coverage_hit"]:
        return "escalation"
    # Parallel fan-out: both branches write to disjoint state keys
    # (eligibility_result / risk_result — see graph/state.py module
    # docstring), so LangGraph runs them in the same superstep and
    # decision_composition (their shared next node) fires once both finish.
    return ["eligibility_evaluation", "risk_anomaly"]


def _after_ui(state: AdjudicationState) -> str:
    decision = state["decision"]
    return "escalation" if decision.outcome == Outcome.ESCALATE else END


def build_graph() -> StateGraph:
    """Returns the UNCOMPILED graph builder — call `.compile(checkpointer=...)`
    on it (see `compile_graph` below for the standard SqliteSaver setup, or
    compile with a different checkpointer directly in tests)."""
    graph = StateGraph(AdjudicationState)

    graph.add_node("intake_normalize", intake_normalize)
    graph.add_node("extraction", extraction)
    graph.add_node("router", router)
    graph.add_node("policy_retrieval", policy_retrieval)
    graph.add_node("eligibility_evaluation", eligibility_evaluation)
    graph.add_node("risk_anomaly", risk_anomaly)
    graph.add_node("decision_composition", decision_composition)
    graph.add_node("explanation", explanation)
    graph.add_node("escalation", escalation)
    graph.add_node("ui_composition", ui_composition)

    graph.add_edge(START, "intake_normalize")
    graph.add_edge("intake_normalize", "extraction")
    graph.add_conditional_edges("extraction", _after_extraction, ["escalation", "router"])
    graph.add_conditional_edges("router", _after_router, ["escalation", "policy_retrieval"])
    graph.add_conditional_edges(
        "policy_retrieval", _after_retrieval, ["escalation", "eligibility_evaluation", "risk_anomaly"]
    )
    graph.add_edge("eligibility_evaluation", "decision_composition")
    graph.add_edge("risk_anomaly", "decision_composition")
    graph.add_edge("decision_composition", "explanation")
    graph.add_edge("explanation", "ui_composition")
    graph.add_conditional_edges("ui_composition", _after_ui, ["escalation", END])
    graph.add_edge("escalation", END)

    return graph


async def compile_graph(db_path: str = "checkpoints/adjudicator.sqlite"):
    """Standard compilation with the file-backed async SQLite checkpointer —
    durable across restarts, keyed by thread_id (== claim_id), which is what
    makes `escalation`'s interrupt/resume actually work across separate HTTP
    requests (see agent-service's FastAPI routes).

    Built directly from an `aiosqlite` connection (instead of
    `AsyncSqliteSaver.from_conn_string`, which doesn't accept a `serde=`
    override) so we can pass an explicit `allowed_msgpack_modules` allowlist
    (every class in graph/schemas.py, see `_CHECKPOINT_SCHEMA_TYPES` above)
    to the serializer: our state schema stores our own Pydantic/enum types
    (`ClaimFacts`, `AuditEvent`, `Peril`, ...) directly, and without this,
    langgraph's default serde warns on every checkpoint read/write for each
    ("unregistered type... will be blocked in a future version") and would
    start hard-failing checkpoint restores once that happens. Passing the
    exact classes (rather than the blanket `allowed_msgpack_modules=True`)
    both silences the warning now and stays strict against anything NOT in
    our own schema module.

    Returns (graph, conn) — caller must `await conn.close()` on shutdown.
    """
    conn = await aiosqlite.connect(db_path)
    serde = JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_SCHEMA_TYPES)
    checkpointer = AsyncSqliteSaver(conn, serde=serde)
    await checkpointer.setup()
    graph = build_graph().compile(checkpointer=checkpointer)
    return graph, conn
