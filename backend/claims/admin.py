from django.contrib import admin

from .models import AuditEvent, Claim, Decision


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = ["claim_id", "policy_id", "status", "created_at", "updated_at"]
    list_filter = ["status", "policy_id"]
    search_fields = ["claim_id", "claimant_name"]


@admin.register(Decision)
class DecisionAdmin(admin.ModelAdmin):
    list_display = ["claim_id", "outcome", "amount", "confidence"]


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["claim_id", "event_type", "node", "occurred_at"]
    list_filter = ["event_type", "node"]
