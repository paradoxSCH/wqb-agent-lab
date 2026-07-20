from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wqb_agent_lab.runtime import (
    RunManifest,
    SensitiveManifestValueError,
    artifact_provenance,
)


class RunManifestTests(unittest.TestCase):
    def test_manifest_is_immutable_and_has_stable_digest(self) -> None:
        code = {"git_commit": "abc123", "metadata": {"dirty": False}}
        manifest = RunManifest.create(
            run_id="run-001",
            created_at="2026-07-20T12:00:00Z",
            code=code,
            runtime={"python": "3.12", "lock_digest": "lock123"},
            configuration={"workflow_digest": "workflow123"},
            llm={"provider": "openai_compatible", "model": "research-model"},
            research={"operator_catalog_digest": "catalog123"},
        )
        digest = manifest.digest()
        code["metadata"]["dirty"] = True

        self.assertFalse(manifest.to_dict()["code"]["metadata"]["dirty"])
        self.assertEqual(digest, manifest.digest())
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_artifact_provenance_is_workspace_relative_and_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_path = root / "runs" / "run-001" / "proposal.json"
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_text(json.dumps({"plan_id": "plan-001"}), encoding="utf-8")

            artifact = artifact_provenance(
                root,
                artifact_path,
                kind="plan_proposal",
                schema_name="plan_proposal",
                producer="planning-stage",
            )
            manifest = RunManifest.create(
                run_id="run-001",
                created_at="2026-07-20T12:00:00Z",
            ).with_artifact(artifact)

            self.assertEqual("runs/run-001/proposal.json", artifact.path)
            self.assertEqual(artifact, manifest.artifacts[0])
            self.assertRegex(artifact.sha256, r"^[0-9a-f]{64}$")
            self.assertRegex(artifact.schema_digest, r"^[0-9a-f]{64}$")

    def test_duplicate_artifact_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "artifact.json"
            path.write_text("{}", encoding="utf-8")
            artifact = artifact_provenance(root, path, kind="test")
            manifest = RunManifest.create(
                run_id="run-duplicate",
                created_at="2026-07-20T12:00:00Z",
            ).with_artifact(artifact)

            with self.assertRaisesRegex(ValueError, "already exists"):
                manifest.with_artifact(artifact)

    def test_artifact_outside_workspace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / "artifact.json"
            path.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "inside the workspace"):
                artifact_provenance(Path(workspace), path, kind="test")

    def test_sensitive_metadata_keys_are_rejected_at_any_depth(self) -> None:
        with self.assertRaisesRegex(SensitiveManifestValueError, "api_key"):
            RunManifest.create(
                run_id="run-sensitive",
                created_at="2026-07-20T12:00:00Z",
                llm={"provider": "example", "nested": {"api_key": "must-not-persist"}},
            )


if __name__ == "__main__":
    unittest.main()
