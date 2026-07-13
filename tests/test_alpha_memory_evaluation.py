from __future__ import annotations

import unittest

from src.alpha_memory.evaluation import evaluate_memory_runs


class AlphaMemoryEvaluationTests(unittest.TestCase):
    def test_evaluate_memory_runs_reports_wqb_outcome_metrics(self) -> None:
        baseline = [
            {"simulations": 1000, "submit_ready": 1, "high_self_corr": 12, "duplicates": 9, "near_pass": 3},
        ]
        hybrid = [
            {"simulations": 1000, "submit_ready": 3, "high_self_corr": 5, "duplicates": 2, "near_pass": 7},
        ]

        report = evaluate_memory_runs({"baseline": baseline, "hybrid": hybrid})

        self.assertEqual(report["baseline"]["submit_ready_per_1000"], 1.0)
        self.assertEqual(report["hybrid"]["submit_ready_per_1000"], 3.0)
        self.assertEqual(report["hybrid"]["duplicate_rate"], 0.002)
        self.assertGreater(report["delta"]["submit_ready_per_1000"], 0)

    def test_evaluate_memory_runs_handles_missing_and_non_numeric_values(self) -> None:
        report = evaluate_memory_runs({
            "baseline": [{"simulations": 0, "submit_ready": "not-a-number"}],
            "candidate": [{"simulations": "bad", "near_pass": 2, "high_self_corr": None, "duplicates": True}],
        })

        self.assertEqual(report["baseline"]["submit_ready_per_1000"], 0.0)
        self.assertEqual(report["candidate"]["near_pass_per_1000"], 2000.0)
        self.assertEqual(report["candidate"]["high_self_corr_rate"], 0.0)
        self.assertEqual(report["candidate"]["duplicate_rate"], 0.0)
        self.assertEqual(report["delta"]["near_pass_per_1000"], 2000.0)

    def test_evaluate_memory_runs_reports_zero_delta_when_only_baseline_exists(self) -> None:
        report = evaluate_memory_runs({"baseline": [{"simulations": 100, "submit_ready": 2}]})

        self.assertEqual(report["delta"]["submit_ready_per_1000"], 0.0)
        self.assertEqual(report["delta"]["near_pass_per_1000"], 0.0)
        self.assertEqual(report["delta"]["high_self_corr_rate"], 0.0)
        self.assertEqual(report["delta"]["duplicate_rate"], 0.0)

    def test_evaluate_memory_runs_uses_sorted_non_hybrid_delta_variant(self) -> None:
        first_report = evaluate_memory_runs({
            "baseline": [{"simulations": 1000}],
            "zeta": [{"simulations": 1000, "submit_ready": 9}],
            "alpha": [{"simulations": 1000, "submit_ready": 2}],
        })
        second_report = evaluate_memory_runs({
            "baseline": [{"simulations": 1000}],
            "alpha": [{"simulations": 1000, "submit_ready": 2}],
            "zeta": [{"simulations": 1000, "submit_ready": 9}],
        })

        self.assertEqual(first_report["delta"]["submit_ready_per_1000"], 2.0)
        self.assertEqual(second_report["delta"]["submit_ready_per_1000"], 2.0)

    def test_evaluate_memory_runs_treats_negative_counts_as_zero(self) -> None:
        report = evaluate_memory_runs({
            "baseline": [{
                "simulations": -100,
                "submit_ready": -1,
                "near_pass": -2,
                "high_self_corr": -3,
                "duplicates": -4,
            }],
        })

        self.assertEqual(report["baseline"]["submit_ready_per_1000"], 0.0)
        self.assertEqual(report["baseline"]["near_pass_per_1000"], 0.0)
        self.assertEqual(report["baseline"]["high_self_corr_rate"], 0.0)
        self.assertEqual(report["baseline"]["duplicate_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
