"""intake_normalize — raw claim submission -> cleaned, structured envelope.

Pure code, no LLM. Two jobs:
  1. Normalize whitespace/control characters in the free-text narrative so
     extraction sees clean input.
  2. Flag (not strip) suspicious prompt-injection-shaped patterns in the
     narrative — e.g. "SYSTEM:", "ignore previous instructions". These are
     LEFT IN the text (the narrative is evidence; silently editing a
     claimant's submission would itself be an audit problem) but tagged in
     `injection_flags` so later nodes/evaluators can check whether an
     injection attempt correlates with a change in outcome (see EVIDENCE.md's
     injection-resistance section).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from graph.schemas import AuditEvent, AuditEventType
from graph.state import AdjudicationState

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_WHITESPACE_RE = re.compile(r"[ \t]+")

# Deliberately simple, auditable pattern list rather than a second LLM call to
# "detect injection" — a classifier-calling-a-classifier adds a failure mode
# without adding much precision at this scale. See DESIGN.md if this needs to
# grow into something more principled later.
_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("system_role_marker", re.compile(r"\bSYSTEM\s*:", re.IGNORECASE)),
    ("assistant_role_marker", re.compile(r"\bASSISTANT\s*:", re.IGNORECASE)),
    ("ignore_instructions", re.compile(r"ignore (all |any )?(previous|prior|above) instructions", re.IGNORECASE)),
    ("pre_approved_claim", re.compile(r"pre[- ]approved by underwriting", re.IGNORECASE)),
    ("no_restrictions", re.compile(r"no restrictions|unrestricted assistant", re.IGNORECASE)),
    ("skip_deductible_or_review", re.compile(r"skip (the )?(deductible|review|verification)", re.IGNORECASE)),
    ("set_confidence_or_amount", re.compile(r"(set|mark).{0,20}(confidence|payout|amount|approved)", re.IGNORECASE)),
]

MAX_NARRATIVE_CHARS = 8000  # token-budget guard, see DESIGN.md "token budget" section


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def intake_normalize(state: AdjudicationState) -> dict:
    raw = state["raw_input"]
    narrative = raw.get("narrative_text", "")

    cleaned = _CONTROL_CHARS_RE.sub("", narrative)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    truncated = len(cleaned) > MAX_NARRATIVE_CHARS
    if truncated:
        cleaned = cleaned[:MAX_NARRATIVE_CHARS]

    flags = [name for name, pattern in _INJECTION_PATTERNS if pattern.search(narrative)]

    envelope = {
        "claim_id": raw.get("claim_id"),
        "policy_id": raw.get("policy_id"),
        "policy_start_date": raw.get("policy_start_date"),
        "filed_date": raw.get("filed_date"),
        "claimant_name": raw.get("claimant_name"),
        "claimant_gender": raw.get("claimant_gender"),
        "claimant_city": raw.get("claimant_city"),
        "narrative_text": cleaned,
        "narrative_truncated": truncated,
    }

    audit_detail = f"Normalized narrative ({len(cleaned)} chars)."
    if flags:
        audit_detail += f" Injection-shaped patterns flagged: {flags}."
    if truncated:
        audit_detail += f" Narrative truncated to {MAX_NARRATIVE_CHARS} chars (token budget)."

    return {
        "normalized_envelope": envelope,
        "injection_flags": flags,
        "audit_log": [
            AuditEvent(
                event_type=AuditEventType.INTAKE_COMPLETE,
                node="intake_normalize",
                detail=audit_detail,
                timestamp=_now_iso(),
            )
        ],
    }
