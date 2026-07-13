"""Phase 3 变异与优化策略测试。"""

from __future__ import annotations

import unittest

from src.refiner import generate_mutations, mutate_field_all_variants, mutate_lookback


class RefinerTests(unittest.TestCase):
    def test_mutate_field_all_variants_keeps_same_field_group(self) -> None:
        variants = list(mutate_field_all_variants("rank(ts_delta(close, 5))"))

        self.assertTrue(any("open" in expr for expr in variants))
        self.assertFalse(any("target_price" in expr for expr in variants))

    def test_mutate_lookback_generates_window_perturbations(self) -> None:
        variants = list(mutate_lookback("rank(ts_delta(close, 20))"))

        self.assertIn("rank(ts_delta(close, 10))", variants)
        self.assertIn("rank(ts_delta(close, 22))", variants)

    def test_generate_mutations_filters_original_and_deduplicates(self) -> None:
        mutations = generate_mutations("rank(ts_delta(close, 5))", max_per_type=5)

        self.assertTrue(mutations)
        self.assertNotIn("rank(ts_delta(close, 5))", mutations)
        self.assertEqual(len(mutations), len(set(expr.replace(" ", "") for expr in mutations)))


if __name__ == "__main__":
    unittest.main()