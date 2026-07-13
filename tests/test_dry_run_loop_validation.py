from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.loop_validation import run_dry_run_loop_validation


class DryRunLoopValidationTests(unittest.TestCase):
    def test_dry_run_loop_writes_closed_loop_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = run_dry_run_loop_validation(root, run_tag="dry-run-validation")

            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "dry-run-validation"
            self.assertEqual(result["status"], "complete")
            self.assertEqual(Path(result["run_dir"]), run_dir)
            for artifact_name in (
                "output_evaluation_report.json",
                "candidate_hypothesis_queue.json",
                "decision_attribution.json",
                "policy_effectiveness_report.json",
                "memory_governance_report.json",
                "memory_sync_report.json",
                "dry_run_loop_validation_report.json",
            ):
                self.assertTrue((run_dir / artifact_name).exists(), artifact_name)

            output_evaluation = self._read_json(run_dir / "output_evaluation_report.json")
            action_types = {
                action["diagnosis_type"]
                for action in output_evaluation["budget_policy_actions"]
            }
            self.assertIn("weak_behavior_proxy", action_types)
            self.assertIn("overcrowded_skeleton", action_types)

            queue = self._read_json(run_dir / "candidate_hypothesis_queue.json")
            hypotheses = queue["hypotheses"]
            self.assertGreater(len(hypotheses), 0)
            self.assertTrue(
                any(hypothesis["policy_feedback"]["budget_actions"] for hypothesis in hypotheses),
                "Policy feedback should flow into generated hypotheses.",
            )

            decisions = self._read_json(run_dir / "decision_attribution.json")
            self.assertGreater(len(decisions), 0)
            self.assertGreater(decisions[0]["outcome"]["simulations_spent"], 0)
            self.assertTrue(decisions[0]["policy_actions_used"])

            memory_governance = self._read_json(run_dir / "memory_governance_report.json")
            self.assertGreater(memory_governance["policy_count"], 0)
            for row in memory_governance["policies"]:
                if row["permission"]["can_promote"] or row["permission"]["can_be_default_prior"]:
                    self.assertEqual(row["permission"]["max_budget_policy"], "policy_evaluator_required")
                self.assertFalse(row["permission"]["can_block_generation"])

            memory_sync = self._read_json(run_dir / "memory_sync_report.json")
            self.assertGreaterEqual(memory_sync["events_recorded"], 3)

    def _read_json(self, path: Path) -> object:
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
