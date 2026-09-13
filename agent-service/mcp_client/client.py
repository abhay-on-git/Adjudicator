"""The MCP client wrapper graph nodes call.

Transport: in-process (`mcp.client.Client` connected directly to the
`MCPServer` instance in the same Python process — the SDK's documented
in-memory pattern), not a subprocess/stdio or HTTP transport. This is a
deliberate simplification for a take-home: DESIGN.md already establishes
that these tools are a deterministic data-layer interface owned by
agent-service, not an arbitrary external service, so there's no
auditability or architecture benefit to paying for a subprocess boundary
here. Swapping to stdio/HTTP transport later would only mean changing how
`_client()` below constructs its `Client`, not the node code that calls
these functions.

Every function here is a plain async wrapper: serialize typed Pydantic
arguments to JSON-compatible dicts, call the tool, parse the structured
result back into the typed model. Graph nodes call these directly and
unconditionally — the LLM never sees these functions or chooses whether to
call them (see DESIGN.md "Tool granularity (MCP)").
"""

from __future__ import annotations

from mcp.client import Client

from graph.schemas import ClaimFacts, ClauseRef, EligibilityResult, ReviewFlagResult
from mcp_server.server import mcp


async def search_policy(query: str, policy_id: str, top_k: int = 5) -> list[ClauseRef]:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_policy", {"query": query, "policy_id": policy_id, "top_k": top_k}
        )
        if result.is_error:
            raise RuntimeError(f"search_policy tool call failed: {result.content}")
        raw = result.structured_content["result"] if result.structured_content else []
        return [ClauseRef(**item) for item in raw]


async def get_clause(policy_id: str, clause_id: str) -> ClauseRef | None:
    async with Client(mcp) as client:
        result = await client.call_tool("get_clause", {"policy_id": policy_id, "clause_id": clause_id})
        if result.is_error:
            raise RuntimeError(f"get_clause tool call failed: {result.content}")
        if not result.structured_content:
            return None
        # `ClauseRef | None` is a union, not a bare object type, so this one
        # IS wrapped under "result" (same reasoning as the list case above —
        # a union return type isn't itself a JSON object schema).
        raw = result.structured_content.get("result")
        return ClauseRef(**raw) if raw else None


async def compute_payout(facts: ClaimFacts, clauses: list[ClauseRef]) -> EligibilityResult:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "compute_payout",
            {
                "facts": facts.model_dump(mode="json"),
                "clauses": [c.model_dump(mode="json") for c in clauses],
            },
        )
        if result.is_error:
            raise RuntimeError(f"compute_payout tool call failed: {result.content}")
        # A tool whose return type is itself an object (EligibilityResult) is
        # NOT wrapped under a "result" key the way list-returning tools are
        # (list responses get wrapped because a JSON-Schema tool result must
        # be an object at the top level) — the object's own fields are the
        # top-level structured_content here.
        return EligibilityResult(**result.structured_content)


async def flag_for_review(claim_id: str, reason: str) -> ReviewFlagResult:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "flag_for_review",
            {"claim_id": claim_id, "reason": reason},
        )
        if result.is_error:
            raise RuntimeError(f"flag_for_review tool call failed: {result.content}")
        return ReviewFlagResult(**result.structured_content)
