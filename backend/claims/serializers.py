from rest_framework import serializers

from .models import AuditEvent, Claim, Decision


class ClaimSubmitSerializer(serializers.Serializer):
    """Validates a new claim submission. `claim_id` is optional — the view
    generates one when omitted (the realistic default for a real
    submission form); the eval harness (agent-service/eval/run_eval.py)
    supplies the fixture's own claim_id explicitly so results line up with
    ground_truth.json."""

    claim_id = serializers.CharField(required=False, allow_blank=False, max_length=64)
    policy_id = serializers.CharField()
    policy_start_date = serializers.CharField()
    filed_date = serializers.CharField()
    claimant_name = serializers.CharField()
    claimant_gender = serializers.CharField()
    claimant_city = serializers.CharField()
    narrative_text = serializers.CharField()


class ResumeSerializer(serializers.Serializer):
    human_response = serializers.JSONField()


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = ["event_type", "node", "detail", "occurred_at"]


class DecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Decision
        fields = ["outcome", "amount", "confidence", "escalation_reason", "ui_spec"]


class ClaimDetailSerializer(serializers.ModelSerializer):
    decision = DecisionSerializer(read_only=True, required=False)
    audit_events = AuditEventSerializer(many=True, read_only=True)

    class Meta:
        model = Claim
        fields = [
            "claim_id", "policy_id", "policy_start_date", "filed_date",
            "claimant_name", "claimant_gender", "claimant_city", "narrative_text",
            "status", "created_at", "updated_at", "decision", "audit_events",
        ]
