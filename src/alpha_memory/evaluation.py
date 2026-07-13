from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


OUTCOME_METRICS = (
    "submit_ready_per_1000",
    "near_pass_per_1000",
    "high_self_corr_rate",
    "duplicate_rate",
)


def evaluate_memory_runs(variants: dict[str, Sequence[dict[str, object]]]) -> dict[str, dict[str, float]]:
    report = {name: _summarize_runs(runs) for name, runs in variants.items()}

    baseline = report.get("baseline")
    if baseline is not None:
        comparison_name = _select_comparison_variant(report)
        comparison = report[comparison_name] if comparison_name is not None else baseline
        report["delta"] = _delta_metrics(baseline, comparison)

    return report


def _summarize_runs(runs: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    simulations = sum(_coerce_number(run.get("simulations")) for run in runs)
    denominator = max(simulations, 1.0)
    submit_ready = sum(_coerce_number(run.get("submit_ready")) for run in runs)
    near_pass = sum(_coerce_number(run.get("near_pass")) for run in runs)
    high_self_corr = sum(_coerce_number(run.get("high_self_corr")) for run in runs)
    duplicates = sum(_coerce_number(run.get("duplicates")) for run in runs)

    return {
        "submit_ready_per_1000": round((submit_ready / denominator) * 1000.0, 3),
        "near_pass_per_1000": round((near_pass / denominator) * 1000.0, 3),
        "high_self_corr_rate": round(high_self_corr / denominator, 6),
        "duplicate_rate": round(duplicates / denominator, 6),
    }


def _coerce_number(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number < 0.0:
        return 0.0
    return number


def _select_comparison_variant(report: Mapping[str, dict[str, float]]) -> str | None:
    """Choose the non-baseline variant used for delta metrics.

    The dashboard/CLI contract is stable: prefer the explicit "hybrid"
    variant, otherwise fall back to the alphabetically first non-baseline name.
    """
    if "hybrid" in report:
        return "hybrid"
    candidates = sorted(name for name in report if name not in ("baseline", "delta"))
    return candidates[0] if candidates else None


def _delta_metrics(baseline: Mapping[str, float], comparison: Mapping[str, float]) -> dict[str, float]:
    return {
        metric: round(comparison.get(metric, 0.0) - baseline.get(metric, 0.0), _precision_for(metric))
        for metric in OUTCOME_METRICS
    }


def _precision_for(metric: str) -> int:
    if metric.endswith("_rate"):
        return 6
    return 3


__all__ = ["evaluate_memory_runs"]
