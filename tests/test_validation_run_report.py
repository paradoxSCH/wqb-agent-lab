from __future__ import annotations

from scripts.maintenance.evaluate_validation_run import summarize_rows
from src.wqb.check_readiness import REQUIRED_SUBMISSION_CHECK_NAMES


def passing_checks() -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "result": "PASS",
            "value": 0.1 if name == "SELF_CORRELATION" else None,
            "limit": 0.7 if name == "SELF_CORRELATION" else None,
        }
        for name in sorted(REQUIRED_SUBMISSION_CHECK_NAMES)
    ]


def test_summary_reconciles_target_and_separates_transport_errors() -> None:
    rows = [
        {
            "alpha_id": "A1",
            "note": "family_a: pass",
            "expression": "rank(a)",
            "settings": {},
            "metrics": {"sharpe": 1.5, "fitness": 1.2, "turnover": 0.1},
            "checks": passing_checks(),
        },
        {
            "expression": "rank(b)",
            "settings": {},
            "error": "simulation_create_failed",
        },
    ]

    summary = summarize_rows(rows, target=1)

    assert summary["target_reconciled"] is True
    assert summary["successful_simulations"] == 1
    assert summary["pass_count"] == 1
    assert summary["historical_error_requests"] == 1
    assert summary["families"]["family_a"]["pass_count"] == 1


def test_summary_counts_duplicate_alpha_id_once() -> None:
    rows = [
        {
            "alpha_id": "A1",
            "expression": expression,
            "settings": {},
            "metrics": {"sharpe": 1.5, "fitness": 1.2, "turnover": 0.1},
            "checks": [],
        }
        for expression in ("rank(a)", "rank(b)")
    ]

    summary = summarize_rows(rows, target=1)

    assert summary["successful_simulations"] == 1
    assert summary["unique_alpha_ids"] == 1
