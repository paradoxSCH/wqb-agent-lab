from __future__ import annotations

import unittest

from src.output_evaluation.validators import (
    KNOWN_OPERATORS,
    validate_candidate_hypothesis_queue,
    validate_expression_candidates,
    validate_memory_sync_report,
    validate_report_text,
)
from src.wqb.operator_catalog import load_operator_names


class OutputEvaluationValidatorTests(unittest.TestCase):
    def test_expression_operator_allowlist_matches_current_catalog(self) -> None:
        catalog = load_operator_names()

        self.assertEqual(catalog, KNOWN_OPERATORS)

    def test_expression_validator_reports_unknown_operator_before_simulation(self) -> None:
        candidates = [
            {"expression": "rank(ts_max(close, 20))"},
            {"expression": "rank(ts_std_dev(returns, 20))"},
        ]

        record = validate_expression_candidates(
            "scan_config_test.json",
            candidates,
            field_types={"close": "matrix", "returns": "matrix"},
        )

        self.assertEqual("block", record.validation_status)
        self.assertEqual(1, record.metrics["invalid_count"])
        self.assertIn("unknown_operator", {diagnosis.diagnosis_type for diagnosis in record.diagnoses})

    def test_candidate_queue_blocks_missing_kill_condition_and_price_volume_primary_proxy(self) -> None:
        payload = {
            "hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "mechanism": "attention_only",
                    "primary_proxy": "volume",
                    "kill_conditions": [],
                    "preflight_requirements": [],
                }
            ]
        }

        record = validate_candidate_hypothesis_queue("candidate_hypothesis_queue.json", payload)

        diagnosis_types = {diagnosis.diagnosis_type for diagnosis in record.diagnoses}
        self.assertEqual(record.validation_status, "block")
        self.assertIn("pure_price_volume_primary_proxy", diagnosis_types)
        self.assertIn("missing_kill_condition", diagnosis_types)

    def test_expression_validator_blocks_event_operator_and_missing_field_before_simulation(self) -> None:
        candidates = [
            {"expression": "ts_delta(event_field, 20) / cap"},
            {"expression": "group_rank(rank(missing_field), industry)"},
        ]
        field_types = {"event_field": "event", "cap": "matrix"}

        record = validate_expression_candidates("scan_config_test.json", candidates, field_types=field_types)

        diagnosis_types = {diagnosis.diagnosis_type for diagnosis in record.diagnoses}
        self.assertEqual(record.validation_status, "block")
        self.assertIn("field_type_operator_mismatch", diagnosis_types)
        self.assertIn("missing_field_reference", diagnosis_types)
        self.assertEqual(record.metrics["budget_saved_estimate"], 2)

    def test_expression_validator_blocks_vector_field_arithmetic_before_simulation(self) -> None:
        candidates = [
            {"expression": "group_rank(rank(fnd6_newqeventv110_hedgeglq / close), industry)"},
            {"expression": "rank(ts_mean(fnd6_dvrated, 60))"},
            {"expression": "group_rank(rank(good_field / close), industry)"},
        ]
        field_types = {
            "fnd6_newqeventv110_hedgeglq": "vector",
            "fnd6_dvrated": "vector",
            "good_field": "matrix",
            "close": "matrix",
        }

        record = validate_expression_candidates("scan_config_test.json", candidates, field_types=field_types)

        mismatches = [
            diagnosis
            for diagnosis in record.diagnoses
            if diagnosis.diagnosis_type == "field_type_operator_mismatch"
        ]
        self.assertEqual(record.validation_status, "block")
        self.assertEqual(record.metrics["budget_saved_estimate"], 2)
        self.assertEqual({item.evidence["field"] for item in mismatches}, {"fnd6_newqeventv110_hedgeglq", "fnd6_dvrated"})

    def test_memory_validator_blocks_unsupported_long_term_promotion(self) -> None:
        record = validate_memory_sync_report(
            "memory_sync_report.json",
            {
                "nodes_written": 3,
                "events_recorded": 0,
                "promotions": [{"target": "long_term", "evidence_level": "L1"}],
            },
        )

        diagnosis_types = {diagnosis.diagnosis_type for diagnosis in record.diagnoses}
        self.assertEqual(record.validation_status, "block")
        self.assertIn("missing_memory_event_trace", diagnosis_types)
        self.assertIn("unsupported_memory_promotion", diagnosis_types)

    def test_report_validator_catches_mermaid_syntax_and_submit_status_conflation(self) -> None:
        text = "Syntax error in text\nsubmit-ready candidates were submitted successfully"
        record = validate_report_text("wqb-agent-latest-workflow-uml.html", text)

        diagnosis_types = {diagnosis.diagnosis_type for diagnosis in record.diagnoses}
        self.assertEqual(record.validation_status, "block")
        self.assertIn("render_syntax_error", diagnosis_types)
        self.assertIn("submit_status_conflation", diagnosis_types)


if __name__ == "__main__":
    unittest.main()
