"""Django's persistence layer for claims — the "database of record" the
brief asks Django to own, separate from (and downstream of) agent-service's
own LangGraph checkpoint. agent-service's checkpoint is the execution
engine's working state (needed to resume an interrupted graph run); these
models are the durable product-facing record built by observing the SSE
event stream as it's relayed through views.py — no LangGraph types or
imports here, by design (see the repo-level split in DESIGN.md).
"""

from django.db import models


class Claim(models.Model):
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_ESCALATED = "escalated"
    STATUS_AWAITING_CONFIRM = "awaiting_confirm"
    STATUS_DONE = "done"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_ESCALATED, "Escalated"),
        (STATUS_AWAITING_CONFIRM, "Awaiting confirmation"),
        (STATUS_DONE, "Done"),
    ]

    claim_id = models.CharField(max_length=64, primary_key=True)
    policy_id = models.CharField(max_length=64)
    policy_start_date = models.CharField(max_length=32)
    filed_date = models.CharField(max_length=32)
    claimant_name = models.CharField(max_length=200)
    claimant_gender = models.CharField(max_length=32)
    claimant_city = models.CharField(max_length=200)
    narrative_text = models.TextField()
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.claim_id} ({self.status})"


class Decision(models.Model):
    """At most one per claim. Only exists once `decision_composition` has
    actually run in the graph — a claim escalated earlier (missing field, no
    coverage, extraction failure) never gets one, by design; see
    graph/build_graph.py's docstring for why those paths never reach
    decision_composition."""

    claim = models.OneToOneField(Claim, on_delete=models.CASCADE, primary_key=True, related_name="decision")
    outcome = models.CharField(max_length=20)
    amount = models.FloatField()
    confidence = models.FloatField()
    escalation_reason = models.TextField(null=True, blank=True)
    ui_spec = models.JSONField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Decision({self.claim_id}: {self.outcome}, {self.amount})"


class AuditEvent(models.Model):
    """Django's own copy of agent-service's audit_log entries, persisted
    as they arrive over SSE — durable proof of what happened, independent of
    whether the agent-service process or its checkpoint file survives."""

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="audit_events")
    event_type = models.CharField(max_length=64)
    node = models.CharField(max_length=64)
    detail = models.TextField()
    occurred_at = models.CharField(max_length=64)  # ISO timestamp, verbatim from agent-service
    original_outcome = models.CharField(max_length=20, null=True, blank=True)
    original_amount = models.FloatField(null=True, blank=True)
    override_reason = models.TextField(null=True, blank=True)
    override_proposed_amount = models.IntegerField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["recorded_at", "id"]

    def __str__(self) -> str:
        return f"{self.claim_id}: {self.event_type} @ {self.node}"
