from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from wqb_agent_lab.evaluation.agent import evaluate_ablation, summarize_run_dir


class AgentEvaluationAblationTests(unittest.TestCase):
    def test_evaluate_ablation_marks_full_agent_useful_when_lift_beats_cost(self) -> None:
        report = evaluate_ablation(
            {
                "baseline": [
                    {
                        "simulations": 1000,
                        "submit_ready": 2,
                        "final_submitted": 1,
                        "independent_submit_clusters": 1,
                        "low_value": 700,
                        "duplicates": 80,
                        "complexity_cost": 20,
                    }
                ],
                "full_agent": [
                    {
                        "simulations": 1000,
                        "submit_ready": 5,
                        "final_submitted": 3,
                        "independent_submit_clusters": 3,
                        "low_value": 450,
                        "duplicates": 30,
                        "complexity_cost": 60,
                    }
                ],
            }
        )

        self.assertEqual(report["verdict"], "useful")
        self.assertEqual(report["variants"]["full_agent"]["submit_ready_per_1000"], 5.0)
        self.assertEqual(report["delta_vs_baseline"]["full_agent"]["submit_ready_per_1000"], 3.0)
        self.assertLess(report["variants"]["full_agent"]["wasted_budget_rate"], report["variants"]["baseline"]["wasted_budget_rate"])

    def test_evaluate_ablation_marks_agent_bloated_without_outcome_lift(self) -> None:
        report = evaluate_ablation(
            {
                "baseline": [{"simulations": 1000, "submit_ready": 3, "low_value": 500, "complexity_cost": 10}],
                "full_agent": [{"simulations": 1000, "submit_ready": 3, "low_value": 520, "complexity_cost": 180}],
            }
        )

        self.assertEqual(report["verdict"], "bloated")
        self.assertLessEqual(report["delta_vs_baseline"]["full_agent"]["submit_ready_per_1000"], 0.0)

    def test_summarize_run_dir_reads_workflow_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "daily-run"
            run_dir.mkdir()
            self._write_json(
                run_dir / "daily_budget_ledger.json",
                {
                    "daily_run_tag": "daily-run",
                    "spent_simulations": 120,
                    "closed_loop": {"counts": {"submit_ready": 2, "low_value": 80}},
                },
            )
            self._write_json(
                run_dir / "decision_attribution.json",
                [
                    {
                        "outcome": {
                            "simulations_spent": 120,
                            "submit_ready_count": 1,
                            "near_pass_count": 10,
                            "low_value_count": 80,
                            "self_corr_fail_count": 9,
                        }
                    }
                ],
            )

            summary = summarize_run_dir(run_dir)

            self.assertEqual(summary["simulations"], 120)
            self.assertEqual(summary["submit_ready"], 2)
            self.assertEqual(summary["low_value"], 80)
            self.assertEqual(summary["decision_count"], 1)

    def test_cli_writes_ablation_report_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.json"
            full = root / "full.json"
            out = root / "eval"
            self._write_json(baseline, [{"simulations": 1000, "submit_ready": 1, "low_value": 800, "complexity_cost": 10}])
            self._write_json(full, [{"simulations": 1000, "submit_ready": 4, "low_value": 500, "complexity_cost": 50}])

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.evaluation.agent_ablation",
                    "--variant",
                    f"baseline={baseline}",
                    "--variant",
                    f"full_agent={full}",
                    "--output-dir",
                    str(out),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((out / "ablation_report.json").exists())
            self.assertTrue((out / "summary.md").exists())
            self.assertIn("verdict", completed.stdout)

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
