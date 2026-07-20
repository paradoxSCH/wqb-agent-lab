from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.decision_attribution import record_scan_decision, score_decision_outcomes
from src.policy_effectiveness import write_policy_effectiveness_report


class PolicyEffectivenessTests(unittest.TestCase):
    def test_write_policy_effectiveness_report_aggregates_policy_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "daily-20260704"
            run_dir.mkdir(parents=True)
            result_path = run_dir / "direction_probe_results.json"
            candidates = [
                self._candidate("rank(a)"),
                self._candidate("rank(b)"),
                self._candidate("rank(c)"),
            ]
            record_scan_decision(
                root,
                run_dir,
                stage="direction_probe",
                stage_budget=3,
                remaining_stage_budget=3,
                remaining_daily_budget=1000,
                source_config=Path("configs/source/scan_config_round1.json"),
                sliced_config=Path("configs/run/direction_probe_3.json"),
                output_path=result_path,
                candidates=candidates,
            )
            self._write_json(
                result_path,
                [
                    self._result(1.5, 1.1, []),
                    self._result(1.7, 1.2, ["SELF_CORRELATION"]),
                    self._result(0.2, 0.1, ["LOW_SHARPE", "LOW_FITNESS"]),
                ],
            )
            score_decision_outcomes(run_dir)

            report_path = write_policy_effectiveness_report(run_dir)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            row = report["policies"][0]

            self.assertEqual(row["diagnosis_type"], "overcrowded_skeleton")
            self.assertEqual(row["simulations_spent"], 3)
            self.assertEqual(row["submit_ready_count"], 1)
            self.assertEqual(row["near_pass_count"], 2)
            self.assertEqual(row["low_value_count"], 1)
            self.assertEqual(row["roi_per_1000"], 333.333)

    def test_cli_writes_policy_effectiveness_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            self._write_json(
                run_dir / "decision_attribution.json",
                [
                    {
                        "policy_actions_used": [{"diagnosis_type": "weak_behavior_proxy", "budget_action": "downweight_family_or_proxy"}],
                        "outcome": {
                            "simulations_spent": 10,
                            "submit_ready_count": 0,
                            "near_pass_count": 1,
                            "low_value_count": 9,
                            "roi_per_1000": 0.0,
                        },
                    }
                ],
            )

            completed = subprocess.run(
                [sys.executable, "-m", "scripts.evaluation.policy_effectiveness", "--run-dir", str(run_dir)],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((run_dir / "policy_effectiveness_report.json").exists())
            self.assertIn("policy_effectiveness_report", completed.stdout)

    def _candidate(self, expression: str) -> dict[str, object]:
        return {
            "expression": expression,
            "behavior_family": "family_a",
            "wqb_action_lane": "repair_probe",
            "policy_feedback": {
                "budget_actions": {
                    "overcrowded_skeleton": {
                        "diagnosis_type": "overcrowded_skeleton",
                        "budget_action": "allocate_controlled_repair_budget",
                        "max_budget_share": 0.15,
                    }
                }
            },
        }

    def _result(self, sharpe: float, fitness: float, failures: list[str]) -> dict[str, object]:
        return {
            "metrics": {"sharpe": sharpe, "fitness": fitness, "turnover": 0.1},
            "checks": [
                {"name": "LOW_SHARPE", "result": "FAIL" if "LOW_SHARPE" in failures else "PASS"},
                {"name": "LOW_FITNESS", "result": "FAIL" if "LOW_FITNESS" in failures else "PASS"},
                {"name": "SELF_CORRELATION", "result": "FAIL" if "SELF_CORRELATION" in failures else "PASS"},
            ],
        }

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
