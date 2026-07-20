from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from wqb_agent_lab.governance.policy_feedback import (
    aggregate_shadow_evidence,
    evaluate_promotion_gate,
    record_shadow_decision,
    resolve_feedback_mode,
    score_shadow_decisions,
)


class PolicyFeedbackGovernanceTests(unittest.TestCase):
    def test_control_fails_closed_to_shadow_without_measured_evidence(self) -> None:
        governance = resolve_feedback_mode({"mode": "control"}, {})

        self.assertEqual("control", governance["requested_mode"])
        self.assertEqual("shadow", governance["effective_mode"])
        self.assertFalse(governance["promotion_gate"]["passed"])

    def test_shadow_scores_counterfactual_subset_without_mutating_candidate_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local/data/runs/continuous-alpha/run-1"
            run_dir.mkdir(parents=True)
            candidates = [
                {
                    "expression": "future_operator(custom_field)",
                    "behavior_family": "novel_family",
                    "settings": {"novelSetting": "OPEN"},
                    "extensions": {"unknown_llm_field": {"confidence": 0.7}},
                },
                {
                    "expression": "rank(other_field)",
                    "behavior_family": "known_family",
                },
            ]
            output_path = run_dir / "probe_results.json"
            output_path.write_text(
                json.dumps(
                    [
                        {
                            **candidates[0],
                            "metrics": {"sharpe": 1.5, "fitness": 1.2},
                            "checks": [{"name": "SELF_CORRELATION", "result": "PASS"}],
                        },
                        {
                            **candidates[1],
                            "metrics": {"sharpe": 0.5, "fitness": 0.2},
                            "checks": [{"name": "LOW_SHARPE", "result": "FAIL"}],
                        },
                    ]
                ),
                encoding="utf-8",
            )
            record_shadow_decision(
                run_dir,
                stage="probe",
                output_path=output_path.relative_to(root).as_posix(),
                baseline_candidates=candidates,
                recommended_candidates=[candidates[0]],
                governance={"requested_mode": "shadow", "effective_mode": "shadow"},
                caps_applied={"weak": {"max_budget_share": 0.5}},
                overflow_candidates=[candidates[1]],
                now=datetime(2026, 7, 21, 8, 0),
            )

            report_path = score_shadow_decisions(
                root,
                run_dir,
                now=datetime(2026, 7, 21, 9, 0),
            )

            assert report_path is not None
            report = json.loads(report_path.read_text(encoding="utf-8"))
            decision = report["decisions"][0]
            self.assertEqual(2, decision["baseline"]["simulations_observed"])
            self.assertEqual(1, decision["recommended"]["simulations_observed"])
            self.assertEqual(1, decision["recommended"]["submit_ready_count"])
            source = json.loads((run_dir / "policy_feedback_shadow.json").read_text(encoding="utf-8"))
            novel = source["decisions"][0]["baseline_candidates"][0]
            self.assertEqual("OPEN", novel["settings"]["novelSetting"])
            self.assertEqual(0.7, novel["extensions"]["unknown_llm_field"]["confidence"])

    def test_multi_run_aggregate_can_pass_conservative_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp)
            for index in range(3):
                run_dir = runs_root / f"run-{index}"
                run_dir.mkdir()
                (run_dir / "policy_feedback_shadow_evaluation.json").write_text(
                    json.dumps(
                        {
                            "aggregate": {
                                "baseline_simulations_observed": 50,
                                "recommended_simulations_observed": 50,
                                "baseline_submit_ready_count": 5,
                                "recommended_submit_ready_count": 8,
                                "baseline_low_value_count": 25,
                                "recommended_low_value_count": 20,
                                "baseline_distinct_family_count": 5,
                                "recommended_distinct_family_count": 4,
                            }
                        }
                    ),
                    encoding="utf-8",
                )

            evidence = aggregate_shadow_evidence(runs_root)
            gate = evaluate_promotion_gate(evidence)

            self.assertEqual(3, evidence["run_count"])
            self.assertEqual(150, evidence["recommended_simulations_observed"])
            self.assertGreater(evidence["submit_ready_rate_lift"], 0)
            self.assertLess(evidence["low_value_rate_delta"], 0)
            self.assertTrue(gate["passed"])

    def test_configuration_cannot_weaken_conservative_gate_floor(self) -> None:
        gate = evaluate_promotion_gate(
            {
                "run_count": 1,
                "recommended_simulations_observed": 1,
                "submit_ready_rate_lift": -1.0,
                "low_value_rate_delta": 1.0,
                "distinct_family_retention": 0.0,
            },
            {
                "min_runs": 1,
                "min_recommended_simulations": 1,
                "min_submit_ready_rate_lift": -1.0,
                "max_low_value_rate_delta": 1.0,
                "min_distinct_family_retention": 0.0,
            },
        )

        self.assertFalse(gate["passed"])
        self.assertEqual(3, gate["thresholds"]["min_runs"])
        self.assertEqual(100, gate["thresholds"]["min_recommended_simulations"])
        self.assertEqual(0.0, gate["thresholds"]["min_submit_ready_rate_lift"])
        self.assertEqual(-0.02, gate["thresholds"]["max_low_value_rate_delta"])
        self.assertEqual(0.8, gate["thresholds"]["min_distinct_family_retention"])


if __name__ == "__main__":
    unittest.main()
