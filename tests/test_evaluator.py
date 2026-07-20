"""evaluator 模块单元测试。"""

import unittest

from wqb_agent_lab.evaluation.scoring import (
    AlphaMetrics,
    IterationConfig,
    ScoreWeights,
    assign_composite_scores,
    compute_composite_score,
    ensure_diversity,
    extract_metrics,
    filter_alphas,
    rank_alphas,
    run_iteration,
)


class TestCompositeScore(unittest.TestCase):
    """综合评分计算测试。"""

    def test_perfect_alpha_has_high_score(self):
        m = AlphaMetrics(
            expression="rank(ts_delta(close, 5))",
            sharpe=3.0,
            fitness=2.0,
            turnover=0.0,
            returns=0.1,
            drawdown=0.0,
        )
        score = compute_composite_score(m)
        self.assertAlmostEqual(score, 1.0, places=4)

    def test_zero_alpha_has_partial_score(self):
        """全零指标时，turnover=0 和 drawdown=0 仍贡献正分。"""
        m = AlphaMetrics(expression="x", sharpe=0, fitness=0, turnover=0, returns=0, drawdown=0)
        score = compute_composite_score(m)
        expected = 0.15 + 0.10  # turnover 反向=1*0.15, drawdown 反向=1*0.10
        self.assertAlmostEqual(score, expected, places=4)

    def test_custom_weights(self):
        m = AlphaMetrics(expression="x", sharpe=1.5, fitness=1.0, turnover=0.5, returns=0.05, drawdown=0.1)
        w = ScoreWeights(sharpe=1.0, fitness=0.0, turnover=0.0, returns=0.0, drawdown=0.0)
        score = compute_composite_score(m, w)
        self.assertAlmostEqual(score, 1.5 / 3.0, places=4)

    def test_assign_writes_back(self):
        items = [
            AlphaMetrics(expression="a", sharpe=2.0, fitness=1.5),
            AlphaMetrics(expression="b", sharpe=1.0, fitness=0.5),
        ]
        assign_composite_scores(items)
        self.assertGreater(items[0].composite_score, 0)
        self.assertGreater(items[0].composite_score, items[1].composite_score)


class TestFilterAndRank(unittest.TestCase):
    """过滤与排序测试。"""

    def _make_list(self):
        return [
            AlphaMetrics(expression="a", sharpe=2.0, fitness=1.5, turnover=0.3, returns=0.05, drawdown=0.1),
            AlphaMetrics(expression="b", sharpe=0.5, fitness=0.3, turnover=0.9, returns=0.01, drawdown=0.4),
            AlphaMetrics(expression="c", sharpe=1.5, fitness=1.2, turnover=0.6, returns=0.03, drawdown=0.2),
        ]

    def test_filter_removes_bad(self):
        result = filter_alphas(self._make_list())
        expressions = [m.expression for m in result]
        self.assertIn("a", expressions)
        self.assertNotIn("b", expressions)

    def test_rank_by_composite(self):
        items = self._make_list()
        assign_composite_scores(items)
        ranked = rank_alphas(items, sort_by="composite_score")
        self.assertEqual(ranked[0].expression, "a")


class TestDiversity(unittest.TestCase):
    """多样性约束测试。"""

    def test_removes_near_duplicates(self):
        items = [
            AlphaMetrics(expression="rank(ts_delta(close, 5))", sharpe=2.0),
            AlphaMetrics(expression="rank(ts_delta(close, 10))", sharpe=1.8),
            AlphaMetrics(expression="-ts_corr(rank(open), rank(volume), 20)", sharpe=1.5),
        ]
        diverse = ensure_diversity(items, min_distance=0.3)
        # 前两个结构接近，应只保留第一个
        expressions = [m.expression for m in diverse]
        self.assertIn("rank(ts_delta(close, 5))", expressions)
        self.assertIn("-ts_corr(rank(open), rank(volume), 20)", expressions)

    def test_empty_input(self):
        self.assertEqual(ensure_diversity([]), [])


class TestRunIteration(unittest.TestCase):
    """迭代优化闭环测试。"""

    def test_single_round(self):
        """一轮迭代后应返回 rounds 和 best 结构。"""

        def mock_simulate(expressions):
            return [
                {
                    "success": True,
                    "data": {
                        "sharpe": 1.8,
                        "fitness": 1.3,
                        "turnover": 0.4,
                        "returns": 0.05,
                        "drawdown": 0.1,
                        "id": f"alpha_{i}",
                    },
                }
                for i, _ in enumerate(expressions)
            ]

        seeds = [
            "rank(ts_delta(close, 5))",
            "-ts_corr(rank(close), rank(volume), 10)",
        ]
        config = IterationConfig(max_rounds=1, top_k_per_round=5)
        result = run_iteration(seeds, mock_simulate, config)

        self.assertIn("rounds", result)
        self.assertIn("best", result)
        self.assertEqual(result["total_rounds"], 1)
        self.assertGreater(result["best_count"], 0)

    def test_multi_round_expands(self):
        """多轮迭代应经历变异与再模拟。"""
        call_count = {"n": 0}

        def mock_simulate(expressions):
            call_count["n"] += 1
            return [
                {
                    "success": True,
                    "data": {
                        "sharpe": 1.5 + 0.1 * call_count["n"],
                        "fitness": 1.1,
                        "turnover": 0.5,
                        "returns": 0.03,
                        "drawdown": 0.15,
                        "id": f"a_{i}_{call_count['n']}",
                    },
                }
                for i, _ in enumerate(expressions)
            ]

        seeds = ["rank(ts_delta(close, 5))"]
        config = IterationConfig(max_rounds=2, top_k_per_round=3, mutations_per_winner=3)
        result = run_iteration(seeds, mock_simulate, config)
        self.assertGreaterEqual(result["total_rounds"], 1)


class TestExtractMetrics(unittest.TestCase):
    """指标提取测试。"""

    def test_missing_data_returns_none(self):
        self.assertIsNone(extract_metrics("x", {"success": True}))

    def test_success_false_returns_none(self):
        self.assertIsNone(extract_metrics("x", {"success": False, "data": {"is": {"sharpe": 1}}}))

    def test_extracts_from_is_dict(self):
        sim_result = {
            "success": True,
            "data": {
                "id": "abc123",
                "is": {
                    "sharpe": 1.40,
                    "fitness": 0.77,
                    "turnover": 0.43,
                    "returns": 0.03,
                    "drawdown": 0.09,
                    "margin": 0.0003,
                },
            },
        }
        m = extract_metrics("rank(close)", sim_result)
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m.sharpe, 1.40)
        self.assertAlmostEqual(m.fitness, 0.77)
        self.assertAlmostEqual(m.turnover, 0.43)
        self.assertEqual(m.alpha_id, "abc123")

    def test_fallback_to_top_level_metrics(self):
        """若 data 无 'is' 键，回退到 data 本身读取指标。"""
        sim_result = {
            "success": True,
            "data": {
                "id": "x1",
                "sharpe": 2.0,
                "fitness": 1.5,
                "turnover": 0.3,
                "returns": 0.05,
                "drawdown": 0.1,
                "margin": 0.001,
            },
        }
        m = extract_metrics("rank(close)", sim_result)
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m.sharpe, 2.0)


if __name__ == "__main__":
    unittest.main()
