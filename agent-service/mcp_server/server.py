"""The MCP server exposing the data-layer tools graph nodes call.

Per DESIGN.md's "Tool granularity (MCP)" section: these tools are called
deterministically by graph nodes as a fixed part of the control flow (e.g.
`policy_retrieval` always calls `search_policy`; `eligibility_evaluation`
always calls `compute_payout`). They are NOT exposed to the LLM as a
free-choice tool-calling surface — the graph decides which tool runs and
when, not the model. Read tools are annotated `read_only_hint=True`
(`search_policy`, `get_clause`, and `compute_payout`, which has no side
effects even though it "computes" rather than "reads"). The one mutating
tool (`flag_for_review`, P1) will carry no such hint and is gated behind a
confirmation step rather than called freely — see DESIGN.md.

P0 scope (agreed): `search_policy` and `compute_payout` are real MCP tools
end-to-end. `get_clause` is included here too since it is a near-zero-cost
wrapper around the same `policy_store` index and directly serves the spec's
"exact text, for verification" requirement for the clause evidence panel.
`get_claim_history` is a plain internal function for P0 (see
graph/nodes/risk.py) and `flag_for_review` is deferred to P1 entirely.
"""

from __future__ import annotations

# mcp>=2.0 renamed FastMCP -> MCPServer (mcp.server.fastmcp removed, not just
# deprecated). Confirmed against installed mcp==2.2.0 and the SDK's
# docs/whats-new.md migration notes, not from (stale) training-data memory —
# see master instructions on fetching current docs for LangGraph/MCP.
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from graph.schemas import ClaimFacts, ClauseRef, EligibilityResult
from mcp_server.policy_store import load_policy_index, search
from rules.payout import compute_payout as _compute_payout

mcp = MCPServer("venxr-adjudicator-data-layer")
_POLICY_INDEX = load_policy_index()


@mcp.tool(
    title="Search Policy",
    description="Clause-level retrieval over one policy's fixture text. Returns "
    "the top-matching clauses with their clause_id, title, text, and a "
    "relevance score, so every downstream citation has an exact clause_id to "
    "point at. Always call with a specific policy_id — this tool does not "
    "search across policies.",
    annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
)
def search_policy(query: str, policy_id: str, top_k: int = 5) -> list[ClauseRef]:
    hits = search(_POLICY_INDEX, query, policy_id, top_k=top_k)
    return [
        ClauseRef(
            policy_id=clause.policy_id,
            clause_id=clause.clause_id,
            title=clause.title,
            text=clause.text,
            relevance_score=score,
        )
        for clause, score in hits
    ]


@mcp.tool(
    title="Get Clause",
    description="Exact text of one specific clause, by policy_id and clause_id. "
    "Use this to verify a clause you already have an ID for (e.g. before citing "
    "it in an explanation), not to discover new clauses — use search_policy for "
    "that.",
    annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
)
def get_clause(policy_id: str, clause_id: str) -> ClauseRef | None:
    clause = _POLICY_INDEX.get(policy_id, clause_id)
    if clause is None:
        return None
    return ClauseRef(
        policy_id=clause.policy_id,
        clause_id=clause.clause_id,
        title=clause.title,
        text=clause.text,
        relevance_score=1.0,
    )


@mcp.tool(
    title="Compute Payout",
    description="THE deterministic rules engine. Given typed claim facts and "
    "the retrieved clauses, returns a per-line-item eligibility breakdown "
    "(allowed/reduced/excluded, with governing clause IDs) and the total "
    "payable amount. This is the ONLY place a rupee figure or a coverage "
    "verdict is produced anywhere in this system — no LLM is in this call "
    "path. Deterministic and idempotent: identical inputs always produce an "
    "identical result.",
    annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
)
def compute_payout(facts: ClaimFacts, clauses: list[ClauseRef]) -> EligibilityResult:
    return _compute_payout(facts, clauses)


if __name__ == "__main__":
    mcp.run()
