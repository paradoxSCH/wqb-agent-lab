from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from wqb_agent_lab.workflow.engine import ResearchWorkflow
from wqb_agent_lab.workflow import StageResult


class WorkflowDiagnosisTriageStageTests(unittest.TestCase):
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
        (workflow.run_dir / "direction_probe_results.json").write_text(
            json.dumps(
                [
                    {
                        "alpha_id": "A-NOVEL",
                        "expression": "future_operator(vector_neutralize(custom_field, custom_group))",
                        "settings": {"region": "USA", "novelSetting": "OPEN"},
                        "mechanism": "unknown cross-cohort attention migration",
                        "extensions": {"new_evidence_shape": {"confidence": 0.63}},
                        "metrics": {"sharpe": 1.1, "fitness": 0.7, "turnover": 0.2},
                        "checks": [
                            {"name": "LOW_FITNESS", "result": "FAIL", "limit": 1.0, "value": 0.7}
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
        return workflow

    def test_stages_preserve_novel_fields_and_checkpoint_advisory_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = self._workflow(root)
            ledger = workflow.load_or_create_ledger()

            state = workflow.run_diagnosis_triage(
                ledger,
                now=datetime(2026, 7, 21, 9, 0),
            )

            diagnosed = json.loads((workflow.run_dir / "diagnosis_results.json").read_text(encoding="utf-8"))
            snapshot = json.loads((workflow.run_dir / "scan_results_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual("unknown cross-cohort attention migration", diagnosed[0]["mechanism"])
            self.assertEqual("OPEN", diagnosed[0]["settings"]["novelSetting"])
            self.assertEqual(0.63, diagnosed[0]["extensions"]["new_evidence_shape"]["confidence"])
            self.assertEqual("low_value", snapshot[0]["triage_bucket"])
            self.assertEqual(1, state["counts"]["low_value"])

            diagnosis = workflow.stage_checkpoint_store.load("diagnosis")
            triage = workflow.stage_checkpoint_store.load("triage")
            assert diagnosis is not None and triage is not None
            self.assertEqual("completed", diagnosis.status)
            self.assertFalse(diagnosis.extensions["remote_side_effects"])
            self.assertTrue(diagnosis.extensions["preserves_open_candidate_fields"])
            self.assertEqual("completed", triage.status)
            self.assertTrue(triage.extensions["research_routes_are_advisory"])
            self.assertIn("rewrite_weak_signal_chassis", triage.output["route_decisions"])

    def test_staged_path_matches_legacy_closed_loop_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            direct_workflow = self._workflow(Path(first_tmp))
            staged_workflow = self._workflow(Path(second_tmp))
            direct_ledger = direct_workflow.load_or_create_ledger()
            staged_ledger = staged_workflow.load_or_create_ledger()
            now = datetime(2026, 7, 21, 10, 0)

            direct = direct_workflow.write_closed_loop_artifacts(direct_ledger, now=now)
            staged = staged_workflow.run_diagnosis_triage(staged_ledger, now=now)

            self.assertEqual(direct, staged)
            for artifact in (
                "scan_results_snapshot.json",
                "optimize_next.json",
                "low_value_avoid.json",
                "family_efficiency.json",
                "diagnosis_policy_evaluation.json",
                "iteration_state.json",
            ):
                self.assertEqual(
                    json.loads((direct_workflow.run_dir / artifact).read_text(encoding="utf-8")),
                    json.loads((staged_workflow.run_dir / artifact).read_text(encoding="utf-8")),
                )

    def test_interrupted_safe_diagnosis_resumes_with_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = self._workflow(root)
            workflow.stage_checkpoint_store.write(
                StageResult.create(
                    run_id=workflow.run_tag,
                    stage_id="diagnosis",
                    attempt_id="interrupted-diagnosis",
                    attempt_number=1,
                    status="running",
                    started_at="2026-07-21T08:00:00",
                    completed_at=None,
                )
            )

            rows = workflow.run_diagnosis_stage(now=datetime(2026, 7, 21, 8, 1))

            self.assertEqual(1, len(rows))
            checkpoint = workflow.stage_checkpoint_store.load("diagnosis")
            assert checkpoint is not None
            self.assertEqual(2, checkpoint.attempt_number)
            self.assertEqual("interrupted-diagnosis", checkpoint.extensions["resumed_from_attempt_id"])

    def test_diagnosis_digest_changes_with_scan_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = self._workflow(root)
            workflow.run_diagnosis_stage(now=datetime(2026, 7, 21, 8, 0))
            first = workflow.stage_checkpoint_store.load("diagnosis")
            assert first is not None
            result_path = workflow.run_dir / "direction_probe_results.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            payload[0]["extensions"]["new_evidence_shape"]["confidence"] = 0.91
            result_path.write_text(json.dumps(payload), encoding="utf-8")

            workflow.run_diagnosis_stage(now=datetime(2026, 7, 21, 8, 2))
            second = workflow.stage_checkpoint_store.load("diagnosis")
            assert second is not None

            self.assertNotEqual(first.input_digest, second.input_digest)

    def test_dry_run_creates_no_stage_or_artifact_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = self._workflow(root, dry_run=True)
            ledger = workflow.load_or_create_ledger()

            state = workflow.run_diagnosis_triage(ledger, now=datetime(2026, 7, 21, 9, 0))

            self.assertEqual(1, state["counts"]["scan_rows"])
            self.assertFalse(workflow.stage_checkpoint_store.path_for("diagnosis").exists())
            self.assertFalse(workflow.stage_checkpoint_store.path_for("triage").exists())
            self.assertFalse((workflow.run_dir / "diagnosis_results.json").exists())
            self.assertFalse((workflow.run_dir / "scan_results_snapshot.json").exists())


if __name__ == "__main__":
    unittest.main()
