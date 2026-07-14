from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.checks.public_snapshot_smoke import (
    forbidden_sdist_members,
    generated_snapshot_paths,
    reset_audit_output,
    reset_snapshot_output,
    snapshot_inventory,
)


class PublicSnapshotSmokeTests(unittest.TestCase):
    def test_reset_snapshot_output_rejects_any_other_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaises(ValueError):
                reset_snapshot_output(root, root / "other")

    def test_reset_snapshot_output_clears_only_canonical_dist_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "dist/release-check/public-snapshot"
            output.mkdir(parents=True)
            (output / "stale.txt").write_text("stale", encoding="utf-8")

            resolved = reset_snapshot_output(root, output)

            self.assertEqual(output.resolve(), resolved)
            self.assertFalse(output.exists())

    def test_reset_audit_output_clears_only_sidecar_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            audit = root / "dist/release-check/public-snapshot-audit"
            audit.mkdir(parents=True)
            (audit / "stale.json").write_text("{}", encoding="utf-8")

            resolved = reset_audit_output(root)

            self.assertEqual(audit.resolve(), resolved)
            self.assertFalse(audit.exists())

    def test_snapshot_inventory_detects_changes_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            snapshot = Path(raw) / "snapshot"
            snapshot.mkdir()
            source = snapshot / "src" / "app.py"
            source.parent.mkdir()
            source.write_text("one", encoding="utf-8")
            before = snapshot_inventory(snapshot)

            source.write_text("two", encoding="utf-8")

            self.assertNotEqual(before, snapshot_inventory(snapshot))

    def test_generated_snapshot_paths_rejects_build_metadata_and_inline_audit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            snapshot = Path(raw)
            for relative in (
                "build/lib/app.py",
                "wqb_agent_lab.egg-info/PKG-INFO",
                "src/__pycache__/app.pyc",
                "PUBLIC_SNAPSHOT_MANIFEST.json",
            ):
                path = snapshot / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("generated", encoding="utf-8")

            contaminated = generated_snapshot_paths(snapshot)

            self.assertEqual(
                (
                    "PUBLIC_SNAPSHOT_MANIFEST.json",
                    "build",
                    "build/lib",
                    "build/lib/app.py",
                    "src/__pycache__",
                    "src/__pycache__/app.pyc",
                    "wqb_agent_lab.egg-info",
                    "wqb_agent_lab.egg-info/PKG-INFO",
                ),
                contaminated,
            )

    def test_sdist_inventory_rejects_archived_and_private_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            archive_path = Path(raw) / "package.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for name in (
                    "package/src/module.py",
                    "package/docs/archive/old.md",
                    "package/.local/data/run.json",
                ):
                    content = b"x"
                    info = tarfile.TarInfo(name)
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))

            forbidden = forbidden_sdist_members(archive_path)

            self.assertEqual((".local/data/run.json", "docs/archive/old.md"), forbidden)


if __name__ == "__main__":
    unittest.main()
