"""Shared interrupt payload helpers.

Two interrupt *reasons*, two payload `kind` values — never fold them into one
node (see DESIGN.md). Routes, eval, and GET-status all need to tell them apart
from the same checkpointed Interrupt object.
"""

from __future__ import annotations

from typing import Any

KIND_ESCALATION = "escalation"
KIND_CONFIRMATION = "confirmation"


def interrupt_kind(interrupt_obj: Any) -> str:
    payload = getattr(interrupt_obj, "value", interrupt_obj)
    if isinstance(payload, dict):
        raw = payload.get("kind")
        if raw in (KIND_ESCALATION, KIND_CONFIRMATION):
            return raw
    return KIND_ESCALATION


def sse_event_for_interrupt(interrupt_obj: Any) -> str:
    if interrupt_kind(interrupt_obj) == KIND_CONFIRMATION:
        return "awaiting_confirmation"
    return "escalated"
