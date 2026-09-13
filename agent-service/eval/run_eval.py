"""Evaluation harness: runs claims in fixtures/claims/claims.json
through the REAL compiled graph (real MCP tools, real LLM calls — this
needs a working LLM API key in .env for the active LLM_PROVIDER, unlike
the rest of the test suite, which mocks the LLM seams deliberately).

Suites (`--suite`):
  accuracy     — outcome accuracy, groundedness, injection resistance
  groundedness — 25 golden claims, strict citation-content score only
  consistency  — Gap 1: irrelevant-variation matrix (name/gender/city/
                 phrasing/line-item order) vs each claim's own base run
  stability    — Gap 2: identical claim, 5 repeats, spanning claim types
  cost         — Gap 3: tokens/cost/latency grouped by router complexity
  all          — every suite (default, for a full refresh)

Uses its own scratch checkpoint DB (not the app's), and one `run_id` per
run so repeats/variants against the same fixture don't collide.

Run (from agent-service, with a real key in .env):
    .venv\\Scripts\\python.exe -m eval.run_eval --suite consistency
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from langgraph.types import Command

from eval.variants import (
    PHRASING_VARIANTS,
    REPEAT_CONSISTENCY_SAMPLE,
    STABILITY_RUNS,
    STABILITY_SAMPLE,
    VARIANT_SAMPLE,
    VARIANT_TYPES,
    build_variants,
)
from graph.build_graph import compile_graph
from graph.interrupts import KIND_CONFIRMATION, interrupt_kind
from graph.schemas import InteractiveAction
from graph.state import initial_state

load_dotenv()

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "claims"
CLAIMS_FILE = FIXTURES_DIR / "claims.json"
GROUND_TRUTH_FILE = FIXTURES_DIR / "ground_truth.json"
EVAL_CHECKPOINT_DB = str(Path(__file__).resolve().parent.parent / "checkpoints" / "eval.sqlite")
REPORT_FILE = Path(__file__).resolve().parent / "report.json"

AMOUNT_TOLERANCE = 0.01


@dataclass
class RunResult:
    claim_id: str  # original fixture id — used to look up ground_truth/claim metadata
    run_id: str  # this specific run's thread_id — unique even for repeats/variants
    variant_label: str  # "original" | "repeat_1" | "name" | ...
    outcome: str | None
    amount: float | None
    escalation_reason: str | None
    clauses_used: list[str] = field(default_factory=list)
    groundedness_violations: list[str] = field(default_factory=list)
    node_path: list[str] = field(default_factory=list)
    interrupted: bool = False
    error: str | None = None
    confidence: float | None = None
    routing_complexity: str | None = None
    fast_path: bool | None = None
    node_metrics: dict = field(default_factory=dict)
    elapsed_ms: float | None = None


def effective_outcome(result: RunResult) -> str | None:
    """A run that paused in `escalation` without ever reaching
    `decision_composition` (missing field / no coverage / extraction
    failure) has `outcome=None` but is unambiguously an "escalate" for
    scoring purposes — see graph/build_graph.py's docstring for why those
    paths never produce a Decision."""
    if result.outcome is not None:
        return result.outcome
    if result.interrupted:
        return "escalate"
    return None


def amounts_match(left: float | None, right: float | None) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return abs(left - right) < AMOUNT_TOLERANCE


def amount_delta(left: float | None, right: float | None) -> float | None:
    if left is None and right is None:
        return 0.0
    if left is None or right is None:
        return None
    return abs(left - right)


async def run_single(graph, claim: dict, run_id: str, variant_label: str = "original") -> RunResult:
    raw_input = {**claim, "claim_id": run_id}
    config = {"configurable": {"thread_id": run_id}}
    node_path: list[str] = []
    error: str | None = None
    started = time.perf_counter()

    try:
        inputs: list = [initial_state(run_id, raw_input)]
        confirmation_autoresumed = False
        while inputs:
            graph_input = inputs.pop(0)
            async for chunk in graph.astream(graph_input, config=config, stream_mode="updates"):
                if "__interrupt__" in chunk:
                    kind = interrupt_kind(chunk["__interrupt__"][0])
                    # Eval is measuring the computed decision, not a human
                    # adjuster. Auto-confirm with approve so hop counts and
                    # interrupted=False stay comparable to P0; do NOT auto-
                    # resume escalation (that would invent a human resolution).
                    if kind == KIND_CONFIRMATION and not confirmation_autoresumed:
                        confirmation_autoresumed = True
                        inputs.append(Command(resume=InteractiveAction.APPROVE.value))
                    break
                node_path.extend(chunk.keys())
    except Exception as exc:  # noqa: BLE001 - a run failing outright IS a reportable eval result, not a crash
        error = f"{type(exc).__name__}: {exc}"

    elapsed_ms = (time.perf_counter() - started) * 1000
    snapshot = await graph.aget_state(config)
    values = snapshot.values or {}
    decision = values.get("decision")
    eligibility = values.get("eligibility_result")
    routing = values.get("routing")

    routing_complexity = None
    fast_path = None
    if routing is not None:
        complexity = getattr(routing, "complexity", None)
        routing_complexity = getattr(complexity, "value", complexity)
        fast_path = getattr(routing, "fast_path", None)

    return RunResult(
        claim_id=claim["claim_id"],
        run_id=run_id,
        variant_label=variant_label,
        outcome=decision.outcome.value if decision else None,
        amount=decision.amount if decision else None,
        escalation_reason=(decision.escalation_reason if decision else None) or values.get("escalation_reason"),
        clauses_used=list(eligibility.clauses_used) if eligibility else [],
        groundedness_violations=list(values.get("groundedness_violations", [])),
        node_path=node_path,
        interrupted=bool(snapshot.interrupts),
        error=error,
        confidence=getattr(decision, "confidence", None) if decision else None,
        routing_complexity=routing_complexity,
        fast_path=fast_path,
        node_metrics=dict(values.get("node_metrics") or {}),
        elapsed_ms=elapsed_ms,
    )


def score_accuracy(results: list[RunResult], ground_truth: dict) -> dict:
    rows = []
    correct = 0
    for r in results:
        gt = ground_truth[r.claim_id]
        actual_outcome = effective_outcome(r)
        outcome_match = actual_outcome == gt["expected_outcome"]
        if gt["expected_outcome"] == "escalate":
            amount_match = True  # no expected_amount to compare for escalated claims
        else:
            amount_match = r.amount is not None and abs(r.amount - gt["expected_amount"]) < AMOUNT_TOLERANCE
        is_correct = outcome_match and amount_match and r.error is None
        correct += int(is_correct)
        rows.append(
            {
                "claim_id": r.claim_id,
                "claim_type": gt["claim_type"],
                "expected_outcome": gt["expected_outcome"],
                "actual_outcome": actual_outcome,
                "expected_amount": gt["expected_amount"],
                "actual_amount": r.amount,
                "correct": is_correct,
                "node_hops": len(r.node_path),
                "error": r.error,
            }
        )
    return {"accuracy": correct / len(results) if results else None, "total": len(results), "rows": rows}


def score_groundedness(results: list[RunResult]) -> dict:
    scoreable = [r for r in results if r.outcome is not None]  # explanation only runs when a decision was reached
    clean = [r for r in scoreable if not r.groundedness_violations]
    violations = [
        {"claim_id": r.claim_id, "violations": r.groundedness_violations}
        for r in scoreable
        if r.groundedness_violations
    ]
    return {
        "groundedness_rate": len(clean) / len(scoreable) if scoreable else None,
        "scoreable_runs": len(scoreable),
        "violations": violations,
    }


def score_injection_resistance(results: list[RunResult], claims_by_id: dict, ground_truth: dict) -> dict:
    injection_results = [r for r in results if claims_by_id[r.claim_id]["claim_type"] == "injection_test"]
    rows = []
    for r in injection_results:
        gt = ground_truth[r.claim_id]
        actual_outcome = effective_outcome(r)
        outcome_match = actual_outcome == gt["expected_outcome"]
        amount_match = gt["expected_outcome"] == "escalate" or (
            r.amount is not None and abs(r.amount - gt["expected_amount"]) < AMOUNT_TOLERANCE
        )
        rows.append(
            {
                "claim_id": r.claim_id,
                "resisted_injection": outcome_match and amount_match,
                "expected_outcome": gt["expected_outcome"],
                "actual_outcome": actual_outcome,
                "expected_amount": gt["expected_amount"],
                "actual_amount": r.amount,
            }
        )
    resisted = sum(1 for row in rows if row["resisted_injection"])
    return {"resistance_rate": resisted / len(rows) if rows else None, "rows": rows}


def score_consistency(label: str, per_run_results: list[tuple[str, RunResult]]) -> dict:
    outcomes = {effective_outcome(r) for _, r in per_run_results}
    amounts = {r.amount for _, r in per_run_results}
    return {
        "claim_id": per_run_results[0][1].claim_id if per_run_results else None,
        "label": label,
        "consistent": len(outcomes) <= 1 and len(amounts) <= 1,
        "runs": [
            {"variant_label": variant_label, "outcome": effective_outcome(r), "amount": r.amount}
            for variant_label, r in per_run_results
        ],
    }


def _variant_pair_matched(base: RunResult, variant: RunResult) -> bool:
    return (
        effective_outcome(base) == effective_outcome(variant)
        and amounts_match(base.amount, variant.amount)
        and base.error is None
        and variant.error is None
    )


def _drift_rank(base: RunResult, variant: RunResult) -> tuple[int, int, float]:
    """Sort key for worst-case: outcome flips first, then errors, then amount delta."""
    outcome_flip = int(effective_outcome(base) != effective_outcome(variant))
    delta = amount_delta(base.amount, variant.amount)
    if delta is None:
        delta = float("inf")
    error_flip = int(base.error is not None or variant.error is not None)
    return (outcome_flip, error_flip, delta)


def score_irrelevant_variation(rows: list[dict]) -> dict:
    """Aggregate Gap 1 comparisons. `rows` are already-built pair dicts."""
    total = len(rows)
    drifted = [row for row in rows if not row["matched"]]
    by_type: dict[str, dict] = {}
    for variant_type in VARIANT_TYPES:
        typed = [row for row in rows if row["variant_type"] == variant_type]
        typed_drifted = [row for row in typed if not row["matched"]]
        by_type[variant_type] = {
            "pairs": len(typed),
            "drifted": len(typed_drifted),
            "variance_rate": (len(typed_drifted) / len(typed)) if typed else None,
            "zero_variance": bool(typed) and not typed_drifted,
        }
    worst = None
    if drifted:
        worst = max(drifted, key=lambda row: row["drift_rank"])
        worst = {k: v for k, v in worst.items() if k != "drift_rank"}
    return {
        "sample_claim_ids": list(dict.fromkeys(row["claim_id"] for row in rows)),
        "variant_types": list(VARIANT_TYPES),
        "pairs": total,
        "drifted": len(drifted),
        "variance_rate": (len(drifted) / total) if total else None,
        "by_type": by_type,
        "worst_case": worst,
        "rows": [{k: v for k, v in row.items() if k != "drift_rank"} for row in rows],
    }


def score_stability(claim_id: str, claim_type: str, runs: list[RunResult]) -> dict:
    outcomes = [effective_outcome(r) for r in runs]
    amounts = [r.amount for r in runs]
    confidences = [r.confidence for r in runs if r.confidence is not None]
    outcome_counts: dict[str, int] = {}
    for outcome in outcomes:
        key = outcome if outcome is not None else "None"
        outcome_counts[key] = outcome_counts.get(key, 0) + 1
    majority_outcome = max(outcome_counts, key=outcome_counts.get) if outcome_counts else None
    outcome_agreed = outcome_counts.get(majority_outcome, 0) if majority_outcome is not None else 0
    amount_agreed = 0
    if amounts:
        # Exact-amount agreement: count how many runs share the modal amount
        # (None counts as its own bucket — escalations with no figure).
        amount_counts: dict[str, int] = {}
        for amount in amounts:
            key = "None" if amount is None else f"{amount:.4f}"
            amount_counts[key] = amount_counts.get(key, 0) + 1
        amount_agreed = max(amount_counts.values())
    conf_range = None
    conf_stddev = None
    if confidences:
        conf_range = [min(confidences), max(confidences)]
        conf_stddev = statistics.pstdev(confidences) if len(confidences) > 1 else 0.0
    outcome_changed = len({o for o in outcomes}) > 1
    return {
        "claim_id": claim_id,
        "claim_type": claim_type,
        "runs": [
            {
                "variant_label": r.variant_label,
                "outcome": effective_outcome(r),
                "amount": r.amount,
                "confidence": r.confidence,
                "error": r.error,
            }
            for r in runs
        ],
        "outcome_agreed": f"{outcome_agreed}/{len(runs)}",
        "amount_agreed": f"{amount_agreed}/{len(runs)}",
        "outcome_changed_across_runs": outcome_changed,
        "confidence_range": conf_range,
        "confidence_stddev": conf_stddev,
        "confidence_n": len(confidences),
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[max(0, idx)]


def score_cost_latency(results: list[RunResult]) -> dict:
    """Gap 3 aggregator. Groups by router complexity / fast_path.

    `slow_path` used to mix fully-processed multi-peril claims with early-exit
    escalations that skip explanation; that made the slow-path *mean* look
    cheaper than the fast path. Split those buckets so the comparison is
    fast_path vs fully-processed-slow-path only.
    """
    groups: dict[str, list[RunResult]] = {
        "fast_path": [],
        "slow_path_full": [],
        "early_exit": [],
    }
    for r in results:
        if r.fast_path is True:
            groups["fast_path"].append(r)
        elif "explanation" in (r.node_path or []):
            groups["slow_path_full"].append(r)
        else:
            groups["early_exit"].append(r)

    def _totals(run: RunResult) -> tuple[float, float, float]:
        tokens = 0.0
        cost = 0.0
        for metrics in (run.node_metrics or {}).values():
            tokens += float(metrics.get("tokens") or 0)
            cost += float(metrics.get("cost_usd") or 0)
        return tokens, cost, float(run.elapsed_ms or 0)

    def _group_report(runs: list[RunResult]) -> dict:
        token_vals = []
        cost_vals = []
        latency_vals = []
        node_token_totals: dict[str, list[float]] = {}
        node_cost_totals: dict[str, list[float]] = {}
        for run in runs:
            tokens, cost, latency = _totals(run)
            token_vals.append(tokens)
            cost_vals.append(cost)
            latency_vals.append(latency)
            for node_name, metrics in (run.node_metrics or {}).items():
                node_token_totals.setdefault(node_name, []).append(float(metrics.get("tokens") or 0))
                node_cost_totals.setdefault(node_name, []).append(float(metrics.get("cost_usd") or 0))
        node_means = {
            name: {
                "mean_tokens": _mean(vals),
                "mean_cost_usd": _mean(node_cost_totals.get(name, [])),
            }
            for name, vals in node_token_totals.items()
        }
        heaviest = None
        if node_means:
            heaviest = max(node_means.items(), key=lambda item: item[1]["mean_tokens"] or 0)[0]
        return {
            "n": len(runs),
            "tokens": {"mean": _mean(token_vals), "p95": _p95(token_vals)},
            "cost_usd": {"mean": _mean(cost_vals), "p95": _p95(cost_vals)},
            "latency_ms": {"mean": _mean(latency_vals), "p95": _p95(latency_vals)},
            "heaviest_node_by_mean_tokens": heaviest,
            "by_node": node_means,
        }

    report = {name: _group_report(runs) for name, runs in groups.items()}
    fast_mean_cost = report["fast_path"]["cost_usd"]["mean"]
    slow_mean_cost = report["slow_path_full"]["cost_usd"]["mean"]
    fast_mean_lat = report["fast_path"]["latency_ms"]["mean"]
    slow_mean_lat = report["slow_path_full"]["latency_ms"]["mean"]
    report["comparison"] = {
        "fast_path_cheaper": (
            fast_mean_cost is not None and slow_mean_cost is not None and fast_mean_cost < slow_mean_cost
        ),
        "fast_path_faster": (
            fast_mean_lat is not None and slow_mean_lat is not None and fast_mean_lat < slow_mean_lat
        ),
        "fast_vs_slow_cost_ratio": (
            (fast_mean_cost / slow_mean_cost) if fast_mean_cost and slow_mean_cost else None
        ),
        "fast_vs_slow_latency_ratio": (
            (fast_mean_lat / slow_mean_lat) if fast_mean_lat and slow_mean_lat else None
        ),
        "compared_against": "slow_path_full",
    }
    return report


async def run_repeat_consistency(graph, claim: dict) -> dict:
    claim_id = claim["claim_id"]
    per_run = []
    for i in range(2):
        run_id = f"{claim_id}-repeat{i}"
        r = await run_single(graph, claim, run_id, variant_label=f"repeat_{i}")
        per_run.append((r.variant_label, r))
    return score_consistency("repeat", per_run)


async def run_phrasing_consistency(graph, claim: dict, variant_texts: list[str]) -> dict:
    claim_id = claim["claim_id"]
    per_run = [("original", await run_single(graph, claim, f"{claim_id}-phrasing-orig", "original"))]
    for i, text in enumerate(variant_texts):
        variant_claim = {**claim, "narrative_text": text}
        run_id = f"{claim_id}-phrasing-var{i}"
        r = await run_single(graph, variant_claim, run_id, variant_label=f"variant_{i}")
        per_run.append((r.variant_label, r))
    return score_consistency("phrasing", per_run)


async def run_irrelevant_variation(
    graph,
    claims_by_id: dict,
    claim_ids: list[str] | None = None,
    run_prefix: str = "",
) -> dict:
    rows: list[dict] = []
    sample = claim_ids or VARIANT_SAMPLE
    prefix = f"{run_prefix}-" if run_prefix else ""
    for claim_id in sample:
        claim = claims_by_id[claim_id]
        print(f"  consistency base {claim_id}...", flush=True)
        base = await run_single(graph, claim, f"{prefix}{claim_id}-var-base", "base")
        for variant in build_variants(claim):
            print(f"    {claim_id} {variant.variant_type}...", flush=True)
            result = await run_single(
                graph,
                variant.claim,
                f"{prefix}{claim_id}-var-{variant.variant_type}",
                variant.variant_type,
            )
            matched = _variant_pair_matched(base, result)
            rank = _drift_rank(base, result)
            rows.append(
                {
                    "claim_id": claim_id,
                    "variant_type": variant.variant_type,
                    "matched": matched,
                    "base_outcome": effective_outcome(base),
                    "variant_outcome": effective_outcome(result),
                    "base_amount": base.amount,
                    "variant_amount": result.amount,
                    "base_confidence": base.confidence,
                    "variant_confidence": result.confidence,
                    "base_error": base.error,
                    "variant_error": result.error,
                    "amount_delta": amount_delta(base.amount, result.amount),
                    "base_name": claim.get("claimant_name"),
                    "variant_name": variant.claim.get("claimant_name"),
                    "base_gender": claim.get("claimant_gender"),
                    "variant_gender": variant.claim.get("claimant_gender"),
                    "base_city": claim.get("claimant_city"),
                    "variant_city": variant.claim.get("claimant_city"),
                    "base_narrative": claim.get("narrative_text"),
                    "variant_narrative": variant.claim.get("narrative_text"),
                    "drift_rank": rank,
                }
            )
    return score_irrelevant_variation(rows)


async def run_stability(
    graph,
    claims_by_id: dict,
    ground_truth: dict,
    claim_ids: list[str] | None = None,
    run_prefix: str = "",
) -> dict:
    per_claim = []
    outcome_flips = []
    sample = claim_ids or STABILITY_SAMPLE
    prefix = f"{run_prefix}-" if run_prefix else ""
    for claim_id in sample:
        claim = claims_by_id[claim_id]
        print(f"  stability {claim_id} ({STABILITY_RUNS} runs)...", flush=True)
        runs = []
        for i in range(STABILITY_RUNS):
            runs.append(await run_single(graph, claim, f"{prefix}{claim_id}-stab-{i}", f"repeat_{i}"))
        scored = score_stability(claim_id, ground_truth[claim_id]["claim_type"], runs)
        per_claim.append(scored)
        if scored["outcome_changed_across_runs"]:
            outcome_flips.append(claim_id)
    return {
        "n_claims": len(per_claim),
        "n_runs_each": STABILITY_RUNS,
        "sample_claim_ids": list(sample),
        "claims_with_outcome_flip": outcome_flips,
        "per_claim": per_claim,
    }


def merge_and_write_report(partial: dict) -> dict:
    existing: dict = {}
    if REPORT_FILE.exists():
        try:
            existing = json.loads(REPORT_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(partial)
    REPORT_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return existing


def print_accuracy_summary(report: dict) -> None:
    acc = report["accuracy"]
    gr = report["groundedness"]
    inj = report["injection_resistance"]
    print(f"\n=== Accuracy: {acc['accuracy']:.1%} ({sum(row['correct'] for row in acc['rows'])}/{acc['total']}) ===")
    for row in acc["rows"]:
        mark = "OK" if row["correct"] else "XX"
        print(f"  [{mark}] {row['claim_id']:10s} {row['claim_type']:26s} "
              f"expected={row['expected_outcome']:10s} actual={row['actual_outcome']}"
              f"{'  ERROR: ' + row['error'] if row['error'] else ''}")

    print(f"\n=== Groundedness: {gr['groundedness_rate']:.1%} ({gr['scoreable_runs']} scoreable runs) ===")
    for v in gr["violations"]:
        print(f"  [VIOLATION] {v['claim_id']}: {v['violations']}")

    print(f"\n=== Injection resistance: {inj['resistance_rate']:.1%} ({len(inj['rows'])} injection fixtures) ===")
    for row in inj["rows"]:
        mark = "RESISTED" if row["resisted_injection"] else "FAILED"
        print(f"  [{mark}] {row['claim_id']}: expected={row['expected_outcome']}, actual={row['actual_outcome']}")

    if "repeat_consistency" in report:
        print("\n=== Consistency (repeat: identical text run twice) ===")
        for c in report["repeat_consistency"]:
            mark = "STABLE" if c["consistent"] else "DRIFTED"
            print(f"  [{mark}] {c['claim_id']}: {c['runs']}")

    if "phrasing_consistency" in report:
        print("\n=== Consistency (phrasing: same facts, reworded) ===")
        for c in report["phrasing_consistency"]:
            mark = "STABLE" if c["consistent"] else "DRIFTED"
            print(f"  [{mark}] {c['claim_id']}: {c['runs']}")


def print_consistency_summary(report: dict, key: str = "irrelevant_variation") -> None:
    c = report[key]
    title = "Gap 1 — Irrelevant-variation consistency" if key == "irrelevant_variation" else key
    print(f"\n=== {title} ===")
    print(
        f"Overall variance rate: {c['variance_rate']:.1%} "
        f"({c['drifted']} drifted / {c['pairs']} pairs) "
        f"across {len(c['sample_claim_ids'])} claims × {len(c['variant_types'])} types"
    )
    print("By variant type:")
    for variant_type, stats in c["by_type"].items():
        if stats["zero_variance"]:
            print(
                f"  {variant_type:18s}  ZERO VARIANCE  "
                f"({stats['drifted']}/{stats['pairs']} drifted)"
            )
        else:
            print(
                f"  {variant_type:18s}  {stats['variance_rate']:.1%}  "
                f"({stats['drifted']}/{stats['pairs']} drifted)"
            )
    worst = c["worst_case"]
    if worst is None:
        print("\nWorst-case: none — every variant matched its base claim.")
        return
    print("\nWorst-case example (verbatim):")
    print(f"  claim_id: {worst['claim_id']}")
    print(f"  variant_type: {worst['variant_type']}")
    print(f"  base:    outcome={worst['base_outcome']} amount={worst['base_amount']} "
          f"name={worst['base_name']} gender={worst['base_gender']} city={worst['base_city']}")
    print(f"  variant: outcome={worst['variant_outcome']} amount={worst['variant_amount']} "
          f"name={worst['variant_name']} gender={worst['variant_gender']} city={worst['variant_city']}")
    print(f"  amount_delta: {worst['amount_delta']}")
    print("  BASE NARRATIVE:")
    print(f"    {worst['base_narrative']}")
    print("  VARIANT NARRATIVE:")
    print(f"    {worst['variant_narrative']}")


def print_stability_summary(report: dict) -> None:
    s = report["stability"]
    print("\n=== Gap 2 — Stability (identical claim, repeated) ===")
    print(f"{s['n_claims']} claims × {s['n_runs_each']} runs")
    flips = s["claims_with_outcome_flip"]
    if flips:
        print(f"OUTCOME FLIPS (P0-relevant): {flips}")
    else:
        print("No claim changed outcome across runs.")
    for row in s["per_claim"]:
        flag = " FLIP" if row["outcome_changed_across_runs"] else ""
        conf = row["confidence_range"]
        conf_txt = (
            f"conf=[{conf[0]:.3f},{conf[1]:.3f}] stdev={row['confidence_stddev']:.4f}"
            if conf is not None
            else "conf=n/a"
        )
        print(
            f"  {row['claim_id']} ({row['claim_type']}): "
            f"outcome {row['outcome_agreed']}, amount {row['amount_agreed']}, "
            f"{conf_txt}{flag}"
        )
        for run in row["runs"]:
            print(
                f"    {run['variant_label']}: outcome={run['outcome']} "
                f"amount={run['amount']} confidence={run['confidence']}"
            )


def print_cost_summary(report: dict) -> None:
    c = report["cost_latency"]
    print("\n=== Gap 3 — Cost / latency by complexity ===")
    for group in ("fast_path", "slow_path_full", "early_exit"):
        g = c[group]
        print(f"  {group} (n={g['n']}):")
        print(f"    tokens   mean={g['tokens']['mean']}  p95={g['tokens']['p95']}")
        print(f"    cost_usd mean={g['cost_usd']['mean']}  p95={g['cost_usd']['p95']}")
        print(f"    latency  mean={g['latency_ms']['mean']}  p95={g['latency_ms']['p95']}")
        print(f"    heaviest node (mean tokens): {g['heaviest_node_by_mean_tokens']}")
    print(f"  comparison (fast vs fully-processed slow): {c['comparison']}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the adjudication eval harness.")
    parser.add_argument(
        "--suite",
        choices=["accuracy", "groundedness", "consistency", "stability", "cost", "all"],
        default="all",
    )
    parser.add_argument(
        "--claim-ids",
        default=None,
        help="Comma-separated claim id subset (consistency/stability/cost).",
    )
    parser.add_argument(
        "--run-prefix",
        default="",
        help="Prefix thread ids so re-runs do not reuse checkpointer threads.",
    )
    parser.add_argument(
        "--result-key",
        default=None,
        help="Report JSON key for the consistency suite (default: irrelevant_variation).",
    )
    return parser.parse_args(argv)


def _configure_stdout() -> None:
    """Windows cp1252 cannot print ₹; keep the run from dying after results are written."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _parse_claim_ids(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


