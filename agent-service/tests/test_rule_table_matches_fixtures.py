"""Guards against rules/eligibility.py's hardcoded clause IDs drifting away
from the actual fixture policy text (see rules/eligibility.py module
docstring: the rule table is authored against clause IDs, not parsed from
prose at runtime, so nothing else would catch this kind of drift)."""

import ast
import re
from pathlib import Path

from mcp_server.policy_store import load_policy_index

RULES_FILE = Path(__file__).resolve().parent.parent / "rules" / "eligibility.py"

POLICY_PREFIX_TO_ID = {
    "evaluate_line_item_home": "POL-HOME-01",
    "evaluate_line_item_health": "POL-HEALTH-01",
    "evaluate_line_item_motor": "POL-MOTOR-01",
    "evaluate_line_item_travel": "POL-TRAVEL-01",
}

CLAUSE_ID_RE = re.compile(r"§\d+(?:\.\d+)*")


def test_every_clause_id_in_rule_table_exists_in_fixtures():
    index = load_policy_index()
    source = RULES_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        policy_id = POLICY_PREFIX_TO_ID.get(node.name)
        if policy_id is None:
            continue
        func_source = ast.get_source_segment(source, node) or ""
        clause_ids = set(CLAUSE_ID_RE.findall(func_source))
        for clause_id in clause_ids:
            if index.get(policy_id, clause_id) is None:
                missing.append(f"{node.name} references {clause_id} not found in {policy_id} fixture")

    assert not missing, "Rule table / fixture drift:\n" + "\n".join(missing)


def test_deductible_clauses_exist():
    from rules.eligibility import POLICY_EVALUATORS

    index = load_policy_index()
    for policy_id, (_evaluator, _amount, clause_id) in POLICY_EVALUATORS.items():
        assert index.get(policy_id, clause_id) is not None, (
            f"Deductible clause {clause_id} for {policy_id} not found in fixture"
        )
