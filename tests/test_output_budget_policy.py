from __future__ import annotations

import unittest

from src.output_evaluation.budget_policy import build_budget_policy_actions


class OutputBudgetPolicyTests(unittest.TestCase):
    def test_policy_actions_convert_diagnoses_into_budget_controls(self) -> None:
        report = {
            "policies": [
                {"diagnosis_type": "field_type_operator_mismatch", "policy_confidence": "medium"},
                {"diagnosis_type": "weak_behavior_proxy", "policy_confidence": "high"},
                {"diagnosis_type": "overcrowded_skeleton", "policy_confidence": "high"},
                {"diagnosis_type": "sub_universe_instability", "policy_confidence": "high"},
            ]
        }

        actions = build_budget_policy_actions(report)
        by_type = {action["diagnosis_type"]: action for action in actions}

        self.assertEqual(by_type["field_type_operator_mismatch"]["budget_action"], "block_until_preflight_guard")
        self.assertEqual(by_type["weak_behavior_proxy"]["budget_action"], "downweight_family_or_proxy")
        self.assertEqual(by_type["overcrowded_skeleton"]["budget_action"], "allocate_controlled_repair_budget")
        self.assertLessEqual(by_type["overcrowded_skeleton"]["max_budget_share"], 0.15)
        self.assertEqual(by_type["sub_universe_instability"]["budget_action"], "allocate_grouping_probe_budget")

    def test_policy_actions_use_bucket_when_available(self) -> None:
        report = {
            "policies": [
                {"diagnosis_type": "weak_behavior_proxy", "policy_key": "weak_behavior_proxy:deep_fail", "bucket": "deep_fail", "policy_confidence": "high"},
                {"diagnosis_type": "weak_behavior_proxy", "policy_key": "weak_behavior_proxy:near_pass", "bucket": "near_pass", "policy_confidence": "medium"},
                {"diagnosis_type": "sub_universe_instability", "policy_key": "sub_universe_instability:severe", "bucket": "severe", "policy_confidence": "high"},
                {"diagnosis_type": "weight_concentration", "policy_key": "weight_concentration:severe", "bucket": "severe", "policy_confidence": "high"},
            ]
        }

        actions = build_budget_policy_actions(report)
        by_key = {action["policy_key"]: action for action in actions}

        self.assertEqual(by_key["weak_behavior_proxy:deep_fail"]["budget_action"], "replace_proxy_before_resimulation")
        self.assertEqual(by_key["weak_behavior_proxy:near_pass"]["budget_action"], "allocate_small_parameter_repair")
        self.assertEqual(by_key["sub_universe_instability:severe"]["budget_action"], "replace_unstable_universe_proxy")
        self.assertEqual(by_key["weight_concentration:severe"]["budget_action"], "replace_concentrated_expression_structure")


if __name__ == "__main__":
    unittest.main()
