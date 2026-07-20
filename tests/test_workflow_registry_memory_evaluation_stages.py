from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from wqb_agent_lab.workflow.engine import ResearchWorkflow
from wqb_agent_lab.workflow import StageResult


class WorkflowRegistryMemoryEvaluationStageTests(unittest.TestCase):
    def _workflow(self, root: Path, *, dry_run: bool = False) -> ResearchWorkflow:
        config = root / "workflow.json"
        config.write_text(
            json.dumps(
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {
                        "standard": {"daily_budget": 1, "stage_budgets": {}}
                    },
                    "stage_order": [],
                    "llm_provider": {"provider": "disabled"},
                    "submitted_registry_sync_enabled": False,
                    "post_stage_memory_sync": {
                        "enabled": True,
                        "db_path": ".local/data/memory/alpha_memory.db",
                    },
                }
            ),
            encoding="utf-8",
        )
        workflow = ResearchWorkflow(
            root,
            workflow_config=config,
            run_date=date(2026, 7, 21),
            execute_scans=False,
            dry_run=dry_run,
        )
        workflow.run_dir.mkdir(parents=True, exist_ok=True)
        (workflow.run_dir / "daily_budget_ledger.json").write_text(
            json.dumps(
                {
                    "daily_run_tag": workflow.run_tag,
                    "date": "2026-07-21",
                    "current_stage": "triage_complete",
                    "spent_simulations": 1,
                    "stage_order": [],
                }
            ),
            encoding="utf-8",
        )
        (workflow.run_dir / "direction_probe_results.json").write_text(
            json.dumps(
                [
                    {
                        "alpha_id": "A1",
                        "expression": "future_operator(custom_field)",
                        "mechanism": "novel attention mechanism",
                        "extensions": {"unbounded_research_note": "preserve"},
                        "metrics": {"sharpe": 1.1, "fitness": 0.8},
                    }
                ]
            ),
            encoding="utf-8",
        )
        (workflow.run_dir / "scan_results_snapshot.json").write_text(
            (workflow.run_dir / "direction_probe_results.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return workflow

    def test_registry_stage_checkpoints_snapshot_and_read_only_refresh_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow = self._workflow(Path(tmp))

            status = workflow.run_registry_stage(now=datetime(2026, 7, 21, 8, 0))

            self.assertEqual("skipped_disabled", status)
            checkpoint = workflow.stage_checkpoint_store.load("registry")
            assert checkpoint is not None
            self.assertEqual("completed", checkpoint.status)
            self.assertEqual("skipped_disabled", checkpoint.output["status"])
            self.assertFalse(checkpoint.extensions["remote_side_effects"])
            self.assertTrue(checkpoint.extensions["refresh_is_read_only"])

    def test_registry_snapshot_is_frozen_for_tick_and_ambiguous_jobs_block_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = self._workflow(root)
            registry_path = root / ".local/data/registry/submitted_alphas.json"
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text(
                json.dumps({"submitted": [{"alpha_id": "A1", "expression": "rank(a)"}]}),
                encoding="utf-8",
            )
            other_run = root / ".local/data/runs/continuous-alpha/other-run"
            other_run.mkdir(parents=True, exist_ok=True)
            (other_run / "submission_state.json").write_text(
                json.dumps(
                    {
                        "jobs": [
                            {"alpha_id": "A-UNKNOWN", "status": "submission_unknown_commit"},
                            {"alpha_id": "A-REJECTED", "status": "rejected"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            workflow.run_registry_stage(now=datetime(2026, 7, 21, 8, 0))
            registry_path.write_text(
                json.dumps({"submitted": [{"alpha_id": "A2", "expression": "rank(b)"}]}),
                encoding="utf-8",
            )
            frozen_ids, frozen_expressions = workflow._submitted_registry()

            self.assertEqual({"A1", "A-UNKNOWN"}, frozen_ids)
            self.assertEqual({"rank(a)"}, frozen_expressions)
            self.assertNotIn("A-REJECTED", frozen_ids)

            workflow.run_registry_stage(now=datetime(2026, 7, 21, 8, 1))
            refreshed_ids, refreshed_expressions = workflow._submitted_registry()
            self.assertEqual({"A2", "A-UNKNOWN"}, refreshed_ids)
            self.assertEqual({"rank(b)"}, refreshed_expressions)

    def test_memory_completes_before_evaluation_and_preserves_open_research_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow = self._workflow(Path(tmp))
            now = datetime(2026, 7, 21, 9, 30)

            memory_report = workflow.run_memory_stage(now=now)
            evaluation_report, _summary = workflow.run_evaluation_stage(now=now)

            assert memory_report is not None
            self.assertTrue(memory_report.is_file())
            evaluation = json.loads(evaluation_report.read_text(encoding="utf-8"))
            self.assertEqual("2026-07-21T09:30:00", evaluation["generated_at"])
            self.assertIn(
                "memory_sync_report.json",
                {record["artifact"] for record in evaluation["records"]},
            )
            source = json.loads(
                (workflow.run_dir / "direction_probe_results.json").read_text(encoding="utf-8")
            )
            self.assertEqual("novel attention mechanism", source[0]["mechanism"])
            self.assertEqual("preserve", source[0]["extensions"]["unbounded_research_note"])
            memory_checkpoint = workflow.stage_checkpoint_store.load("memory")
            evaluation_checkpoint = workflow.stage_checkpoint_store.load("evaluation")
            assert memory_checkpoint is not None and evaluation_checkpoint is not None
            self.assertTrue(memory_checkpoint.extensions["sqlite_upserts_are_idempotent"])
            self.assertTrue(evaluation_checkpoint.extensions["consumes_completed_memory_stage"])
            self.assertTrue(evaluation_checkpoint.extensions["policy_actions_are_observations"])

    def test_interrupted_memory_stage_resumes_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow = self._workflow(Path(tmp))
            workflow.stage_checkpoint_store.write(
                StageResult.create(
                    run_id=workflow.run_tag,
                    stage_id="memory",
                    attempt_id="interrupted-memory",
                    attempt_number=1,
                    status="running",
                    started_at="2026-07-21T09:00:00",
                    completed_at=None,
                )
            )

            report = workflow.run_memory_stage(now=datetime(2026, 7, 21, 9, 1))

            self.assertIsNotNone(report)
            checkpoint = workflow.stage_checkpoint_store.load("memory")
            assert checkpoint is not None
            self.assertEqual(2, checkpoint.attempt_number)
            self.assertEqual("interrupted-memory", checkpoint.extensions["resumed_from_attempt_id"])

    def test_memory_digest_changes_with_causal_result_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow = self._workflow(Path(tmp))
            workflow.run_memory_stage(now=datetime(2026, 7, 21, 10, 0))
            first = workflow.stage_checkpoint_store.load("memory")
            assert first is not None
            result_path = workflow.run_dir / "direction_probe_results.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            payload[0]["extensions"]["unbounded_research_note"] = "new evidence"
            result_path.write_text(json.dumps(payload), encoding="utf-8")

            workflow.run_memory_stage(now=datetime(2026, 7, 21, 10, 1))
            second = workflow.stage_checkpoint_store.load("memory")
            assert second is not None

            self.assertNotEqual(first.input_digest, second.input_digest)

    def test_dry_run_writes_no_registry_memory_or_evaluation_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow = self._workflow(Path(tmp), dry_run=True)

            self.assertEqual("skipped_dry_run", workflow.run_registry_stage())
            self.assertIsNone(workflow.run_memory_stage())
            workflow.run_evaluation_stage()

            for stage_id in ("registry", "memory", "evaluation"):
                self.assertFalse(workflow.stage_checkpoint_store.path_for(stage_id).exists())
            self.assertFalse((workflow.run_dir / "memory_sync_report.json").exists())
            self.assertFalse((workflow.run_dir / "output_evaluation_report.json").exists())


if __name__ == "__main__":
    unittest.main()
