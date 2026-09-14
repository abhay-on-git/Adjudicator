"""Views for the claims app.

`spike_stream_proxy` is the original streaming-proxy spike (DESIGN.md,
"Streaming transport" fork) — kept as historical evidence, no longer
load-bearing. The three real views below (`ClaimListCreateView`,
`ClaimDetailView`, `ClaimResumeView`) are the actual product surface: the two
POST views proxy agent-service's SSE routes byte-for-byte to the client
(same proven relay pattern as the spike) while ALSO parsing each complete
SSE event out of the stream to persist it into Django's own models
(`models.py`) — the durable database-of-record, independent of
agent-service's own checkpoint. The GET view never calls agent-service at
all; it serves entirely from what's already been persisted.
"""

import json
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from django.http import StreamingHttpResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AuditEvent, Claim, Decision
from .serializers import ClaimDetailSerializer, ClaimSubmitSerializer, ResumeSerializer

logger = logging.getLogger(__name__)

AGENT_SERVICE_BASE_URL = "http://127.0.0.1:8001"
# connect is short; read must cover a full extraction/explanation LLM call
# because SSE is silent between nodes. A 120s total/read timeout made the UI
# freeze on "Extract facts" when MiniMax thought longer than two minutes.
UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


def spike_stream_proxy(request):
    """Proxies agent-service's /spike/stream chunk-by-chunk.

    Streams each chunk to the client as soon as it arrives from agent-service,
    and prefixes it with the proxy's own receive timestamp so we can compare
    agent-service send time vs. Django receive time vs. client receive time
    from one log line each.
    """

    def relay():
        with httpx.stream("GET", f"{AGENT_SERVICE_BASE_URL}/spike/stream", timeout=30) as upstream:
            for chunk in upstream.iter_raw():
                if not chunk:
                    continue
                stamped = chunk.decode("utf-8").rstrip("\n")
                stamped += f" | django_proxied_at={time.time():.3f}\n\n"
                yield stamped.encode("utf-8")

    response = StreamingHttpResponse(relay(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_sse_block(raw_block: str) -> tuple[str | None, dict | None]:
    event_name = None
    data = None
    for line in raw_block.splitlines():
        if line.startswith("event: "):
            event_name = line[len("event: "):]
        elif line.startswith("data: "):
            data = json.loads(line[len("data: "):])
    return event_name, data


def _persist_node_complete(claim_id: str, data: dict) -> None:
    node = data.get("node")
    update = data.get("update") or {}
    for entry in update.get("audit_log", []):
        AuditEvent.objects.create(
            claim_id=claim_id,
            event_type=entry.get("event_type", ""),
            node=entry.get("node", node),
            detail=entry.get("detail", ""),
            occurred_at=entry.get("timestamp", ""),
            original_outcome=entry.get("original_outcome"),
            original_amount=entry.get("original_amount"),
            override_reason=entry.get("override_reason"),
            override_proposed_amount=entry.get("override_proposed_amount"),
        )
    if node == "decision_composition" and update.get("decision") is not None:
        decision = update["decision"]
        Decision.objects.update_or_create(
            claim_id=claim_id,
            defaults=dict(
                outcome=decision.get("outcome"),
                amount=decision.get("amount"),
                confidence=decision.get("confidence"),
                escalation_reason=decision.get("escalation_reason"),
            ),
        )
    if node == "ui_composition" and update.get("ui_spec") is not None:
        Decision.objects.filter(claim_id=claim_id).update(ui_spec=update["ui_spec"])


def _persist_escalated(claim_id: str, data: dict) -> None:
    Claim.objects.filter(claim_id=claim_id).update(status=Claim.STATUS_ESCALATED)
    AuditEvent.objects.create(
        claim_id=claim_id,
        event_type="ESCALATED",
        node="escalation",
        detail=data.get("reason", ""),
        occurred_at=_now_iso(),
    )


def _persist_awaiting_confirmation(claim_id: str, data: dict) -> None:
    Claim.objects.filter(claim_id=claim_id).update(status=Claim.STATUS_AWAITING_CONFIRM)
    AuditEvent.objects.create(
        claim_id=claim_id,
        event_type="AWAITING_CONFIRMATION",
        node="commit_decision",
        detail=data.get("reason", ""),
        occurred_at=_now_iso(),
    )


def _persist_done(claim_id: str, data: dict) -> None:
    Claim.objects.filter(claim_id=claim_id).update(status=Claim.STATUS_DONE)
    decision = data.get("decision")
    if decision is not None:
        Decision.objects.update_or_create(
            claim_id=claim_id,
            defaults=dict(
                outcome=decision.get("outcome"),
                amount=decision.get("amount"),
                confidence=decision.get("confidence"),
                escalation_reason=decision.get("escalation_reason"),
                ui_spec=data.get("ui_spec"),
            ),
        )


def _persist_sse_event(claim_id: str, event_name: str, data: dict) -> None:
    if event_name == "node_complete":
        _persist_node_complete(claim_id, data)
    elif event_name == "escalated":
        _persist_escalated(claim_id, data)
    elif event_name == "awaiting_confirmation":
        _persist_awaiting_confirmation(claim_id, data)
    elif event_name == "done":
        _persist_done(claim_id, data)


def _stream_and_persist(claim_id: str, upstream_url: str, payload: dict):
    """Generator fed to StreamingHttpResponse: relays every raw byte chunk
    from agent-service to the client IMMEDIATELY (this is the property the
    streaming spike proved works end-to-end), and independently buffers text
    to find complete SSE events (blank-line-terminated) to persist — chunk
    boundaries from chunked transfer encoding don't necessarily line up with
    event boundaries, so persistence parsing is decoupled from relaying."""
    buffer = ""
    with httpx.stream("POST", upstream_url, json=payload, timeout=UPSTREAM_TIMEOUT) as upstream:
        for chunk in upstream.iter_raw():
            if not chunk:
                continue
            yield chunk
            buffer += chunk.decode("utf-8")
            while "\n\n" in buffer:
                raw_block, buffer = buffer.split("\n\n", 1)
                event_name, data = _parse_sse_block(raw_block)
                if event_name and data is not None:
                    try:
                        _persist_sse_event(claim_id, event_name, data)
                    except Exception:
                        # Persistence must never abort the live stream — a missing
                        # migration here used to kill the generator after Intake,
                        # so the UI froze on Extract facts while the agent kept going.
                        logger.exception(
                            "Failed to persist SSE event %s for %s; continuing stream",
                            event_name,
                            claim_id,
                        )


def _sse_response(generator) -> StreamingHttpResponse:
    response = StreamingHttpResponse(generator, content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


class ClaimListCreateView(APIView):
    """POST /api/claims/ — submit a new claim and stream its adjudication."""

    def post(self, request):
        serializer = ClaimSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)
        claim_id = fields.pop("claim_id", None) or f"CLM-{uuid4().hex[:10]}"

        if Claim.objects.filter(claim_id=claim_id).exists():
            return Response({"detail": f"claim_id {claim_id!r} already submitted."}, status=409)

        Claim.objects.create(claim_id=claim_id, status=Claim.STATUS_IN_PROGRESS, **fields)

        payload = {**fields, "claim_id": claim_id}
        upstream_url = f"{AGENT_SERVICE_BASE_URL}/claims/{claim_id}/adjudicate"
        return _sse_response(_stream_and_persist(claim_id, upstream_url, payload))


class ClaimDetailView(APIView):
    """GET /api/claims/<id>/ — served entirely from Django's own persisted
    record; never calls agent-service."""

    def get(self, request, claim_id: str):
        try:
            claim = Claim.objects.select_related("decision").prefetch_related("audit_events").get(claim_id=claim_id)
        except Claim.DoesNotExist:
            return Response({"detail": f"No claim {claim_id!r} found."}, status=404)
        return Response(ClaimDetailSerializer(claim).data)


class ClaimResumeView(APIView):
    """POST /api/claims/<id>/resume/ — proxy + persist, same pattern as
    submission, for a claim currently paused in agent-service's `escalation`
    or `commit_decision` node."""

    def post(self, request, claim_id: str):
        if not Claim.objects.filter(claim_id=claim_id).exists():
            return Response({"detail": f"No claim {claim_id!r} found."}, status=404)

        serializer = ResumeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        upstream_url = f"{AGENT_SERVICE_BASE_URL}/claims/{claim_id}/resume"
        payload = {"human_response": serializer.validated_data["human_response"]}
        return _sse_response(_stream_and_persist(claim_id, upstream_url, payload))
