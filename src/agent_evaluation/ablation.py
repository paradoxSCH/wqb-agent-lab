from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence


PRIMARY_METRICS = (
    "submit_ready_per_1000",
    "final_submitted_per_1000",
    "independent_submit_clusters_per_1000",
    "wasted_budget_rate",
    "duplicate_generation_rate",
    "complexity_cost_rate",
    "net_usefulness_score",
)


def evaluate_ablation(variants: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    summarized = {name: _summarize_runs(runs) for name, runs in variants.items()}
    baseline = summarized.get("baseline")
    deltas = {}
    if baseline is not None:
        for name, summary in summarized.items():
            if name == "baseline":
                continue
            deltas[name] = _delta(summary, baseline)
    verdict = _verdict(summarized, deltas)
    return {
        "verdict": verdict,
        "variants": summarized,
        "delta_vs_baseline": deltas,
        "metrics": list(PRIMARY_METRICS),
    }


def summarize_run_dir(run_dir: Path | str) -> dict[str, Any]:
    path = Path(run_dir)
    ledger = _read_json(path / "daily_budget_ledger.json", {})
    closed_counts = {}
    if isinstance(ledger, dict):
        closed_loop = ledger.get("closed_loop") or {}
        if isinstance(closed_loop, dict):
            closed_counts = closed_loop.get("counts") or {}
    decisions = _read_json(path / "decision_attribution.json", [])
    if not isinstance(decisions, list):
        decisions = []
    result_rows = _read_result_rows(path)
    simulations = _int(ledger.get("spent_simulations")) if isinstance(ledger, dict) else 0
    decision_outcomes = [item.get("outcome") for item in decisions if isinstance(item, dict) and isinstance(item.get("outcome"), dict)]
    if not simulations:
        simulations = sum(_int(outcome.get("simulations_spent")) for outcome in decision_outcomes)
    if not simulations:
        simulations = len(result_rows)
    low_value = _int(closed_counts.get("low_value"))
    if not low_value:
        low_value = sum(_int(outcome.get("low_value_count")) for outcome in decision_outcomes)
    if not low_value:
        low_value = sum(1 for row in result_rows if _is_low_value(row))
    submit_ready = _int(closed_counts.get("submit_ready"))
    if not submit_ready:
        submit_ready = sum(_int(outcome.get("submit_ready_count")) for outcome in decision_outcomes)
    if not submit_ready:
        submit_ready = len(_read_rows(path / "submit_ready.json"))
    direct_submit = _int(closed_counts.get("direct_submit"))
    if not direct_submit:
        direct_submit = len(_read_rows(path / "direct_submit.json"))
    duplicates = _int(closed_counts.get("already_submitted"))
    if not duplicates:
        duplicates = sum(1 for row in result_rows if _has_check(row, "MATCHES_SUBMITTED_ALPHA", "FAIL"))
    return {
        "run_tag": ledger.get("daily_run_tag", path.name) if isinstance(ledger, dict) else path.name,
        "simulations": simulations,
        "submit_ready": submit_ready,
        "final_submitted": direct_submit,
        "independent_submit_clusters": _int(closed_counts.get("submit_ready")),
        "low_value": low_value,
        "duplicates": duplicates,
        "complexity_cost": len(decisions),
        "decision_count": len(decisions),
    }


def write_evaluation_report(output_dir: Path | str, variants: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = evaluate_ablation(variants)
    (output / "ablation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Agent Ablation Evaluation",
        "",
        f"Verdict: `{report['verdict']}`",
        "",
        "## Variants",
    ]
    for name, summary in report["variants"].items():
        lines.append(
            f"- `{name}` submit_ready_per_1000={summary['submit_ready_per_1000']} "
            f"wasted_budget_rate={summary['wasted_budget_rate']} "
            f"complexity_cost_rate={summary['complexity_cost_rate']}"
        )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _summarize_runs(runs: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    simulations = sum(_int(run.get("simulations")) for run in runs)
    denominator = max(simulations, 1)
    submit_ready = sum(_int(run.get("submit_ready")) for run in runs)
    final_submitted = sum(_int(run.get("final_submitted")) for run in runs)
    clusters = sum(_int(run.get("independent_submit_clusters")) for run in runs)
    low_value = sum(_int(run.get("low_value")) for run in runs)
    duplicates = sum(_int(run.get("duplicates")) for run in runs)
    complexity = sum(_int(run.get("complexity_cost")) for run in runs)
    submit_ready_per_1000 = (submit_ready / denominator) * 1000.0
    final_submitted_per_1000 = (final_submitted / denominator) * 1000.0
    clusters_per_1000 = (clusters / denominator) * 1000.0
    wasted_budget_rate = low_value / denominator
    duplicate_rate = duplicates / denominator
    complexity_rate = complexity / denominator
    return {
        "simulations": float(simulations),
        "submit_ready_per_1000": round(submit_ready_per_1000, 3),
        "final_submitted_per_1000": round(final_submitted_per_1000, 3),
        "independent_submit_clusters_per_1000": round(clusters_per_1000, 3),
        "wasted_budget_rate": round(wasted_budget_rate, 6),
        "duplicate_generation_rate": round(duplicate_rate, 6),
        "complexity_cost_rate": round(complexity_rate, 6),
        "net_usefulness_score": round(submit_ready_per_1000 + clusters_per_1000 - (wasted_budget_rate * 10.0) - (complexity_rate * 10.0), 3),
    }


def _delta(summary: Mapping[str, float], baseline: Mapping[str, float]) -> dict[str, float]:
    return {
        metric: round(float(summary.get(metric, 0.0)) - float(baseline.get(metric, 0.0)), 6)
        for metric in PRIMARY_METRICS
    }


def _verdict(summarized: Mapping[str, Mapping[str, float]], deltas: Mapping[str, Mapping[str, float]]) -> str:
    full = summarized.get("full_agent")
    full_delta = deltas.get("full_agent")
    if not full or not full_delta:
        return "inconclusive"
    lift = float(full_delta.get("submit_ready_per_1000", 0.0))
    waste_delta = float(full_delta.get("wasted_budget_rate", 0.0))
    complexity_rate = float(full.get("complexity_cost_rate", 0.0))
    if lift >= 2.0 and waste_delta <= -0.05 and complexity_rate <= 0.1:
        return "useful"
    if lift <= 0.0 and complexity_rate > 0.1:
        return "bloated"
    return "inconclusive"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _read_rows(path: Path) -> list[Mapping[str, Any]]:
    payload = _read_json(path, [])
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("rows") or payload.get("results") or payload.get("items")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _read_result_rows(run_dir: Path) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    snapshot = run_dir / "scan_results_snapshot.json"
    if snapshot.exists():
        rows.extend(_read_rows(snapshot))
    if rows:
        return rows
    for path in sorted(run_dir.glob("*_results.json")):
        rows.extend(_read_rows(path))
    return rows


def _is_low_value(row: Mapping[str, Any]) -> bool:
    checks = row.get("checks")
    if not isinstance(checks, list):
        return False
    hard_failures = {"LOW_SHARPE", "LOW_FITNESS", "LOW_SUB_UNIVERSE_SHARPE"}
    for check in checks:
        if not isinstance(check, dict):
            continue
        if check.get("name") in hard_failures and check.get("result") == "FAIL":
            return True
    return False


def _has_check(row: Mapping[str, Any], name: str, result: str) -> bool:
    checks = row.get("checks")
    if not isinstance(checks, list):
        return False
    return any(isinstance(check, dict) and check.get("name") == name and check.get("result") == result for check in checks)


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
