from __future__ import annotations

import unittest

from run_scan import is_pass
from src.wqb.check_readiness import REQUIRED_SUBMISSION_CHECK_NAMES, evaluate_check_snapshot


def complete_checks(*, self_corr_result: str = "PASS", self_corr_value: float = 0.4) -> list[dict[str, object]]:
    checks = [
        {"name": name, "result": "PASS"}
        for name in sorted(REQUIRED_SUBMISSION_CHECK_NAMES - {"SELF_CORRELATION"})
    ]
    checks.append(
        {
            "name": "SELF_CORRELATION",
            "result": self_corr_result,
            "value": self_corr_value,
            "limit": 0.7,
        }
    )
    return checks


class CheckReadinessTests(unittest.TestCase):
    def test_empty_snapshot_waits_instead_of_passing(self) -> None:
        decision = evaluate_check_snapshot([])

        self.assertEqual(decision.status, "waiting")
        self.assertIn("SELF_CORRELATION", decision.missing_checks)

    def test_pending_required_check_waits(self) -> None:
        checks = complete_checks()
        checks[-1]["result"] = "PENDING"

        decision = evaluate_check_snapshot(checks)

        self.assertEqual(decision.status, "waiting")
        self.assertEqual(decision.pending_checks, ("SELF_CORRELATION",))

    def test_self_correlation_value_above_limit_fails_even_if_result_says_pass(self) -> None:
        decision = evaluate_check_snapshot(complete_checks(self_corr_value=0.71))

        self.assertEqual(decision.status, "failed")
        self.assertEqual(decision.failed_checks, ("SELF_CORRELATION",))

    def test_complete_clean_snapshot_is_ready_and_has_stable_fingerprint(self) -> None:
        first = evaluate_check_snapshot(complete_checks())
        second = evaluate_check_snapshot(list(reversed(complete_checks())))

        self.assertEqual(first.status, "ready")
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_scan_pass_requires_complete_ready_checks(self) -> None:
        metrics = {"sharpe": 1.5, "fitness": 1.2, "turnover": 0.2}

        self.assertFalse(is_pass(metrics, []))
        self.assertTrue(is_pass(metrics, complete_checks()))


if __name__ == "__main__":
    unittest.main()
