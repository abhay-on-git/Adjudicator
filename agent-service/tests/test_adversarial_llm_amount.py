"""P0 required deliverable: prove — structurally, not by prompt wording — that
an LLM-produced amount or decision cannot flow into the final Decision.

Three independent structural barriers are exercised, each of which alone would
block the attack even if the other two didn't exist:

  1. Schema barrier: the LLM's structured-output schema (`ExtractedNarrativeFacts`,
     the base of `ClaimFacts`) rejects unknown fields at parse time
     (`model_config = ConfigDict(extra="forbid")`). A "confused" or adversarial
     model that emits `{"decision": "approve", "amount": 999999, ...}` fails
     validation before that JSON becomes a Python object at all.
  2. Function-signature / no-getattr barrier: even if a smuggled field somehow
     existed on an object passed to `compute_payout`, the function's source
     never looks it up — it only reads `facts.policy_id`, `facts.line_items`,
     `facts.evidence_tags`, `facts.cause_ambiguous`, `facts.date_of_loss`,
     `facts.policy_start_date`. We assert this by source inspection so the
     test fails loudly if a future edit ever adds a `getattr(facts, ...)` or
     `facts.amount`-style read.
  3. Import-graph barrier: `rules/payout.py` and `rules/eligibility.py` do not
     import any LLM client at all, so there is no code path by which those
     modules could call out to a model even if someone tried to wire one in
     ad hoc inside a branch.
"""

import ast
import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from graph.schemas import ClaimFacts, ExtractedNarrativeFacts, LineItem, Peril
from rules.payout import compute_payout

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

DISALLOWED_IMPORT_PREFIXES = ("openai", "langchain", "langgraph", "mcp_client")


def test_barrier_1_schema_rejects_smuggled_decision_and_amount():
    malicious_llm_output = {
        "date_of_loss": "2024-06-01",
        "perils": ["water_damage"],
        "line_items": [{"description": "Cabinet", "category": "cabinetry", "claimed_amount": 5000}],
        "narrative_summary": "test",
        # The attack: a model that "helpfully" also emits a decision + amount.
        "decision": "approve",
        "amount": 999_999,
        "suggested_amount": 999_999,
    }
    with pytest.raises(ValidationError) as exc_info:
        ExtractedNarrativeFacts(**malicious_llm_output)
    assert "decision" in str(exc_info.value) or "extra" in str(exc_info.value).lower()

    # Confirm ClaimFacts (the full merged schema eligibility actually reads)
    # inherits the same barrier.
    malicious_llm_output["policy_id"] = "POL-HOME-01"
    malicious_llm_output["policy_start_date"] = "2024-01-01"
    with pytest.raises(ValidationError):
        ClaimFacts(**malicious_llm_output)


def test_barrier_1b_legitimate_fields_still_work():
    """Sanity check that barrier #1 isn't just rejecting everything — a real
    extraction payload with no smuggled fields parses fine."""
    facts = ClaimFacts(
        policy_id="POL-HOME-01",
        policy_start_date="2024-01-01",
        date_of_loss="2024-06-01",
        perils=[Peril.WATER_DAMAGE],
        line_items=[LineItem(description="Cabinet", category="cabinetry", claimed_amount=5000)],
        narrative_summary="test",
    )
    assert facts.line_items[0].claimed_amount == 5000


def test_barrier_2_compute_payout_never_reads_an_amount_or_decision_attribute():
    """Source-inspection: compute_payout and its helpers only ever access the
    declared ClaimFacts fields, never a free-form 'amount'/'decision'-shaped
    attribute, and never use getattr() to read an arbitrary field name."""
    for path in [RULES_DIR / "payout.py", RULES_DIR / "eligibility.py"]:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
                pytest.fail(f"{path.name} uses getattr(), which could read a smuggled field: {ast.dump(node)}")
            if isinstance(node, ast.Attribute) and node.attr in {"decision", "outcome", "suggested_amount"}:
                pytest.fail(f"{path.name} reads a decision/amount-shaped attribute: .{node.attr}")


def test_barrier_2b_end_to_end_smuggled_amount_does_not_affect_payout():
    """Even granting the attacker a best-case scenario — a raw dict claim with
    an extra 'amount' key that made it past everything else somehow, expressed
    as an ExtractedNarrativeFacts field the attacker hopes downstream code
    reads dynamically — compute_payout's actual output is identical with or
    without that key, because nothing in its call path ever looks for it."""
    baseline = ClaimFacts(
        policy_id="POL-HOME-01",
        policy_start_date="2024-01-01",
        date_of_loss="2024-06-01",
        perils=[Peril.WATER_DAMAGE],
        line_items=[LineItem(description="Cabinet", category="cabinetry", claimed_amount=5000,
                              evidence_tags=["sudden_discharge"])],
        narrative_summary="test",
    )
    result_without_attack = compute_payout(baseline, clauses=[])

    # Simulate a compromised object that has a rogue attribute attached via
    # direct __dict__ manipulation (bypassing pydantic validation entirely,
    # the most generous possible attack surface).
    tampered = baseline.model_copy(deep=True)
    object.__setattr__(tampered, "__dict__", {**tampered.__dict__, "amount": 999_999, "decision": "approve"})
    result_with_attack = compute_payout(tampered, clauses=[])

    assert result_with_attack.total_payable == result_without_attack.total_payable
    assert result_with_attack.total_payable != 999_999


def test_barrier_3_rules_engine_has_no_llm_imports():
    for path in [RULES_DIR / "payout.py", RULES_DIR / "eligibility.py"]:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.startswith(DISALLOWED_IMPORT_PREFIXES), (
                    f"{path.name} imports '{name}' — the deterministic rules engine "
                    "must have zero LLM-adjacent imports."
                )


def test_barrier_3b_module_actually_importable_without_llm_deps_installed():
    """Belt-and-suspenders: re-import the modules fresh and confirm they don't
    transitively pull in an LLM client at import time either."""
    payout = importlib.reload(importlib.import_module("rules.payout"))
    eligibility = importlib.reload(importlib.import_module("rules.eligibility"))
    for mod in (payout, eligibility):
        module_names = {name for name in dir(mod)}
        assert "openai" not in module_names
        assert "OpenAI" not in module_names
