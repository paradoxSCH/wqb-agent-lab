from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wqb_agent_lab.runtime import (
    RunManifest,
    SensitiveManifestValueError,
    artifact_provenance,
    collect_artifact_provenance,
)


class RunManifestTests(unittest.TestCase):
    @staticmethod
    def _plan_proposal() -> dict[str, object]:
        return {
            "schema_version": 1,
            "plan_id": "plan-001",
            "objective": "Explore an unconstrained research idea.",
            "hypotheses": [],
            "requested_actions": [],
            "alternatives": [],
            "freeform_notes": "Preserve unknown future mechanisms.",
            "extensions": {"unknown_llm_field": {"confidence": 0.7}},
        }

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
            artifact_path.write_text(json.dumps(self._plan_proposal()), encoding="utf-8")

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

            with self.assertRaisesRegex(ValueError, "already exists"):
                RunManifest.create(
                    run_id="run-batch-duplicate",
                    created_at="2026-07-20T12:00:00Z",
                ).with_artifacts((artifact, artifact))

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

    def test_collect_artifacts_is_stable_and_can_exclude_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run-001"
            run_dir.mkdir(parents=True)
            manifest_path = run_dir / "run_manifest.json"
            manifest_path.write_text("{}", encoding="utf-8")
            (run_dir / "z.log").write_text("last", encoding="utf-8")
            (run_dir / "a.json").write_text("{}", encoding="utf-8")

            artifacts = collect_artifact_provenance(
                root,
                run_dir,
                exclude=(manifest_path,),
                producer="test-workflow",
            )

            self.assertEqual(
                ["runs/run-001/a.json", "runs/run-001/z.log"],
                [artifact.path for artifact in artifacts],
            )
            self.assertEqual("application/json", artifacts[0].kind)
            self.assertEqual("text/plain", artifacts[1].kind)
            self.assertTrue(all(artifact.producer == "test-workflow" for artifact in artifacts))

    def test_schema_declared_artifact_is_validated_before_manifesting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_path = root / "invalid-proposal.json"
            artifact_path.write_text('{"plan_id": "missing-required-fields"}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "plan_proposal contract validation failed"):
                artifact_provenance(
                    root,
                    artifact_path,
                    kind="plan_proposal",
                    schema_name="plan_proposal",
                )

    def test_manifest_consumer_revalidates_digests_and_artifact_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_path = root / "proposal.json"
            artifact_path.write_text(json.dumps(self._plan_proposal()), encoding="utf-8")
            artifact = artifact_provenance(
                root,
                artifact_path,
                kind="plan_proposal",
                schema_name="plan_proposal",
            )
            original = RunManifest.create(
                run_id="run-consumer",
                created_at="2026-07-20T12:00:00Z",
            ).with_artifact(artifact)

            loaded = RunManifest.from_dict(original.to_dict())
            loaded.verify_artifacts(root)
            historical_payload = original.to_dict()
            historical_payload["artifacts"][0]["schema_digest"] = "0" * 64
            historical = RunManifest.from_dict(historical_payload)
            with self.assertRaisesRegex(ValueError, "schema version is unavailable"):
                historical.verify_artifacts(root)
            artifact_path.write_text('{"schema_version": 1}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "artifact size changed|artifact digest changed"):
                loaded.verify_artifacts(root)


if __name__ == "__main__":
    unittest.main()
