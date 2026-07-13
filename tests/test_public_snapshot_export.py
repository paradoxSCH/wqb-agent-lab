from __future__ import annotations

import json
import io
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from scripts.release.export_public_snapshot import (
    SnapshotExportError,
    build_snapshot_report,
    export_public_snapshot,
    load_manifest,
    run,
    select_snapshot_files,
)


ROOT = Path(__file__).resolve().parents[1]


class PublicSnapshotExportTests(unittest.TestCase):
    @staticmethod
    def write(root: Path, relative_path: str, content: str = "content") -> Path:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_manifest(self, root: Path, payload: dict[str, object]) -> Path:
        path = root / "release" / "public_snapshot_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    @staticmethod
    def manifest_payload() -> dict[str, object]:
        return {
            "schema_version": 1,
            "include": {"files": ["README.md"], "trees": ["src", "data"]},
            "exclude": {
                "paths": [],
                "trees": ["data"],
                "glob_patterns": ["**/__pycache__/**", "*.pyc"],
            },
            "required_files": ["README.md"],
            "release_blockers": [
                {"id": "side_effect_governance", "message": "Side effects are not yet unified."}
            ],
        }

    def test_selection_is_sorted_and_exclusions_override_included_trees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "README.md", "readme")
            self.write(root, "src/zeta.py", "zeta")
            self.write(root, "src/alpha.py", "alpha")
            self.write(root, "src/__pycache__/alpha.pyc", "cache")
            self.write(root, ".local/data/private.json", "private")
            manifest = load_manifest(self.write_manifest(root, self.manifest_payload()))

            selected = select_snapshot_files(root, manifest)

            self.assertEqual(
                ["README.md", "src/alpha.py", "src/zeta.py"],
                [item.relative_path for item in selected],
            )
            self.assertEqual([6, 5, 4], [item.size for item in selected])

    def test_missing_required_file_has_stable_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = self.manifest_payload()
            manifest = load_manifest(self.write_manifest(root, payload))

            with self.assertRaises(SnapshotExportError) as raised:
                select_snapshot_files(root, manifest)

            self.assertEqual("missing_required_file", raised.exception.code)
            self.assertEqual("README.md", raised.exception.path)

    def test_manifest_rejects_traversal_and_absolute_paths(self) -> None:
        unsafe_paths = ("../private.txt", str(Path(tempfile.gettempdir()) / "private.txt"))
        for unsafe_path in unsafe_paths:
            with self.subTest(unsafe_path=unsafe_path), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                payload = self.manifest_payload()
                payload["include"] = {"files": [unsafe_path], "trees": []}

                with self.assertRaises(SnapshotExportError) as raised:
                    load_manifest(self.write_manifest(root, payload))

                self.assertEqual("unsafe_manifest_path", raised.exception.code)

    def test_manifest_rejects_non_object_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = self.manifest_payload()
            payload["release_blockers"] = ["side_effect_governance"]

            with self.assertRaises(SnapshotExportError) as raised:
                load_manifest(self.write_manifest(root, payload))

            self.assertEqual("invalid_manifest", raised.exception.code)

    def test_selection_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "README.md", "readme")
            target = self.write(root, "outside.txt", "private")
            link = root / "src" / "linked.txt"
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(target, link)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            manifest = load_manifest(self.write_manifest(root, self.manifest_payload()))

            with self.assertRaises(SnapshotExportError) as raised:
                select_snapshot_files(root, manifest)

            self.assertEqual("symlink_rejected", raised.exception.code)
            self.assertEqual("src/linked.txt", raised.exception.path)

    def test_check_mode_emits_draft_report_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "README.md", "readme")
            self.write(root, "src/app.py", "app")
            manifest_path = self.write_manifest(root, self.manifest_payload())
            output = root / "dist" / "public-snapshot"
            stdout = io.StringIO()

            exit_code = run(
                [
                    "--workspace-root",
                    str(root),
                    "--manifest",
                    str(manifest_path),
                    "--output",
                    str(output),
                    "--check",
                    "--json",
                ],
                stdout=stdout,
            )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(0, exit_code)
            self.assertFalse(output.exists())
            self.assertEqual("draft", payload["status"])
            self.assertFalse(payload["publish_ready"])
            self.assertEqual(2, payload["selected_file_count"])
            self.assertEqual(9, payload["selected_total_bytes"])
            self.assertEqual(["side_effect_governance"], [item["id"] for item in payload["release_blockers"]])
            self.assertIn("source_commit", payload)
            self.assertIn("source_dirty", payload)

    def test_report_is_publish_ready_only_without_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "README.md", "readme")
            payload = self.manifest_payload()
            payload["release_blockers"] = []
            manifest = load_manifest(self.write_manifest(root, payload))
            selected = select_snapshot_files(root, manifest)

            report = build_snapshot_report(root, manifest, selected)

            self.assertEqual("ready", report["status"])
            self.assertTrue(report["publish_ready"])

    def test_export_copies_bytes_and_writes_hash_and_blocker_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            output = base / "public-snapshot"
            root.mkdir()
            readme_bytes = b"public readme\r\n"
            readme = root / "README.md"
            readme.write_bytes(readme_bytes)
            self.write(root, "src/app.py", "print('app')\n")
            manifest_path = self.write_manifest(root, self.manifest_payload())

            result = export_public_snapshot(root, output, manifest_path)

            self.assertEqual(readme_bytes, (output / "README.md").read_bytes())
            self.assertEqual("draft", result.report["status"])
            metadata = json.loads((output / "PUBLIC_SNAPSHOT_MANIFEST.json").read_text(encoding="utf-8"))
            blockers = json.loads((output / "PUBLIC_SNAPSHOT_BLOCKERS.json").read_text(encoding="utf-8"))
            self.assertNotIn("output_path", metadata)
            self.assertNotIn(str(output), (output / "PUBLIC_SNAPSHOT_MANIFEST.json").read_text(encoding="utf-8"))
            rows = {item["path"]: item for item in metadata["files"]}
            self.assertEqual(hashlib.sha256(readme_bytes).hexdigest(), rows["README.md"]["sha256"])
            self.assertEqual(len(readme_bytes), rows["README.md"]["size"])
            self.assertFalse(metadata["publish_ready"])
            self.assertEqual("side_effect_governance", blockers["release_blockers"][0]["id"])

    def test_export_rejects_non_empty_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            output = base / "public-snapshot"
            root.mkdir()
            output.mkdir()
            self.write(output, "keep.txt", "keep")
            self.write(root, "README.md", "readme")
            manifest_path = self.write_manifest(root, self.manifest_payload())

            with self.assertRaises(SnapshotExportError) as raised:
                export_public_snapshot(root, output, manifest_path)

            self.assertEqual("output_not_empty", raised.exception.code)
            self.assertTrue((output / "keep.txt").exists())

    def test_actual_public_snapshot_selects_and_exports_research_policy_docs(self) -> None:
        manifest_path = ROOT / "release" / "public_snapshot_manifest.json"
        manifest = load_manifest(manifest_path)
        selected = select_snapshot_files(ROOT, manifest)
        report = build_snapshot_report(ROOT, manifest, selected)

        self.assertIn("docs/user/RESEARCH_POLICY.md", report["selected_paths"])
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "public-snapshot"
            result = export_public_snapshot(ROOT, output, manifest_path)

            self.assertIn(
                "docs/user/RESEARCH_POLICY.md",
                [item.relative_path for item in result.files],
            )
            self.assertEqual(
                (ROOT / "docs" / "user" / "RESEARCH_POLICY.md").read_bytes(),
                (output / "docs" / "user" / "RESEARCH_POLICY.md").read_bytes(),
            )

    def test_export_rejects_output_inside_included_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "README.md", "readme")
            self.write(root, "src/app.py", "app")
            manifest_path = self.write_manifest(root, self.manifest_payload())

            with self.assertRaises(SnapshotExportError) as raised:
                export_public_snapshot(root, root / "src" / "snapshot", manifest_path)

            self.assertEqual("output_overlaps_source", raised.exception.code)

    def test_release_audit_failure_preserves_preexisting_empty_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            output = base / "public-snapshot"
            root.mkdir()
            output.mkdir()
            self.write(root, "README.md", "readme")
            self.write(root, "pyproject.toml", 'Homepage = "https://github.com/your-org/project"\n')
            payload = self.manifest_payload()
            payload["include"] = {"files": ["README.md", "pyproject.toml"], "trees": []}
            manifest_path = self.write_manifest(root, payload)

            with self.assertRaises(SnapshotExportError) as raised:
                export_public_snapshot(root, output, manifest_path)

            self.assertEqual("release_audit_failed", raised.exception.code)
            self.assertTrue(output.is_dir())
            self.assertEqual([], list(output.iterdir()))

    def test_release_audit_failure_removes_output_created_by_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            output = base / "public-snapshot"
            root.mkdir()
            self.write(root, "README.md", "readme")
            self.write(root, "pyproject.toml", 'Homepage = "https://github.com/your-org/project"\n')
            payload = self.manifest_payload()
            payload["include"] = {"files": ["README.md", "pyproject.toml"], "trees": []}
            manifest_path = self.write_manifest(root, payload)

            with self.assertRaises(SnapshotExportError):
                export_public_snapshot(root, output, manifest_path)

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