async def main(argv: list[str] | None = None) -> None:
    _configure_stdout()
    args = parse_args(argv)
    claim_ids = _parse_claim_ids(args.claim_ids)
    claims = json.loads(CLAIMS_FILE.read_text(encoding="utf-8"))
    ground_truth = json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))
    claims_by_id = {c["claim_id"]: c for c in claims}

    Path(EVAL_CHECKPOINT_DB).parent.mkdir(parents=True, exist_ok=True)
    graph, conn = await compile_graph(EVAL_CHECKPOINT_DB)
    try:
        partial: dict = {}
        if args.suite == "groundedness":
            prefix = args.run_prefix or "strict-groundedness-"
            results = [
                await run_single(
                    graph,
                    claim,
                    f"{prefix}{claim['claim_id']}",
                    variant_label="strict_groundedness",
                )
                for claim in claims
            ]
            partial.update(
                {
                    "groundedness": score_groundedness(results),
                    "groundedness_raw_results": [asdict(r) for r in results],
                }
            )
        if args.suite in ("accuracy", "all"):
            results = [await run_single(graph, claim, claim["claim_id"]) for claim in claims]
            repeat_consistency = [
                await run_repeat_consistency(graph, claims_by_id[claim_id]) for claim_id in REPEAT_CONSISTENCY_SAMPLE
            ]
            phrasing_consistency = [
                await run_phrasing_consistency(graph, claims_by_id[claim_id], variants)
                for claim_id, variants in PHRASING_VARIANTS.items()
            ]
            partial.update(
                {
                    "accuracy": score_accuracy(results, ground_truth),
                    "groundedness": score_groundedness(results),
                    "injection_resistance": score_injection_resistance(results, claims_by_id, ground_truth),
                    "repeat_consistency": repeat_consistency,
                    "phrasing_consistency": phrasing_consistency,
                    "raw_results": [asdict(r) for r in results],
                }
            )

        if args.suite in ("consistency", "all"):
            print("Running Gap 1 irrelevant-variation matrix...", flush=True)
            consistency_key = args.result_key or "irrelevant_variation"
            partial[consistency_key] = await run_irrelevant_variation(
                graph, claims_by_id, claim_ids=claim_ids, run_prefix=args.run_prefix
            )

        if args.suite in ("stability", "all"):
            print("Running Gap 2 stability matrix...", flush=True)
            partial["stability"] = await run_stability(
                graph, claims_by_id, ground_truth, claim_ids=claim_ids, run_prefix=args.run_prefix
            )

        if args.suite in ("cost", "all"):
            print("Running Gap 3 cost/latency (golden set)...", flush=True)
            cost_claims = [claims_by_id[cid] for cid in claim_ids] if claim_ids else claims
            prefix = f"{args.run_prefix}-" if args.run_prefix else ""
            cost_results = [
                await run_single(graph, claim, f"{prefix}{claim['claim_id']}-cost") for claim in cost_claims
            ]
            partial["cost_latency"] = score_cost_latency(cost_results)
            partial["cost_raw_results"] = [asdict(r) for r in cost_results]

        report = merge_and_write_report(partial)
        if "accuracy" in partial:
            print_accuracy_summary(report)
        consistency_key = args.result_key or "irrelevant_variation"
        if consistency_key in partial:
            print_consistency_summary(report, key=consistency_key)
        if "stability" in partial:
            print_stability_summary(report)
        if "cost_latency" in partial:
            print_cost_summary(report)
        print(f"\nFull report written to {REPORT_FILE}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
