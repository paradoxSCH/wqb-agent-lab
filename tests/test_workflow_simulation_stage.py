from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from wqb_agent_lab.workflow.engine import ResearchWorkflow, StagePlan
from wqb_agent_lab.workflow import StageResult


class WorkflowSimulationStageTests(unittest.TestCase):
    def _prepared(self, root: Path) -> tuple[ResearchWorkflow, dict, StagePlan, dict]:
        source = root / "configs" / "novel-candidates.json"
        source.parent.mkdir(parents=True)
        source.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "expression": "future_operator(custom_field)",
                            "settings": {"region": "USA", "delay": 1, "novelSetting": "OPEN"},
                            "mechanism": "unknown cohort migration",
                        },
                        {
                            "expression": "rank(second_custom_field)",
                            "settings": {"region": "USA", "delay": 1},
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        workflow_config = root / "workflow.json"
        workflow_config.write_text(
            json.dumps(
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {
                        "standard": {
                            "daily_budget": 2,
                            "stage_budgets": {"direction_probe": 2},
                        }
                    },
                    "stage_order": ["direction_probe"],
                    "default_queued_scan_configs": [source.relative_to(root).as_posix()],
                    "llm_provider": {"provider": "disabled"},
                }
            ),
            encoding="utf-8",
        )
        workflow = ResearchWorkflow(
            root,
            workflow_config=workflow_config,
            run_date=date(2026, 7, 20),
            execute_scans=True,
        )
        ledger = workflow.load_or_create_ledger()
        plan, _action = workflow.run_scan_preflight(ledger)
        assert plan.sliced_config is not None
        sliced = json.loads(plan.sliced_config.read_text(encoding="utf-8"))
        return workflow, ledger, plan, sliced["candidates"][0]

    def _interrupt_stage(self, workflow: ResearchWorkflow) -> None:
        workflow.stage_checkpoint_store.write(
            StageResult.create(
                run_id=workflow.run_tag,
                stage_id="simulation",
                attempt_id="crashed-after-post",
                attempt_number=1,
                status="running",
                started_at="2026-07-20T12:00:00",
                completed_at=None,
            )
        )

    def test_unresolved_interrupted_post_blocks_new_scan_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow, ledger, plan, candidate = self._prepared(root)
            self._interrupt_stage(workflow)
            operation = workflow.operation_journal.begin(
                "simulation.create",
                {
                    "type": "REGULAR",
                    "settings": candidate.get("settings") or {},
                    "regular": candidate["expression"],
                },
                run_id=workflow.run_tag,
            )
            workflow.operation_journal.finish(
                operation.operation_id,
                "unknown_commit",
                reason="read_timeout_after_send",
            )
            commands: list[list[str]] = []

            def unresolved(command, **_kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 3)

            with patch("wqb_agent_lab.workflow.engine.subprocess.run", side_effect=unresolved):
                spent = workflow.execute_scan(plan, ledger)

            self.assertEqual(0, spent)
            self.assertEqual(1, len(commands))
            self.assertIn("--reconcile-only", commands[0])
            checkpoint = workflow.stage_checkpoint_store.load("simulation")
            assert checkpoint is not None
            self.assertEqual("running", checkpoint.status)
            report = json.loads((workflow.run_dir / "simulation_reconciliation.json").read_text(encoding="utf-8"))
            self.assertEqual("blocked", report["status"])

    def test_positive_reconciliation_allows_resume_without_reposting_recovered_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow, ledger, plan, candidate = self._prepared(root)
            self._interrupt_stage(workflow)
            operation = workflow.operation_journal.begin(
                "simulation.create",
                {
                    "type": "REGULAR",
                    "settings": candidate.get("settings") or {},
                    "regular": candidate["expression"],
                },
                run_id=workflow.run_tag,
            )
            workflow.operation_journal.finish(
                operation.operation_id,
                "unknown_commit",
                reason="connection_lost_after_possible_send",
            )
            commands: list[list[str]] = []

            def reconciled(command, **_kwargs):
                commands.append(command)
                if "--reconcile-only" in command:
                    assert plan.output_path is not None
                    plan.output_path.parent.mkdir(parents=True, exist_ok=True)
                    plan.output_path.write_text(
                        json.dumps(
                            [
                                {
                                    "alpha_id": "A-RECOVERED",
                                    "expression": candidate["expression"],
                                    "settings": candidate.get("settings") or {},
                                    "metrics": {"sharpe": 1.4, "fitness": 1.1, "turnover": 0.2},
                                    "checks": [],
                                    "reconciliation": {"status": "recovered"},
                                }
                            ]
                        ),
                        encoding="utf-8",
                    )
                    workflow.operation_journal.finish(
                        operation.operation_id,
                        "accepted",
                        reason="reconciled_alpha_match",
                        remote_ref="/alphas/A-RECOVERED",
                    )
                return subprocess.CompletedProcess(command, 0)

            with patch("wqb_agent_lab.workflow.engine.subprocess.run", side_effect=reconciled):
                spent = workflow.execute_scan(plan, ledger)

            self.assertEqual(1, spent)
            self.assertEqual(2, len(commands))
            self.assertIn("--reconcile-only", commands[0])
            self.assertNotIn("--reconcile-only", commands[1])
            checkpoint = workflow.stage_checkpoint_store.load("simulation")
            assert checkpoint is not None
            self.assertEqual("completed", checkpoint.status)
            self.assertEqual(2, checkpoint.attempt_number)
            self.assertTrue(checkpoint.extensions["reconciled_interrupted_attempt"])
            self.assertEqual((), checkpoint.output["unresolved_operation_ids"])


if __name__ == "__main__":
    unittest.main()
