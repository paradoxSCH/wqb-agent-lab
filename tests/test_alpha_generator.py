"""Phase 3 Alpha 生成引擎测试。"""

from __future__ import annotations

import unittest

from wqb_agent_lab.research.alpha_generator import (
    GenerationConstraints,
    deduplicate_expressions,
    generate_category_alphas,
    generate_field_driven_alphas,
    generate_template_library,
    rank_field_candidates,
)


class AlphaGeneratorTests(unittest.TestCase):
    def test_generate_template_library_returns_deduplicated_expressions(self) -> None:
        expressions = generate_template_library(category="pv")

        self.assertTrue(expressions)
        self.assertEqual(len(expressions), len(set(expr.replace(" ", "") for expr in expressions)))
        self.assertTrue(any("ts_delta" in expr for expr in expressions))
        self.assertTrue(any("ts_std_dev" in expr for expr in expressions))
        self.assertFalse(any("ts_std(" in expr for expr in expressions))

    def test_generate_category_alphas_uses_category_fields(self) -> None:
        expressions = generate_category_alphas("fundamental")

        self.assertTrue(expressions)
        self.assertTrue(any("revenue" in expr or "assets" in expr for expr in expressions))
        self.assertFalse(any("target_price" in expr for expr in expressions))

    def test_rank_field_candidates_prefers_high_coverage_and_low_alpha_count(self) -> None:
        field_records = [
            {"id": "close", "coverage": 0.98, "alphaCount": 200, "datasetId": "pv1", "category": "pv"},
            {"id": "cash", "coverage": 0.90, "alphaCount": 5, "valueScore": 1.5, "datasetId": "fund1", "category": "fundamental"},
            {"id": "target_price", "coverage": 0.70, "alphaCount": 2, "datasetId": "analyst1", "category": "analyst"},
        ]

        ranked = rank_field_candidates(field_records, min_coverage=0.8)

        self.assertEqual("cash", ranked[0].field_id)
        self.assertEqual("fundamental", ranked[0].category)

    def test_generate_field_driven_alphas_filters_by_category_and_constraints(self) -> None:
        field_records = [
            {"id": "cash", "coverage": 0.91, "alphaCount": 3, "valueScore": 1.2, "datasetId": "fund1", "category": "fundamental"},
            {"id": "debt", "coverage": 0.89, "alphaCount": 8, "valueScore": 1.1, "datasetId": "fund1", "category": "fundamental"},
            {"id": "close", "coverage": 0.99, "alphaCount": 300, "datasetId": "pv1", "category": "pv"},
        ]
        constraints = GenerationConstraints(max_expression_length=80)

        expressions = generate_field_driven_alphas(
            field_records,
            category="fundamental",
            top_n=2,
            constraints=constraints,
        )

        self.assertTrue(expressions)
        self.assertTrue(all("cash" in expr or "debt" in expr for expr in expressions))
        self.assertFalse(any("close" in expr for expr in expressions))

    def test_deduplicate_expressions_rejects_redundant_wrappers(self) -> None:
        expressions = [
            "rank(close)",
            " rank ( close ) ",
            "rank(rank(close))",
            "",
        ]

        deduplicated = deduplicate_expressions(expressions)

        self.assertEqual(["rank(close)"], deduplicated)


if __name__ == "__main__":
    unittest.main()
