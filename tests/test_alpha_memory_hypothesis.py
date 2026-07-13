from __future__ import annotations

import unittest

from src.alpha_memory.hypothesis import (
    HypothesisDraft,
    classify_wqb_action_lane,
    validate_hypothesis,
)


class AlphaMemoryHypothesisTests(unittest.TestCase):
    def test_validate_hypothesis_requires_actionable_proxy_and_kill_condition(self) -> None:
        draft = HypothesisDraft(
            behavior_thesis="Quality-value mispricing",
            mechanism="Investors underreact to improving cashflow quality.",
            proxies=["cashflow_quality", "valuation_compression"],
            operator_skeletons=["rank(ts_mean(cashflow_quality, 60)) - rank(close)"],
            kill_conditions=["high self-corr", "LOW_FITNESS", "duplicate skeleton"],
            success_criteria=["near-pass", "low self-corr"],
        )

        result = validate_hypothesis(draft)

        self.assertTrue(result.ok)
        self.assertEqual(result.missing_fields, [])

    def test_validate_hypothesis_rejects_decorative_thesis(self) -> None:
        draft = HypothesisDraft(
            behavior_thesis="Investors are emotional",
            mechanism="A broad market story.",
            proxies=[],
            operator_skeletons=[],
            kill_conditions=[],
            success_criteria=["sounds good"],
        )

        result = validate_hypothesis(draft)

        self.assertFalse(result.ok)
        self.assertIn("proxies", result.missing_fields)
        self.assertIn("operator_skeletons", result.missing_fields)
        self.assertIn("kill_conditions", result.missing_fields)

    def test_classify_wqb_action_lane_from_metrics(self) -> None:
        self.assertEqual(classify_wqb_action_lane({"submit_ready": True}), "submit")
        self.assertEqual(classify_wqb_action_lane({"near_pass": True, "self_corr": 0.62}), "repair")
        self.assertEqual(classify_wqb_action_lane({"pass": True, "self_corr": 0.18}), "scale")
        self.assertEqual(classify_wqb_action_lane({"pass": True, "self_corr": None}), "holdout")
        self.assertEqual(classify_wqb_action_lane({"pass": True, "self_corr": "low"}), "holdout")
        self.assertEqual(classify_wqb_action_lane({"duplicate": True}), "block")
        self.assertEqual(classify_wqb_action_lane({"new_thesis": True}), "probe")
        self.assertEqual(classify_wqb_action_lane({}), "holdout")


if __name__ == "__main__":
    unittest.main()
