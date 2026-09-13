from graph.nodes import risk as risk_module
from graph.nodes.risk import risk_anomaly
from graph.schemas import ClaimFacts, LineItem, Peril, RiskSeverity


def make_state(envelope_overrides=None, **facts_overrides):
    defaults = dict(
        policy_id="POL-HOME-01", policy_start_date="2024-01-01", date_of_loss="2024-06-01",
        perils=[Peril.WATER_DAMAGE],
        line_items=[LineItem(description="Cabinet", category="cabinetry", claimed_amount=5000)],
        narrative_summary="test",
    )
    defaults.update(facts_overrides)
    envelope = {"claim_id": "CLM-TEST", "filed_date": "2024-06-05"}
    envelope.update(envelope_overrides or {})
    return {"claim_facts": ClaimFacts(**defaults), "normalized_envelope": envelope, "injection_flags": []}


def test_clean_claim_has_no_flags(monkeypatch):
    monkeypatch.setattr(risk_module, "get_claim_history", lambda *a, **k: [])
    result = risk_anomaly(make_state())
    assert result["risk_result"].severity == RiskSeverity.NONE
    assert result["risk_result"].flags == []


def test_filed_before_loss_date_is_high_severity(monkeypatch):
    monkeypatch.setattr(risk_module, "get_claim_history", lambda *a, **k: [])
    state = make_state(envelope_overrides={"filed_date": "2024-05-01"})  # before date_of_loss
    result = risk_anomaly(state)
    assert "filed_before_loss_date" in result["risk_result"].flags
    assert result["risk_result"].severity == RiskSeverity.HIGH


def test_injection_flag_carryover_is_medium_severity(monkeypatch):
    """Injection is audited but must not HIGH-override a correct deny/approve."""
    monkeypatch.setattr(risk_module, "get_claim_history", lambda *a, **k: [])
    state = make_state()
    state["injection_flags"] = ["system_role_marker"]
    result = risk_anomaly(state)
    assert "injection_attempt_detected" in result["risk_result"].flags
    assert result["risk_result"].severity == RiskSeverity.MEDIUM


def test_duplicate_claim_detection(monkeypatch):
    fake_history = [
        {
            "claim_id": "CLM-OLD-1",
            "policy_id": "POL-HOME-01",
            "narrative_text": "Something happened on 2024-06-01 and I claimed it before.",
        }
    ]
    monkeypatch.setattr(risk_module, "get_claim_history", lambda *a, **k: fake_history)
    result = risk_anomaly(make_state())
    assert "possible_duplicate_claim" in result["risk_result"].flags
    assert "CLM-OLD-1" in result["risk_result"].duplicate_claim_ids
    assert result["risk_result"].severity == RiskSeverity.MEDIUM


def test_high_value_missing_documentation(monkeypatch):
    monkeypatch.setattr(risk_module, "get_claim_history", lambda *a, **k: [])
    state = make_state(
        line_items=[LineItem(description="Big claim", category="other", claimed_amount=50_000)],
    )
    result = risk_anomaly(state)
    assert "high_value_claim_missing_documentation" in result["risk_result"].flags


def test_get_claim_history_reads_real_fixture_and_excludes_self():
    history = risk_module.get_claim_history("POL-HOME-01", exclude_claim_id="CLM-001")
    assert all(c["claim_id"] != "CLM-001" for c in history)
    assert all(c["policy_id"] == "POL-HOME-01" for c in history)
    assert len(history) > 0
