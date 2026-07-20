from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.diagnosis_policy import evaluate_diagnosis_policies


class DiagnosisPolicyTests(unittest.TestCase):
    def test_evaluator_turns_diagnosis_types_into_quantified_policies(self) -> None:
        rows = [
            self._row("A1", "optimize_next", "overcrowded_skeleton", sharpe=1.91, fitness=1.4),
            self._row("A2", "optimize_next", "overcrowded_skeleton", sharpe=1.72, fitness=1.2),
            self._row("A3", "low_value", "weak_behavior_proxy", sharpe=0.81, fitness=0.4),
            self._row("A4", "low_value", "weak_behavior_proxy", sharpe=0.9, fitness=0.5),
            self._row("A5", "low_value", "field_type_operator_mismatch", error="event inputs"),
            self._row("A6", "direct_submit", "", sharpe=1.8, fitness=1.3),
        ]

        report = evaluate_diagnosis_policies(rows)

        self.assertEqual(report["total_rows"], 6)
        by_type = {row["diagnosis_type"]: row for row in report["policies"]}
        self.assertEqual(by_type["overcrowded_skeleton"]["recommended_policy"], "controlled_structural_repair")
        self.assertEqual(by_type["overcrowded_skeleton"]["repair_count"], 2)
        self.assertGreater(by_type["overcrowded_skeleton"]["repair_candidate_rate"], 0.9)
        self.assertEqual(by_type["weak_behavior_proxy"]["recommended_policy"], "replace_proxy_or_downweight_family")
        self.assertEqual(by_type["weak_behavior_proxy"]["blocked_count"], 2)
        self.assertEqual(by_type["field_type_operator_mismatch"]["recommended_policy"], "static_preflight_block")
        self.assertEqual(by_type["field_type_operator_mismatch"]["budget_policy"], "zero_simulation_until_guarded")
        self.assertGreater(report["budget_saved_estimate"], 0)

    def test_evaluator_includes_known_policy_for_every_current_diagnosis_type(self) -> None:
        rows = [
            self._row("A1", "low_value", "weak_behavior_proxy"),
            self._row("A2", "optimize_next", "overcrowded_skeleton"),
            self._row("A3", "optimize_next", "sub_universe_instability"),
            self._row("A4", "low_value", "field_type_operator_mismatch"),
            self._row("A5", "optimize_next", "turnover_instability"),
            self._row("A6", "optimize_next", "unit_normalization_mismatch"),
            self._row("A7", "low_value", "weight_concentration"),
        ]

        report = evaluate_diagnosis_policies(rows)

        for policy in report["policies"]:
            self.assertNotEqual(policy["recommended_policy"], "manual_review")
            self.assertTrue(policy["success_metric"])
            self.assertTrue(policy["failure_metric"])
            self.assertTrue(policy["next_action"])

    def test_evaluator_splits_policy_by_diagnosis_bucket(self) -> None:
        rows = [
            self._row("A1", "optimize_next", "sub_universe_instability", evidence={"sub_universe_bucket": "mild"}),
            self._row("A2", "low_value", "sub_universe_instability", evidence={"sub_universe_bucket": "severe"}),
            self._row("A3", "optimize_next", "weak_behavior_proxy", evidence={"weak_signal_bucket": "near_pass"}),
            self._row("A4", "low_value", "weak_behavior_proxy", evidence={"weak_signal_bucket": "deep_fail"}),
        ]

        report = evaluate_diagnosis_policies(rows)

        policy_keys = {row["policy_key"] for row in report["policies"]}
        self.assertIn("sub_universe_instability:mild", policy_keys)
        self.assertIn("sub_universe_instability:severe", policy_keys)
        self.assertIn("weak_behavior_proxy:near_pass", policy_keys)
        self.assertIn("weak_behavior_proxy:deep_fail", policy_keys)

    def test_cli_writes_policy_evaluation_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            snapshot = run_dir / "scan_results_snapshot.json"
            snapshot.write_text(
                json.dumps(
                    [
                        self._row("A1", "optimize_next", "overcrowded_skeleton"),
                        self._row("A2", "low_value", "field_type_operator_mismatch", error="event inputs"),
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.evaluation.diagnosis_policies",
                    "--run-dir",
                    str(run_dir),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            artifact = run_dir / "diagnosis_policy_evaluation.json"
            self.assertTrue(artifact.exists())
            report = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(report["policy_count"], 2)
            self.assertIn("diagnosis_policy_evaluation", completed.stdout)

    def _row(
        self,
        alpha_id: str,
        bucket: str,
        diagnosis_type: str,
        *,
        sharpe: float = 1.2,
        fitness: float = 0.9,
        error: str = "",
        evidence: dict[str, object] | None = None,
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "alpha_id": alpha_id,
            "triage_bucket": bucket,
            "metrics": {"sharpe": sharpe, "fitness": fitness, "turnover": 0.1},
            "error": error,
            "family": "test_family",
            "skeleton": f"test_family:{diagnosis_type or 'pass'}",
        }
        if diagnosis_type:
            row["failure_diagnoses"] = [
                {
                    "diagnosis_type": diagnosis_type,
                    "severity": "high" if bucket == "low_value" else "medium",
                    "check_names": [],
                    "evidence": evidence or {},
                    "recommended_action": "test_action",
                    "generation_feedback": ["test_feedback"],
                }
            ]
        else:
            row["failure_diagnoses"] = []
        return row


if __name__ == "__main__":
    unittest.main()
