from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.checks.public_snapshot_smoke import forbidden_sdist_members, reset_snapshot_output


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
