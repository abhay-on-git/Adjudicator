"""HTTP surface for the compiled LangGraph adjudication graph.

This is the ONLY place that talks to the graph over HTTP — Django's
`claims` app proxies these routes (see backend/claims/views.py and the
streaming spike in DESIGN.md) rather than calling agent-service's Python
directly, keeping the process boundary the spec asks for.

Three routes, one per graph lifecycle stage:
  - POST /claims/{claim_id}/adjudicate — start a new run. `claim_id` becomes
    the LangGraph `thread_id`, so a resume later (or a page refresh calling
    GET) finds the same checkpointed run.
  - POST /claims/{claim_id}/resume — continue a run that's paused inside
    `escalation`'s `interrupt()`, with the human's response as the resume
    value.
  - GET /claims/{claim_id} — read-only snapshot of the current checkpoint,
    for polling/refresh without re-running anything.

Both POST routes stream Server-Sent Events: one `node_complete` event per
graph superstep (carrying exactly what that node returned — audit entries,
new facts, etc.), then a terminal `escalated` or `done` event. This is the
real pipeline the streaming spike (DESIGN.md) was de-risking; Django relays
these bytes unmodified.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langgraph.types import Command, Interrupt
from pydantic import BaseModel

from graph.state import initial_state

router = APIRouter(prefix="/claims", tags=["claims"])


class ClaimSubmissionRequest(BaseModel):
    """Mirrors intake_normalize's expected `raw_input` shape exactly — see
    graph/nodes/intake.py. Django's submit endpoint forwards this as-is."""

    claim_id: str
    policy_id: str
    policy_start_date: str
    filed_date: str
    claimant_name: str
    claimant_gender: str
    claimant_city: str
    narrative_text: str


class ResumeRequest(BaseModel):
    human_response: Any


def _json_safe(value: Any) -> Any:
    """Recursively converts Pydantic models / Enums / Interrupt objects
    (anything that can show up inside AdjudicationState or a LangGraph
    Interrupt) into plain JSON-serializable data."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Interrupt):
        return {"interrupt_id": value.id, **_json_safe(value.value)}
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(_json_safe(data))}\n\n".encode("utf-8")


def _thread_config(claim_id: str) -> dict:
    return {"configurable": {"thread_id": claim_id}}


async def _stream_graph_run(graph, graph_input: Any, claim_id: str) -> AsyncIterator[bytes]:
    """Shared driver for both POST routes: runs (or resumes) the graph,
    yielding one SSE `node_complete` event per completed superstep. Stops
    and yields a terminal `escalated` event the moment the graph pauses;
    otherwise yields a terminal `done` event with the final decision/UI spec
    once the graph actually finishes."""
    config = _thread_config(claim_id)
    interrupted = False

    async for chunk in graph.astream(graph_input, config=config, stream_mode="updates"):
        if "__interrupt__" in chunk:
            interrupted = True
            interrupt_obj = chunk["__interrupt__"][0]
            yield _sse("escalated", {"claim_id": claim_id, **_json_safe(interrupt_obj)})
            break
        for node_name, update in chunk.items():
            yield _sse("node_complete", {"claim_id": claim_id, "node": node_name, "update": _json_safe(update)})

    if not interrupted:
        snapshot = await graph.aget_state(config)
        values = snapshot.values
        yield _sse(
            "done",
            {
                "claim_id": claim_id,
                "decision": _json_safe(values.get("decision")),
                "ui_spec": values.get("ui_spec"),
            },
        )


@router.post("/{claim_id}/adjudicate")
async def adjudicate(claim_id: str, submission: ClaimSubmissionRequest, request: Request):
    if submission.claim_id != claim_id:
        raise HTTPException(status_code=400, detail="claim_id in path and body must match")

    graph = request.app.state.graph
    graph_input = initial_state(claim_id, submission.model_dump())

    return StreamingResponse(_stream_graph_run(graph, graph_input, claim_id), media_type="text/event-stream")


@router.post("/{claim_id}/resume")
async def resume(claim_id: str, body: ResumeRequest, request: Request):
    graph = request.app.state.graph
    config = _thread_config(claim_id)
    snapshot = await graph.aget_state(config)
    if not snapshot.interrupts:
        raise HTTPException(status_code=409, detail="Claim is not currently paused for escalation.")

    return StreamingResponse(
        _stream_graph_run(graph, Command(resume=body.human_response), claim_id),
        media_type="text/event-stream",
    )


@router.get("/{claim_id}")
async def get_claim_status(claim_id: str, request: Request):
    graph = request.app.state.graph
    config = _thread_config(claim_id)
    snapshot = await graph.aget_state(config)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail=f"No run found for claim_id={claim_id!r}")

    values = snapshot.values
    if snapshot.interrupts:
        status = "escalated"
    elif not snapshot.next:
        # No pending interrupt and no more nodes queued: the graph reached
        # END. True even for a claim that escalated early (extraction/router/
        # policy_retrieval) and was resolved by a human without ever
        # reaching decision_composition — `decision` may legitimately be
        # None in that case.
        status = "done"
    else:
        status = "in_progress"

    return {
        "claim_id": claim_id,
        "status": status,
        "decision": _json_safe(values.get("decision")),
        "ui_spec": values.get("ui_spec"),
        "interrupts": [_json_safe(i) for i in snapshot.interrupts],
        "audit_log": _json_safe(values.get("audit_log", [])),
    }
