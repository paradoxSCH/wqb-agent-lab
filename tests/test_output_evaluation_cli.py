from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class OutputEvaluationCliTests(unittest.TestCase):
    def test_cli_writes_run_level_output_evaluation_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "candidate_hypothesis_queue.json").write_text(
                json.dumps({"hypotheses": [{"hypothesis_id": "H1", "primary_proxy": "volume", "kill_conditions": []}]}),
                encoding="utf-8",
            )
            (run_dir / "scan_results_snapshot.json").write_text(
                json.dumps(
                    [
                        {
                            "alpha_id": "A1",
                            "triage_bucket": "low_value",
                            "failure_diagnoses": [{"diagnosis_type": "weak_behavior_proxy", "severity": "high"}],
                            "metrics": {"sharpe": 0.8, "fitness": 0.4},
                        }
                    ]
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, "-m", "scripts.evaluation.output_artifacts", "--run-dir", str(run_dir)],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report_path = run_dir / "output_evaluation_report.json"
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(report["record_count"], 2)
            self.assertIn("output_evaluation_report", completed.stdout)


if __name__ == "__main__":
    unittest.main()
