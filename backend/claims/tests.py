"""Tests for the claims app's real views (ClaimListCreateView, ClaimDetailView,
ClaimResumeView). `httpx.stream` is monkeypatched to a fake context manager
that replays a scripted SSE byte sequence — these tests never hit a real
agent-service process, but exercise the exact same relay-and-persist code
path (`_stream_and_persist`) that would run against the real one.
"""

import json
import unittest.mock as mock

from django.test import Client, TestCase

from claims import views
from claims.models import AuditEvent, Claim, Decision


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


class _FakeUpstreamContext:
    """Stands in for the object `httpx.stream(...)` returns when used as a
    context manager. `iter_raw()` replays pre-scripted byte chunks, exactly
    like the real streaming spike's upstream object."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.calls: list[tuple[str, str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def iter_raw(self):
        return iter(self._chunks)


CLEAN_RUN_CHUNKS = [
    _sse("node_complete", {"claim_id": "CLM-DJ-001", "node": "intake_normalize",
                            "update": {"audit_log": [{"event_type": "INTAKE_COMPLETE", "node": "intake_normalize",
                                                       "detail": "Normalized.", "timestamp": "2024-01-01T00:00:00Z"}]}}),
    _sse("node_complete", {"claim_id": "CLM-DJ-001", "node": "decision_composition",
                            "update": {
                                "decision": {"outcome": "approve", "amount": 8000.0, "confidence": 0.95,
                                             "escalation_reason": None},
                                "audit_log": [{"event_type": "DECISION_COMPOSED", "node": "decision_composition",
                                               "detail": "approve 8000.0", "timestamp": "2024-01-01T00:00:05Z"}],
                            }}),
    _sse("node_complete", {"claim_id": "CLM-DJ-001", "node": "ui_composition",
                            "update": {"ui_spec": {"blocks": [{"type": "outcome"}]},
                                       "audit_log": [{"event_type": "UI_COMPOSED", "node": "ui_composition",
                                                      "detail": "1 block(s).", "timestamp": "2024-01-01T00:00:06Z"}]}}),
    _sse("done", {"claim_id": "CLM-DJ-001",
                  "decision": {"outcome": "approve", "amount": 8000.0, "confidence": 0.95, "escalation_reason": None},
                  "ui_spec": {"blocks": [{"type": "outcome"}]}}),
]

ESCALATED_RUN_CHUNKS = [
    _sse("node_complete", {"claim_id": "CLM-DJ-002", "node": "router",
                            "update": {"audit_log": [{"event_type": "ROUTING_COMPLETE", "node": "router",
                                                       "detail": "missing field", "timestamp": "2024-01-01T00:00:00Z"}]}}),
    _sse("escalated", {"claim_id": "CLM-DJ-002", "interrupt_id": "abc",
                        "reason": "Missing required field(s): ['date_of_loss'].", "decision_so_far": None}),
]

RESUME_RUN_CHUNKS = [
    _sse("node_complete", {"claim_id": "CLM-DJ-002", "node": "escalation",
                            "update": {"audit_log": [{"event_type": "ESCALATION_RESOLVED", "node": "escalation",
                                                       "detail": "resolved by human", "timestamp": "2024-01-01T00:01:00Z"}]}}),
    _sse("done", {"claim_id": "CLM-DJ-002", "decision": None, "ui_spec": None}),
]

SUBMISSION_FIELDS = {
    "policy_id": "POL-HOME-01",
    "policy_start_date": "2024-01-10",
    "filed_date": "2024-08-02",
    "claimant_name": "Priya Nair",
    "claimant_gender": "female",
    "claimant_city": "Pune",
    "narrative_text": "Pipe burst, claiming Rs 8000.",
}


class ClaimSubmitAndPersistTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_submit_streams_events_and_persists_full_record(self):
        with mock.patch.object(views.httpx, "stream") as fake_stream:
            fake_stream.return_value = _FakeUpstreamContext(CLEAN_RUN_CHUNKS)
            body = {**SUBMISSION_FIELDS, "claim_id": "CLM-DJ-001"}
            response = self.client.post(
                "/api/claims/", data=json.dumps(body), content_type="application/json"
            )
            self.assertEqual(response.status_code, 200)
            content = b"".join(response.streaming_content)
            self.assertIn(b"event: done", content)

            call_args = fake_stream.call_args
            self.assertEqual(call_args.args[0], "POST")
            self.assertTrue(call_args.args[1].endswith("/claims/CLM-DJ-001/adjudicate"))

        claim = Claim.objects.get(claim_id="CLM-DJ-001")
        self.assertEqual(claim.status, Claim.STATUS_DONE)
        decision = Decision.objects.get(claim_id="CLM-DJ-001")
        self.assertEqual(decision.outcome, "approve")
        self.assertEqual(decision.amount, 8000.0)
        self.assertEqual(decision.ui_spec, {"blocks": [{"type": "outcome"}]})
        event_types = list(AuditEvent.objects.filter(claim_id="CLM-DJ-001").values_list("event_type", flat=True))
        self.assertEqual(event_types, ["INTAKE_COMPLETE", "DECISION_COMPOSED", "UI_COMPOSED"])

    def test_duplicate_claim_id_rejected(self):
        Claim.objects.create(claim_id="CLM-DJ-DUP", status=Claim.STATUS_DONE, **SUBMISSION_FIELDS)
        body = {**SUBMISSION_FIELDS, "claim_id": "CLM-DJ-DUP"}
        response = self.client.post("/api/claims/", data=json.dumps(body), content_type="application/json")
        self.assertEqual(response.status_code, 409)

    def test_claim_id_auto_generated_when_omitted(self):
        with mock.patch.object(views.httpx, "stream") as fake_stream:
            fake_stream.return_value = _FakeUpstreamContext(
                [_sse("done", {"claim_id": "generated", "decision": None, "ui_spec": None})]
            )
            response = self.client.post(
                "/api/claims/", data=json.dumps(SUBMISSION_FIELDS), content_type="application/json"
            )
            self.assertEqual(response.status_code, 200)
            b"".join(response.streaming_content)
        self.assertEqual(Claim.objects.count(), 1)
        self.assertTrue(Claim.objects.first().claim_id.startswith("CLM-"))


class ClaimDetailViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.claim = Claim.objects.create(claim_id="CLM-DJ-010", status=Claim.STATUS_DONE, **SUBMISSION_FIELDS)
        Decision.objects.create(claim=self.claim, outcome="approve", amount=8000.0, confidence=0.9,
                                 ui_spec={"blocks": []})
        AuditEvent.objects.create(claim=self.claim, event_type="INTAKE_COMPLETE", node="intake_normalize",
                                   detail="x", occurred_at="2024-01-01T00:00:00Z")

    def test_get_serves_from_db_and_never_calls_agent_service(self):
        with mock.patch.object(views.httpx, "stream") as fake_stream:
            response = self.client.get(f"/api/claims/{self.claim.claim_id}/")
            fake_stream.assert_not_called()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["decision"]["outcome"], "approve")
        self.assertEqual(len(payload["audit_events"]), 1)

    def test_get_unknown_claim_returns_404(self):
        response = self.client.get("/api/claims/CLM-NOPE/")
        self.assertEqual(response.status_code, 404)


class ClaimResumeViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        Claim.objects.create(claim_id="CLM-DJ-002", status=Claim.STATUS_ESCALATED, **SUBMISSION_FIELDS)

    def test_resume_persists_resolution_event_and_marks_done(self):
        with mock.patch.object(views.httpx, "stream") as fake_stream:
            fake_stream.return_value = _FakeUpstreamContext(RESUME_RUN_CHUNKS)
            response = self.client.post(
                "/api/claims/CLM-DJ-002/resume/",
                data=json.dumps({"human_response": "manually approved"}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            content = b"".join(response.streaming_content)
            self.assertIn(b"event: done", content)

        claim = Claim.objects.get(claim_id="CLM-DJ-002")
        self.assertEqual(claim.status, Claim.STATUS_DONE)
        self.assertTrue(
            AuditEvent.objects.filter(claim_id="CLM-DJ-002", event_type="ESCALATION_RESOLVED").exists()
        )

    def test_resume_unknown_claim_returns_404(self):
        response = self.client.post(
            "/api/claims/CLM-NOPE/resume/",
            data=json.dumps({"human_response": "x"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)


class EscalationPersistenceTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_escalated_event_marks_claim_escalated_and_records_reason(self):
        with mock.patch.object(views.httpx, "stream") as fake_stream:
            fake_stream.return_value = _FakeUpstreamContext(ESCALATED_RUN_CHUNKS)
            body = {**SUBMISSION_FIELDS, "claim_id": "CLM-DJ-002"}
            response = self.client.post("/api/claims/", data=json.dumps(body), content_type="application/json")
            self.assertEqual(response.status_code, 200)
            content = b"".join(response.streaming_content)
            self.assertIn(b"event: escalated", content)

        claim = Claim.objects.get(claim_id="CLM-DJ-002")
        self.assertEqual(claim.status, Claim.STATUS_ESCALATED)
        escalated_event = AuditEvent.objects.get(claim_id="CLM-DJ-002", event_type="ESCALATED")
        self.assertIn("date_of_loss", escalated_event.detail)
