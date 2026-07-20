from __future__ import annotations

import unittest

from wqb_agent_lab.evaluation.failure_diagnosis import diagnose_failure_objects


class FailureDiagnosisTests(unittest.TestCase):
    def test_low_fitness_and_low_sharpe_become_weak_signal_diagnosis(self) -> None:
        row = {
            "alpha_id": "weak-1",
            "expression": "group_rank(rank(field) / 10, industry)",
            "metrics": {"sharpe": 0.82, "fitness": 0.41, "turnover": 0.08},
            "checks": [
                {"name": "LOW_SHARPE", "result": "FAIL", "limit": 1.25, "value": 0.82},
                {"name": "LOW_FITNESS", "result": "FAIL", "limit": 1.0, "value": 0.41},
            ],
            "family": "attention_amplified_anomaly",
            "skeleton": "attention_amplified_anomaly:field",
        }

        diagnoses = diagnose_failure_objects(row)

        self.assertEqual(diagnoses[0]["diagnosis_type"], "weak_behavior_proxy")
        self.assertEqual(diagnoses[0]["severity"], "high")
        self.assertIn("LOW_SHARPE", diagnoses[0]["check_names"])
        self.assertIn("LOW_FITNESS", diagnoses[0]["check_names"])
        self.assertEqual(diagnoses[0]["recommended_action"], "replace_proxy_or_behavior_thesis")
        self.assertIn("do_not_scale", diagnoses[0]["generation_feedback"])

    def test_self_corr_becomes_overcrowded_skeleton_diagnosis(self) -> None:
        row = {
            "alpha_id": "corr-1",
            "expression": "group_rank(rank(field) + rank(-returns), industry)",
            "metrics": {"sharpe": 1.91, "fitness": 1.4, "turnover": 0.11},
            "checks": [{"name": "SELF_CORRELATION", "result": "FAIL", "limit": 0.7, "value": 0.86}],
            "family": "quality_value_reversal",
            "skeleton": "quality_value_reversal:field",
        }

        diagnoses = diagnose_failure_objects(row)

        self.assertEqual(diagnoses[0]["diagnosis_type"], "overcrowded_skeleton")
        self.assertEqual(diagnoses[0]["severity"], "medium")
        self.assertEqual(diagnoses[0]["recommended_action"], "structural_self_corr_escape")
        self.assertIn("change_operator_chassis", diagnoses[0]["generation_feedback"])

    def test_only_very_near_self_corr_failures_use_light_repair(self) -> None:
        near = {
            "alpha_id": "corr-near-1",
            "expression": "group_rank(rank(field) + rank(-returns), industry)",
            "metrics": {"sharpe": 1.91, "fitness": 1.4, "turnover": 0.11},
            "checks": [{"name": "SELF_CORRELATION", "result": "FAIL", "limit": 0.7, "value": 0.715}],
            "family": "quality_value_reversal",
        }
        not_near = {
            **near,
            "alpha_id": "corr-not-near-1",
            "checks": [{"name": "SELF_CORRELATION", "result": "FAIL", "limit": 0.7, "value": 0.73}],
        }

        near_diag = diagnose_failure_objects(near)[0]
        not_near_diag = diagnose_failure_objects(not_near)[0]

        self.assertEqual(near_diag["evidence"]["self_corr_bucket"], "mild")
        self.assertEqual(near_diag["recommended_action"], "light_self_corr_repair")
        self.assertEqual(not_near_diag["evidence"]["self_corr_bucket"], "moderate")
        self.assertEqual(not_near_diag["recommended_action"], "structural_self_corr_escape")

    def test_extreme_self_corr_recommends_replacing_signal_not_repairing(self) -> None:
        row = {
            "alpha_id": "corr-extreme-1",
            "expression": "group_rank(rank(field) + rank(-returns), industry)",
            "metrics": {"sharpe": 1.91, "fitness": 1.4, "turnover": 0.11},
            "checks": [{"name": "SELF_CORRELATION", "result": "FAIL", "limit": 0.7, "value": 0.94}],
            "family": "quality_value_reversal",
            "skeleton": "quality_value_reversal:field",
        }

        diagnoses = diagnose_failure_objects(row)

        self.assertEqual(diagnoses[0]["diagnosis_type"], "overcrowded_skeleton")
        self.assertEqual(diagnoses[0]["severity"], "high")
        self.assertEqual(diagnoses[0]["evidence"]["self_corr_bucket"], "extreme")
        self.assertEqual(diagnoses[0]["recommended_action"], "replace_overcrowded_signal")
        self.assertIn("replace_primary_field_or_behavior_proxy", diagnoses[0]["generation_feedback"])

    def test_event_input_operator_error_becomes_field_type_diagnosis(self) -> None:
        row = {
            "expression": "ts_delta(event_field, 20) / cap",
            "metrics": {},
            "checks": [],
            "error": "Simulation returned message: Operator ts_delta does not support event inputs.",
            "family": "event_field_family",
            "skeleton": "event_field_family:event_field",
        }

        diagnoses = diagnose_failure_objects(row)

        self.assertEqual(diagnoses[0]["diagnosis_type"], "field_type_operator_mismatch")
        self.assertEqual(diagnoses[0]["severity"], "high")
        self.assertEqual(diagnoses[0]["recommended_action"], "add_static_field_operator_guard")
        self.assertIn("block_event_field_time_series_operator_pair", diagnoses[0]["generation_feedback"])

    def test_severe_sub_universe_gap_recommends_proxy_replacement(self) -> None:
        row = {
            "alpha_id": "sub-1",
            "expression": "group_rank(rank(field), industry)",
            "metrics": {"sharpe": 1.7, "fitness": 1.2, "turnover": 0.10},
            "checks": [{"name": "LOW_SUB_UNIVERSE_SHARPE", "result": "FAIL", "limit": 0.70, "value": 0.21}],
            "settings": {"neutralization": "MARKET"},
            "family": "unstable_family",
            "skeleton": "unstable_family:field",
        }

        diagnoses = diagnose_failure_objects(row)

        self.assertEqual(diagnoses[0]["diagnosis_type"], "sub_universe_instability")
        self.assertEqual(diagnoses[0]["severity"], "high")
        self.assertEqual(diagnoses[0]["evidence"]["sub_universe_bucket"], "severe")
        self.assertEqual(diagnoses[0]["recommended_action"], "replace_unstable_universe_proxy")

    def test_weak_signal_bucket_separates_near_pass_from_deep_fail(self) -> None:
        near = {
            "alpha_id": "near-1",
            "metrics": {"sharpe": 1.18, "fitness": 0.93, "turnover": 0.10},
            "checks": [
                {"name": "LOW_SHARPE", "result": "FAIL", "limit": 1.25, "value": 1.18},
                {"name": "LOW_FITNESS", "result": "FAIL", "limit": 1.0, "value": 0.93},
            ],
        }
        deep = {
            "alpha_id": "deep-1",
            "metrics": {"sharpe": 0.62, "fitness": 0.25, "turnover": 0.10},
            "checks": [
                {"name": "LOW_SHARPE", "result": "FAIL", "limit": 1.25, "value": 0.62},
                {"name": "LOW_FITNESS", "result": "FAIL", "limit": 1.0, "value": 0.25},
            ],
        }

        near_diag = diagnose_failure_objects(near)[0]
        deep_diag = diagnose_failure_objects(deep)[0]

        self.assertEqual(near_diag["evidence"]["weak_signal_bucket"], "near_pass")
        self.assertEqual(near_diag["recommended_action"], "local_parameter_or_weight_repair")
        self.assertEqual(deep_diag["evidence"]["weak_signal_bucket"], "deep_fail")
        self.assertEqual(deep_diag["recommended_action"], "replace_proxy_or_behavior_thesis")

    def test_concentrated_weight_gets_diagnosis_object(self) -> None:
        row = {
            "alpha_id": "weight-1",
            "metrics": {"sharpe": 1.5, "fitness": 1.2, "turnover": 0.10},
            "checks": [{"name": "CONCENTRATED_WEIGHT", "result": "FAIL", "limit": 0.10, "value": 0.24}],
        }

        diagnoses = diagnose_failure_objects(row)

        self.assertEqual(diagnoses[0]["diagnosis_type"], "weight_concentration")
        self.assertEqual(diagnoses[0]["evidence"]["weight_concentration_bucket"], "severe")
        self.assertEqual(diagnoses[0]["recommended_action"], "replace_concentrated_expression_structure")


if __name__ == "__main__":
    unittest.main()
