from __future__ import annotations

import unittest

from wqb_agent_lab.memory.core.evaluation import evaluate_memory_runs, evaluate_retrieval_rankings, run_retrieval_benchmark
from wqb_agent_lab.memory.core.schema import MemoryNode
from wqb_agent_lab.memory.core.store import SQLiteMemoryStore
import tempfile
from pathlib import Path


class AlphaMemoryEvaluationTests(unittest.TestCase):
    def test_chinese_benchmark_handles_partial_phrases_and_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db")
            store.initialize()
            for node_id, title, summary in (
                ("behavior-overreaction", "过度反应", "市场冲击后可能出现价格反转"),
                ("behavior-anchoring", "锚定效应", "历史价格构成投资者参考点"),
                ("behavior-attention", "注意力偏差", "显著性影响投资者关注和交易"),
            ):
                store.upsert_node(MemoryNode(id=node_id, type="behavior_thesis", layer="long_term", title=title, summary=summary))
            cases = [
                {"query": "过度反应后的反转", "relevant_ids": ["behavior-overreaction"]},
                {"query": "历史锚定参考", "relevant_ids": ["behavior-anchoring"]},
                {"query": "注意力显著偏差", "relevant_ids": ["behavior-attention"]},
            ]
            report = run_retrieval_benchmark(store, cases, top_k=2)
            self.assertEqual(1.0, report["metrics"]["recall_at_k"])

    def test_retrieval_quality_reports_recall_mrr_and_ndcg(self) -> None:
        report = evaluate_retrieval_rankings(
            [
                {"relevant_ids": ["A", "B"], "retrieved_ids": ["X", "A", "B"]},
                {"relevant_ids": ["C"], "retrieved_ids": ["C", "Y"]},
            ]
        )

        self.assertEqual(1.0, report["recall_at_k"])
        self.assertEqual(0.75, report["mrr"])
        self.assertGreater(report["ndcg_at_k"], 0.8)

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
