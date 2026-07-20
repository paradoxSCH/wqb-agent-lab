from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from src.kimi_daily_workflow import KimiDailyWorkflow
from wqb_agent_lab.workflow import StageResult


class WorkflowScanPreflightStageTests(unittest.TestCase):
    def _workflow(self, root: Path, *, dry_run: bool = False) -> tuple[KimiDailyWorkflow, Path]:
        source = root / "configs" / "novel-candidates.json"
        source.parent.mkdir(parents=True)
        source.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "candidate_id": "novel-1",
                            "expression": "future_operator(vector_neutralize(x, custom_group))",
                            "mechanism": "unknown cross-cohort attention migration",
                        },
                        {
                            "candidate_id": "novel-2",
                            "expression": "rank(custom_proxy_field)",
                            "mechanism": "unregistered proxy discovery",
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
        return (
            KimiDailyWorkflow(
                root,
                workflow_config=workflow_config,
                run_date=date(2026, 7, 20),
                execute_scans=True,
                dry_run=dry_run,
            ),
            source,
        )

    def test_preflight_preserves_novel_candidates_without_running_simulation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow, _source = self._workflow(root)
            ledger = workflow.load_or_create_ledger()

            plan, initial_action = workflow.run_scan_preflight(
                ledger,
                now=datetime(2026, 7, 20, 9, 0),
            )

            self.assertEqual("slice_scan_config", initial_action)
            self.assertEqual("prepared_scan_config", plan.action)
            self.assertEqual(2, plan.candidate_count)
            assert plan.sliced_config is not None
            sliced = json.loads(plan.sliced_config.read_text(encoding="utf-8"))
            self.assertEqual(
                "future_operator(vector_neutralize(x, custom_group))",
                sliced["candidates"][0]["expression"],
            )
            self.assertFalse(plan.output_path.exists() if plan.output_path is not None else True)
            checkpoint = workflow.stage_checkpoint_store.load("scan_preflight")
            assert checkpoint is not None
            self.assertEqual("completed", checkpoint.status)
            self.assertFalse(checkpoint.extensions["remote_side_effects"])
            self.assertIn(plan.sliced_config.resolve().relative_to(root.resolve()).as_posix(), checkpoint.artifacts)

    def test_checkpointed_preflight_matches_existing_direct_methods(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow, _source = self._workflow(root)
            ledger = workflow.load_or_create_ledger()
            direct = workflow.prepare_budgeted_scan(workflow.plan_next_scan(ledger))
            assert direct.sliced_config is not None
            direct_payload = json.loads(direct.sliced_config.read_text(encoding="utf-8"))

            checkpointed, initial_action = workflow.run_scan_preflight(
                ledger,
                now=datetime(2026, 7, 20, 9, 0),
            )
            assert checkpointed.sliced_config is not None
            checkpointed_payload = json.loads(checkpointed.sliced_config.read_text(encoding="utf-8"))

            self.assertEqual("slice_scan_config", initial_action)
            self.assertEqual(direct.action, checkpointed.action)
            self.assertEqual(direct.candidate_count, checkpointed.candidate_count)
            self.assertEqual(direct_payload, checkpointed_payload)

    def test_missing_scan_config_is_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow, source = self._workflow(root)
            source.unlink()
            ledger = workflow.load_or_create_ledger()

            plan, initial_action = workflow.run_scan_preflight(
                ledger,
                now=datetime(2026, 7, 20, 9, 0),
            )

            self.assertEqual("waiting_for_scan_config", initial_action)
            self.assertEqual("waiting_for_scan_config", plan.action)
            checkpoint = workflow.stage_checkpoint_store.load("scan_preflight")
            assert checkpoint is not None
            self.assertEqual("deferred", checkpoint.status)

    def test_interrupted_preflight_is_recomputed_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow, _source = self._workflow(root)
            ledger = workflow.load_or_create_ledger()
            workflow.stage_checkpoint_store.write(
                StageResult.create(
                    run_id=workflow.run_tag,
                    stage_id="scan_preflight",
                    attempt_id="interrupted-preflight",
                    attempt_number=3,
                    status="running",
                    started_at="2026-07-20T08:59:00",
                    completed_at=None,
                )
            )

            plan, _initial_action = workflow.run_scan_preflight(
                ledger,
                now=datetime(2026, 7, 20, 9, 0),
            )

            self.assertEqual("prepared_scan_config", plan.action)
            checkpoint = workflow.stage_checkpoint_store.load("scan_preflight")
            assert checkpoint is not None
            self.assertEqual(4, checkpoint.attempt_number)
            self.assertEqual("interrupted-preflight", checkpoint.extensions["resumed_from_attempt_id"])

    def test_source_change_changes_preflight_input_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow, source = self._workflow(root)
            ledger = workflow.load_or_create_ledger()
            workflow.run_scan_preflight(ledger, now=datetime(2026, 7, 20, 9, 0))
            first = workflow.stage_checkpoint_store.load("scan_preflight")
            assert first is not None
            payload = json.loads(source.read_text(encoding="utf-8"))
            payload["extensions"] = {"new_research_context": "preserve me"}
            source.write_text(json.dumps(payload), encoding="utf-8")

            workflow.run_scan_preflight(ledger, now=datetime(2026, 7, 20, 9, 1))
            second = workflow.stage_checkpoint_store.load("scan_preflight")
            assert second is not None

            self.assertNotEqual(first.input_digest, second.input_digest)

    def test_unchanged_inputs_keep_a_stable_preflight_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow, _source = self._workflow(root)
            ledger = workflow.load_or_create_ledger()
            workflow.run_scan_preflight(ledger, now=datetime(2026, 7, 20, 9, 0))
            first = workflow.stage_checkpoint_store.load("scan_preflight")
            assert first is not None
            ledger["last_agent_callback_event"] = "observability-only-change"

            workflow.run_scan_preflight(ledger, now=datetime(2026, 7, 20, 9, 1))
            second = workflow.stage_checkpoint_store.load("scan_preflight")
            assert second is not None

            self.assertEqual(first.input_digest, second.input_digest)

    def test_dry_run_does_not_write_preflight_checkpoint_or_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow, _source = self._workflow(root, dry_run=True)

            plan, initial_action = workflow.run_scan_preflight(
                workflow.load_or_create_ledger(),
                now=datetime(2026, 7, 20, 9, 0),
            )

            self.assertEqual("slice_scan_config", initial_action)
            self.assertEqual("prepared_scan_config", plan.action)
            self.assertFalse(workflow.stage_checkpoint_store.path_for("scan_preflight").exists())
            self.assertFalse(plan.sliced_config.exists() if plan.sliced_config is not None else True)


if __name__ == "__main__":
    unittest.main()
