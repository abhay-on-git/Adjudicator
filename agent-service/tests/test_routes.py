"""HTTP-level tests for routes.py, run through FastAPI's TestClient (which
drives the real ASGI app, including the `lifespan` that compiles the graph
against a real file-backed checkpointer — the same code path production
uses). LLM calls are monkeypatched at the same seams as
tests/test_build_graph.py, so no network access or OPENAI_API_KEY is needed.

TestClient buffers the whole SSE body before returning (it's a real HTTP
client, not literally the streaming spike's timing harness — that's already
covered by DESIGN.md's spike evidence and the Django proxy test), so these
tests assert on EVENT SEQUENCE AND CONTENT, not on wire-level incremental
delivery.
"""

from __future__ import annotations

import json

import main
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from graph.nodes import explanation as explanation_module
from graph.nodes import extraction as extraction_module
from graph.schemas import ExplanationOutput, ExtractedNarrativeFacts, LineItem, Peril
from routes import _validate_confirmation_response


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Splits a buffered SSE body into (event_name, parsed_json_data) pairs."""
    events = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        events.append((event_name, data))
    return events


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CHECKPOINT_DB_PATH", str(tmp_path / "test.sqlite"))
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def mock_clean_llm_calls(monkeypatch):
    facts = ExtractedNarrativeFacts(
        date_of_loss="2024-07-30",
        perils=[Peril.WATER_DAMAGE],
        line_items=[
            LineItem(description="Kitchen flooring repair", category="flooring",
                      claimed_amount=8000, evidence_tags=["sudden_discharge"])
        ],
        narrative_summary="Sudden pipe burst under kitchen sink.",
    )

    async def _fake_extraction(narrative_text, extra_instruction=""):
        return facts, None

    async def _fake_explanation(context):
        return ExplanationOutput(narrative="Test explanation.", cited_clause_ids=[])

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", _fake_extraction)
    monkeypatch.setattr(explanation_module, "_call_llm", _fake_explanation)


CLEAN_SUBMISSION = {
    "claim_id": "CLM-ROUTE-001",
    "policy_id": "POL-HOME-01",
    "policy_start_date": "2024-01-10",
    "filed_date": "2024-08-02",
    "claimant_name": "Priya Nair",
    "claimant_gender": "female",
    "claimant_city": "Pune",
    "narrative_text": "Pipe burst under my kitchen sink, claiming Rs 8000 for flooring repair.",
}


def test_adjudicate_streams_node_events_then_awaits_confirmation(client, mock_clean_llm_calls):
    resp = client.post("/claims/CLM-ROUTE-001/adjudicate", json=CLEAN_SUBMISSION)
    assert resp.status_code == 200
    events = _parse_sse(resp.text)

    event_names = [name for name, _ in events]
    assert event_names[-1] == "awaiting_confirmation"
    assert "escalated" not in event_names
    assert "done" not in event_names
    node_complete_nodes = [data["node"] for name, data in events if name == "node_complete"]
    assert node_complete_nodes[:4] == [
        "intake_normalize", "extraction", "router", "policy_retrieval",
    ]
    assert set(node_complete_nodes[4:6]) == {"eligibility_evaluation", "risk_anomaly"}
    assert node_complete_nodes[6:] == [
        "decision_composition", "evidence_reconciliation", "explanation", "ui_composition",
    ]
    confirm_data = events[-1][1]
    assert confirm_data["kind"] == "confirmation"
    assert confirm_data["decision_so_far"]["outcome"] != "escalate"

    status = client.get("/claims/CLM-ROUTE-001").json()
    assert status["status"] == "awaiting_confirmation"

    resume_resp = client.post("/claims/CLM-ROUTE-001/resume", json={"human_response": "approve"})
    assert resume_resp.status_code == 200
    resume_events = _parse_sse(resume_resp.text)
    assert resume_events[-1][0] == "done"
    assert [d.get("node") for _, d in resume_events if d.get("node")] == ["commit_decision"]
    assert resume_events[-1][1]["decision"]["outcome"] != "escalate"

    final_status = client.get("/claims/CLM-ROUTE-001").json()
    assert final_status["status"] == "done"


def test_adjudicate_claim_id_mismatch_rejected(client):
    resp = client.post("/claims/CLM-OTHER/adjudicate", json=CLEAN_SUBMISSION)
    assert resp.status_code == 400


def test_override_resume_payload_requires_reason_and_valid_optional_amount():
    with pytest.raises(HTTPException, match="non-empty reason"):
        _validate_confirmation_response({"action": "override", "reason": " "})
    with pytest.raises(HTTPException, match="non-negative integer"):
        _validate_confirmation_response(
            {"action": "override", "reason": "Wrong scope", "proposed_amount": 12.5}
        )
    _validate_confirmation_response(
        {"action": "override", "reason": "Wrong scope", "proposed_amount": 60000}
    )


def test_missing_field_escalates_then_resume_completes(client, monkeypatch):
    facts = ExtractedNarrativeFacts(
        date_of_loss=None,  # missing required field -> router escalates
        perils=[Peril.WATER_DAMAGE],
        line_items=[LineItem(description="Flooring repair", category="flooring", claimed_amount=5000)],
        narrative_summary="Water damage, date unclear.",
    )

    async def _fake_extraction(narrative_text, extra_instruction=""):
        return facts, None

    monkeypatch.setattr(extraction_module, "_call_llm_with_backoff", _fake_extraction)

    submission = {**CLEAN_SUBMISSION, "claim_id": "CLM-ROUTE-002"}
    resp = client.post("/claims/CLM-ROUTE-002/adjudicate", json=submission)
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events[-1][0] == "escalated"
    assert "policy_retrieval" not in [d.get("node") for _, d in events]

    status = client.get("/claims/CLM-ROUTE-002").json()
    assert status["status"] == "escalated"
    assert len(status["interrupts"]) == 1

    resume_resp = client.post(
        "/claims/CLM-ROUTE-002/resume", json={"human_response": "manually approved by adjuster"}
    )
    assert resume_resp.status_code == 200
    resume_events = _parse_sse(resume_resp.text)
    assert [d.get("node") for _, d in resume_events if d.get("node")] == ["escalation"]

    final_status = client.get("/claims/CLM-ROUTE-002").json()
    assert final_status["status"] == "done"
    assert final_status["decision"] is not None
    assert final_status["decision"]["outcome"] == "approve"


def test_resume_without_pending_escalation_returns_409(client):
    resp = client.post("/claims/CLM-NEVER-STARTED/resume", json={"human_response": "x"})
    assert resp.status_code == 409


def test_get_unknown_claim_returns_404(client):
    resp = client.get("/claims/CLM-DOES-NOT-EXIST")
    assert resp.status_code == 404
