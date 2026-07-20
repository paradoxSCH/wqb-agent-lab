from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.agent_memory_sync import sync_run_memory


class AgentMemorySyncTests(unittest.TestCase):
    def test_sync_run_writes_memory_report_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "daily-20260704"
            run_dir.mkdir(parents=True)
            self._write_json(
                run_dir / "daily_budget_ledger.json",
                {
                    "daily_run_tag": "daily-20260704",
                    "date": "2026-07-04",
                    "spent_simulations": 3,
                    "current_stage": "direction_probe_complete",
                    "stage_order": ["direction_probe"],
                },
            )
            self._write_json(
                run_dir / "direction_probe_results.json",
                [
                    {
                        "alpha_id": "A1",
                        "expression": "rank(ts_mean(cashflow, 60)) - rank(close)",
                        "metrics": {"sharpe": 1.7, "fitness": 1.2},
                        "checks": [{"name": "SELF_CORRELATION", "result": "FAIL"}],
                        "note": "quality_value_reversal: synthetic",
                    }
                ],
            )
            self._write_json(run_dir / "optimize_next.json", [{"alpha_id": "A1", "family": "quality_value_reversal"}])
            self._write_json(run_dir / "low_value_avoid.json", [{"skeleton": "quality_value_reversal:cashflow", "reason": "LOW_FITNESS"}])
            self._write_json(
                run_dir / "self_corr_repair_effect_summary.json",
                {
                    "self_corr_bucket_counts": {"mild": 1, "moderate": 2, "extreme": 1},
                    "repair_simulations": 6,
                    "repair_metric_pass_clean_or_pending_count": 2,
                    "repair_fail_counts": {"SELF_CORRELATION": 3, "LOW_FITNESS": 1},
                    "submitted_confirmed": ["A2"],
                    "accepted_but_unconfirmed": ["A3"],
                    "manual_review_or_platform_lag": ["A4"],
                },
            )
            self._write_json(
                root / ".local" / "data" / "behavioral_proxy" / "behavioral_proxy_map.json",
                {"mechanisms": [{"mechanism": "quality_value_mispricing", "budget_policy": "controlled"}]},
            )

            result = sync_run_memory(root, run_dir)

            report = json.loads((run_dir / "memory_sync_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["daily_run_tag"], "daily-20260704")
            self.assertGreaterEqual(report["nodes_written"], 2)
            self.assertGreaterEqual(report["events_recorded"], 4)
            self.assertEqual(report["artifact_counts"]["stage_results"], 1)
            self.assertIn("optimize_next", report["artifact_counts"])
            self.assertEqual(report["artifact_counts"]["self_corr_repair_effect_summary"], 1)
            self.assertEqual(report["repair_effect_summary"]["submitted_confirmed_count"], 1)
            self.assertEqual(report["repair_effect_summary"]["accepted_but_unconfirmed_count"], 1)
            self.assertEqual(report["repair_effect_summary"]["manual_review_or_platform_lag_count"], 1)
            self.assertEqual(result.report_path, run_dir / "memory_sync_report.json")

    def test_cli_syncs_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "daily-20260704"
            run_dir.mkdir(parents=True)
            self._write_json(
                run_dir / "daily_budget_ledger.json",
                {
                    "daily_run_tag": "daily-20260704",
                    "date": "2026-07-04",
                    "spent_simulations": 1,
                    "current_stage": "direction_probe_complete",
                    "stage_order": ["direction_probe"],
                },
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.memory.sync",
                    "--workspace-root",
                    str(root),
                    "--run-dir",
                    str(run_dir),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((run_dir / "memory_sync_report.json").exists())
            self.assertIn("memory_sync_report", completed.stdout)

    def test_sync_run_only_ingests_target_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / ".local" / "data" / "runs" / "continuous-alpha"
            target = runs_root / "target-run"
            sibling = runs_root / "sibling-run"
            target.mkdir(parents=True)
            sibling.mkdir(parents=True)
            self._write_json(
                target / "daily_budget_ledger.json",
                {
                    "daily_run_tag": "target-run",
                    "date": "2026-07-04",
                    "spent_simulations": 1,
                    "current_stage": "direction_probe_complete",
                    "stage_order": ["direction_probe"],
                },
            )
            self._write_json(
                target / "direction_probe_results.json",
                [{"alpha_id": "TARGET", "expression": "rank(cashflow)", "fitness": 1.1, "status": "pass"}],
            )
            self._write_json(
                sibling / "daily_budget_ledger.json",
                {
                    "daily_run_tag": "sibling-run",
                    "date": "2026-07-04",
                    "spent_simulations": 1,
                    "current_stage": "direction_probe_complete",
                    "stage_order": ["direction_probe"],
                },
            )
            self._write_json(
                sibling / "direction_probe_results.json",
                [{"alpha_id": "SIBLING", "expression": "rank(close)", "fitness": 0.1, "status": "holdout"}],
            )

            sync_run_memory(root, target)

            from wqb_agent_lab.memory.core.store import SQLiteMemoryStore

            store = SQLiteMemoryStore(root / ".local" / "data" / "memory" / "alpha_memory.db")
            titles = {node.title for node in store.list_nodes()}
            self.assertIn("TARGET", titles)
            self.assertNotIn("SIBLING", titles)

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
