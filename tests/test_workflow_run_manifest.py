from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from src.contracts import assert_valid_contract
from src.kimi_daily_workflow import KimiDailyWorkflow


class WorkflowRunManifestTests(unittest.TestCase):
    @staticmethod
    def _relative(path: Path, root: Path) -> str:
        return path.resolve().relative_to(root.resolve()).as_posix()

    def _workflow(self, root: Path, *, dry_run: bool = False) -> KimiDailyWorkflow:
        config_path = root / "workflow.json"
        config_path.write_text(
            json.dumps(
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {
                        "standard": {"daily_budget": 1, "stage_budgets": {}}
                    },
                    "stage_order": [],
                    "llm_provider": {
                        "provider": "disabled",
                        "output_contract": "plan_proposal",
                    },
                }
            ),
            encoding="utf-8",
        )
        return KimiDailyWorkflow(
            root,
            workflow_config=config_path,
            run_date=date(2026, 7, 20),
            dry_run=dry_run,
        )

    def test_run_once_checkpoints_a_safe_content_addressed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = self._workflow(root)
            now = datetime(2026, 7, 20, 9, 30)

            messages = workflow.run_once(now=now)
            payload = json.loads(workflow.manifest_path.read_text(encoding="utf-8"))

            assert_valid_contract("run_manifest", payload)
            self.assertIn("run manifest:", messages[-1])
            self.assertEqual(workflow.run_tag, payload["run_id"])
            self.assertEqual("checkpointed", payload["extensions"]["checkpoint_status"])
            self.assertEqual("plan_proposal", payload["llm"]["output_contract"])
            self.assertRegex(payload["research"]["schema_digests"]["plan_proposal"], r"^[0-9a-f]{64}$")
            self.assertRegex(
                payload["research"]["schema_digests"]["workflow_stage_result"],
                r"^[0-9a-f]{64}$",
            )
            self.assertNotIn("api_key", json.dumps(payload).lower())
            artifact_paths = {artifact["path"] for artifact in payload["artifacts"]}
            self.assertIn(self._relative(workflow.ledger_path, root), artifact_paths)
            self.assertIn(
                self._relative(workflow.stage_checkpoint_store.path_for("llm_planning"), root),
                artifact_paths,
            )
            self.assertNotIn(self._relative(workflow.manifest_path, root), artifact_paths)

    def test_checkpoint_refresh_preserves_creation_time_and_adds_new_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = self._workflow(root)
            workflow.run_once(now=datetime(2026, 7, 20, 9, 30))
            first = json.loads(workflow.manifest_path.read_text(encoding="utf-8"))
            extra = workflow.run_dir / "novel-research-output.txt"
            extra.write_text("unconstrained research idea", encoding="utf-8")
            workflow.config_dir.mkdir(parents=True)
            sliced_config = workflow.config_dir / "novel-family.json"
            sliced_config.write_text('{"mechanism": "novel"}', encoding="utf-8")

            workflow.write_run_manifest(
                now=datetime(2026, 7, 20, 10, 0),
                status="checkpointed",
            )
            second = json.loads(workflow.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(first["created_at"], second["created_at"])
            self.assertEqual("2026-07-20T10:00:00", second["extensions"]["checkpointed_at"])
            self.assertIn(
                self._relative(extra, root),
                {artifact["path"] for artifact in second["artifacts"]},
            )
            self.assertIn(
                self._relative(sliced_config, root),
                {artifact["path"] for artifact in second["artifacts"]},
            )

    def test_failed_tick_records_failure_without_masking_the_original_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = self._workflow(root)

            with patch.object(workflow, "_run_once_tick", side_effect=RuntimeError("original")):
                with self.assertRaisesRegex(RuntimeError, "original"):
                    workflow.run_once(now=datetime(2026, 7, 20, 11, 0))

            payload = json.loads(workflow.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("failed", payload["extensions"]["checkpoint_status"])
            self.assertEqual("RuntimeError", payload["extensions"]["error_type"])

    def test_dry_run_does_not_write_a_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = self._workflow(root, dry_run=True)

            workflow.run_once(now=datetime(2026, 7, 20, 9, 30))

            self.assertFalse(workflow.manifest_path.exists())


if __name__ == "__main__":
    unittest.main()
