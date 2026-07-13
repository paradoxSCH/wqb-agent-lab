from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.self_corr_repair import build_bucket_aware_next_scan_plan, build_self_corr_repair_plan, write_bucket_aware_next_scan_artifacts, write_self_corr_repair_artifacts


class SelfCorrRepairTests(unittest.TestCase):
    def test_plan_excludes_extreme_self_corr_from_repair_budget(self) -> None:
        rows = [
            self._row("NEAR", 0.715, "group_rank(rank(ts_delta(field_a, 20)) / 14 + rank(-ts_delta(close, 5)) / 20, industry)"),
            self._row("NOT_NEAR", 0.73, "group_rank(rank(ts_zscore(field_b, 60)) / 12 + rank(-ts_delta(close, 3)) / 16, subindustry)"),
            self._row("EXT", 0.94, "group_rank(rank(ts_zscore(field_c, 120)) / 12 + rank(-ts_delta(vwap, 5)) / 18, industry)"),
        ]

        plan = build_self_corr_repair_plan(rows)

        self.assertEqual(plan["bucket_counts"], {"mild": 1, "moderate": 1, "extreme": 1})
        self.assertEqual([item["alpha_id"] for item in plan["excluded_extreme"]], ["EXT"])
        candidate_base_ids = {item["base_alpha_id"] for item in plan["scan_config"]["candidates"]}
        self.assertEqual(candidate_base_ids, {"NEAR"})
        self.assertTrue(all("NOT_NEAR" not in str(item) for item in plan["scan_config"]["candidates"]))
        self.assertTrue(all("EXT" not in str(item) for item in plan["scan_config"]["candidates"]))

    def test_writer_outputs_review_and_scan_config(self) -> None:
        rows = [self._row("NEAR", 0.715, "group_rank(rank(field_b) / 12 + rank(-ts_delta(close, 3)) / 16, industry)")]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "sample-run"
            run_dir.mkdir(parents=True)
            (run_dir / "scan_results_snapshot.json").write_text(json.dumps(rows), encoding="utf-8")

            result = write_self_corr_repair_artifacts(root, run_dir)

            self.assertTrue(Path(result["review_path"]).exists())
            self.assertTrue(Path(result["scan_config_path"]).exists())
            config = json.loads(Path(result["scan_config_path"]).read_text(encoding="utf-8"))
            self.assertEqual(config["output"], str(run_dir / "self_corr_repair_results.json"))
            self.assertTrue(config["candidates"])
            self.assertEqual(config["candidates"][0]["settings"]["startDate"], "2019-01-01")
            self.assertEqual(config["candidates"][0]["settings"]["endDate"], "2023-12-31")

    def test_bucket_aware_next_scan_replaces_extreme_self_corr(self) -> None:
        rows = [
            self._row("NEAR", 0.715, "group_rank(rank(ts_delta(field_a, 20)) / 14 + rank(-ts_delta(close, 5)) / 20, industry)"),
            self._row("NOT_NEAR", 0.73, "group_rank(rank(ts_delta(field_b, 20)) / 14 + rank(-ts_delta(close, 5)) / 20, industry)"),
            self._row("EXT", 0.94, "group_rank(rank(ts_zscore(field_c, 120)) / 12 + rank(-ts_delta(vwap, 5)) / 18, industry)"),
        ]

        plan = build_bucket_aware_next_scan_plan(rows, target_count=3)

        lanes = {item["wqb_action_lane"] for item in plan["scan_config"]["candidates"]}
        self.assertIn("repair_probe", lanes)
        self.assertIn("replace_probe", lanes)
        self.assertEqual(sum(plan["lane_counts"].values()), len(plan["scan_config"]["candidates"]))
        replacement = [item for item in plan["scan_config"]["candidates"] if item["base_alpha_id"] == "EXT"]
        self.assertTrue(replacement)
        self.assertTrue(all(item["self_corr_bucket"] == "extreme" for item in replacement))
        self.assertTrue(all(item["recommended_action"] == "replace_overcrowded_signal" for item in replacement))
        not_near = [item for item in plan["scan_config"]["candidates"] if item["base_alpha_id"] == "NOT_NEAR"]
        self.assertTrue(not_near)
        self.assertTrue(all(item["wqb_action_lane"] == "replace_probe" for item in not_near))
        self.assertTrue(all(" moderate " in item["note"] for item in not_near))
        self.assertTrue(not_near[0]["axis"].startswith("bridge_"))
        self.assertTrue(not_near[0]["expression"].startswith("group_rank("))
        self.assertNotIn("rank(ts_zscore(group_rank(", not_near[0]["expression"])

    def test_bridge_replacement_prioritizes_replace_and_weaken_before_drop(self) -> None:
        rows = [
            self._row(
                "NOT_NEAR",
                0.73,
                "group_rank(rank(ts_delta(field_b, 20)) / 14 + rank(-ts_delta(close, 5)) / 20 - rank(volume / ts_mean(volume, 20)) / 66, industry)",
            ),
        ]

        plan = build_bucket_aware_next_scan_plan(rows, target_count=2)
        candidates = plan["scan_config"]["candidates"]

        self.assertTrue(candidates)
        self.assertEqual(
            [item["axis"] for item in candidates],
            [
                "bridge_replace_price_reversal_with_primary_confirmation",
                "bridge_weaken_price_reversal_leg",
            ],
        )
        self.assertTrue(all("rank(ts_delta(field_b, 20)) / 14" in item["expression"] for item in candidates))
        self.assertIn("ts_mean(rank(ts_delta(field_b, 20)), 20) / 30", candidates[0]["expression"])
        self.assertIn("rank(-ts_delta(close, 5)) / 30", candidates[1]["expression"])

    def test_bucket_aware_writer_outputs_mixed_small_budget_config(self) -> None:
        rows = [
            self._row("NEAR", 0.715, "group_rank(rank(ts_delta(field_a, 20)) / 14 + rank(-ts_delta(close, 5)) / 20, industry)"),
            self._row("EXT", 0.94, "group_rank(rank(ts_zscore(field_c, 120)) / 12 + rank(-ts_delta(vwap, 5)) / 18, industry)"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "sample-run"
            run_dir.mkdir(parents=True)
            (run_dir / "scan_results_snapshot.json").write_text(json.dumps(rows), encoding="utf-8")

            result = write_bucket_aware_next_scan_artifacts(root, run_dir, target_count=4)

            self.assertTrue(Path(result["scan_config_path"]).exists())
            config = json.loads(Path(result["scan_config_path"]).read_text(encoding="utf-8"))
            self.assertEqual(config["output"], str(run_dir / "bucket_aware_next_scan_results.json"))
            self.assertLessEqual(len(config["candidates"]), 4)
            self.assertIn("replace_probe", {item["wqb_action_lane"] for item in config["candidates"]})

    def test_cli_can_build_bucket_aware_next_scan(self) -> None:
        rows = [
            self._row("EXT", 0.94, "group_rank(rank(ts_zscore(field_c, 120)) / 12 + rank(-ts_delta(vwap, 5)) / 18, industry)"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "sample-run"
            run_dir.mkdir(parents=True)
            (run_dir / "scan_results_snapshot.json").write_text(json.dumps(rows), encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.build_self_corr_repair_scan",
                    "--workspace-root",
                    str(root),
                    "--run-dir",
                    str(run_dir),
                    "--bucket-aware-next",
                    "--target-count",
                    "2",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("scan_config_path=", completed.stdout)
            config_path = root / ".local" / "research" / "scans" / "continuous-alpha" / "bucket-aware-next-sample-run" / "scan_config_round1.json"
            self.assertTrue(config_path.exists())

    def _row(self, alpha_id: str, self_corr: float, expression: str) -> dict[str, object]:
        return {
            "alpha_id": alpha_id,
            "expression": expression,
            "settings": {"decay": 6, "neutralization": "MARKET", "startDate": "2019-01-01", "endDate": "2023-12-31"},
            "metrics": {"sharpe": 1.7, "fitness": 1.2, "turnover": 0.1},
            "checks": [{"name": "SELF_CORRELATION", "result": "FAIL", "value": self_corr, "limit": 0.7}],
            "family": "test_family",
        }


if __name__ == "__main__":
    unittest.main()
