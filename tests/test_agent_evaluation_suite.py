from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.agent_evaluation import build_ablation_suite, select_ablation_candidates


class AgentEvaluationSuiteTests(unittest.TestCase):
    def test_build_ablation_suite_marks_same_budget_same_date_as_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            variant_paths = {}
            for name in ("baseline", "behavioral_proxy_only", "memory_only", "full_agent"):
                run_dir = root / name
                run_dir.mkdir()
                self._write_json(
                    run_dir / "daily_budget_ledger.json",
                    {
                        "daily_run_tag": name,
                        "date": "2026-07-04",
                        "daily_budget": 1000,
                        "spent_simulations": 1000,
                        "closed_loop": {"counts": {"submit_ready": 1, "low_value": 600}},
                    },
                )
                if name in {"memory_only", "full_agent"}:
                    self._write_json(run_dir / "memory_sync_report.json", {"nodes_written": 3})
                if name == "full_agent":
                    self._write_json(run_dir / "decision_attribution.json", [{"outcome": {"simulations_spent": 1000}}])
                variant_paths[name] = run_dir

            suite = build_ablation_suite(variant_paths)

            self.assertEqual(suite["fairness"]["comparison_type"], "controlled")
            self.assertEqual(suite["fairness"]["missing_variants"], [])
            self.assertEqual(set(suite["variants"]), {"baseline", "behavioral_proxy_only", "memory_only", "full_agent"})
            self.assertEqual(suite["report"]["verdict"], "inconclusive")

    def test_build_ablation_suite_marks_mixed_history_as_observational(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            full = root / "full_agent"
            baseline.mkdir()
            full.mkdir()
            self._write_json(
                baseline / "daily_budget_ledger.json",
                {"daily_run_tag": "baseline", "date": "2026-05-01", "daily_budget": 500, "spent_simulations": 500},
            )
            self._write_json(
                full / "daily_budget_ledger.json",
                {"daily_run_tag": "full_agent", "date": "2026-07-04", "daily_budget": 1000, "spent_simulations": 1000},
            )
            self._write_json(full / "decision_attribution.json", [{"outcome": {"simulations_spent": 1000}}])

            suite = build_ablation_suite({"baseline": baseline, "full_agent": full})

            self.assertEqual(suite["fairness"]["comparison_type"], "observational")
            self.assertEqual(suite["fairness"]["missing_variants"], ["behavioral_proxy_only", "memory_only"])
            self.assertIn("different date", " ".join(suite["fairness"]["warnings"]))

    def test_select_ablation_candidates_uses_conservative_local_heuristics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp)
            self._make_run(runs_root / "plain-baseline-20260701", date="2026-07-01", behavioral=False)
            self._make_run(runs_root / "behavioral-proxy-20260702", date="2026-07-02", behavioral=True)
            self._make_run(runs_root / "memory-only-20260703", date="2026-07-03", behavioral=False, memory=True)
            self._make_run(runs_root / "full-agent-20260704", date="2026-07-04", behavioral=True, memory=True, decision=True)

            candidates = select_ablation_candidates(runs_root)

            self.assertEqual(candidates["baseline"].name, "plain-baseline-20260701")
            self.assertEqual(candidates["behavioral_proxy_only"].name, "behavioral-proxy-20260702")
            self.assertEqual(candidates["memory_only"].name, "memory-only-20260703")
            self.assertEqual(candidates["full_agent"].name, "full-agent-20260704")

    def test_select_ablation_candidates_prefers_complete_runs_and_memory_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp)
            self._make_run(runs_root / "plain-baseline-20260701", date="2026-07-01", behavioral=False)
            self._make_run(runs_root / "behavioral-proxy-partial-20260703", date="2026-07-03", behavioral=True, simulations=120)
            self._make_run(runs_root / "behavioral-proxy-complete-20260702", date="2026-07-02", behavioral=True, simulations=1000)
            self._make_run(runs_root / "deepseek-replay-target-20260703", date="2026-07-03", behavioral=False, simulations=1000, snapshot=True)
            self._make_run(runs_root / "full-agent-20260704", date="2026-07-04", behavioral=True, memory=True, decision=True)

            candidates = select_ablation_candidates(runs_root)

            self.assertEqual(candidates["behavioral_proxy_only"].name, "behavioral-proxy-complete-20260702")
            self.assertEqual(candidates["memory_only"].name, "deepseek-replay-target-20260703")

    def test_select_ablation_candidates_uses_daily_budget_baseline_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp)
            self._make_run(runs_root / "kimi-daily-budget-20260701", date="2026-07-01", behavioral=True)
            self._make_run(runs_root / "full-agent-20260704", date="2026-07-04", behavioral=True, memory=True, decision=True)

            candidates = select_ablation_candidates(runs_root)

            self.assertEqual(candidates["baseline"].name, "kimi-daily-budget-20260701")

    def test_cli_auto_suite_writes_suite_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / "runs"
            output_dir = root / "eval"
            self._make_run(runs_root / "plain-baseline-20260701", date="2026-07-01", behavioral=False)
            self._make_run(runs_root / "behavioral-proxy-20260702", date="2026-07-02", behavioral=True)
            self._make_run(runs_root / "memory-only-20260703", date="2026-07-03", behavioral=False, memory=True)
            self._make_run(runs_root / "full-agent-20260704", date="2026-07-04", behavioral=True, memory=True, decision=True)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.evaluation.agent_ablation",
                    "--auto-runs-root",
                    str(runs_root),
                    "--suite-output-dir",
                    str(output_dir),
                    "--allow-observational",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((output_dir / "ablation_suite.json").exists())
            self.assertTrue((output_dir / "ablation_report.json").exists())
            self.assertTrue((output_dir / "summary.md").exists())
            self.assertIn("comparison_type=observational", completed.stdout)

    def _make_run(
        self,
        run_dir: Path,
        *,
        date: str,
        behavioral: bool,
        memory: bool = False,
        decision: bool = False,
        simulations: int = 1000,
        snapshot: bool = False,
    ) -> None:
        run_dir.mkdir(parents=True)
        self._write_json(
            run_dir / "daily_budget_ledger.json",
            {
                "daily_run_tag": run_dir.name,
                "date": date,
                "daily_budget": 1000,
                "spent_simulations": simulations,
                "closed_loop": {"counts": {"submit_ready": 1, "low_value": 500}},
                "default_queued_scan_configs": ["behavioral_scan.json"] if behavioral else ["plain_scan.json"],
            },
        )
        if behavioral:
            self._write_json(run_dir / "scan_results_snapshot.json", [{"note": "behavioral thesis"}])
        if snapshot and not behavioral:
            self._write_json(run_dir / "scan_results_snapshot.json", [{"note": "historical replay target"}])
        if memory:
            self._write_json(run_dir / "memory_sync_report.json", {"nodes_written": 2})
        if decision:
            self._write_json(run_dir / "decision_attribution.json", [{"outcome": {"simulations_spent": 1000}}])

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
