from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.checks.release_version import check_release_versions


class ReleaseVersionTests(unittest.TestCase):
    def test_accepts_matching_prerelease_tag_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._workspace(Path(tmp), version="1.2.3a1", npm_version="1.2.3-alpha.1")
            report = check_release_versions(root, tag="v1.2.3a1")
            self.assertEqual("ok", report["status"])

    def test_rejects_mismatched_tag_or_package_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._workspace(Path(tmp), version="1.2.3", ui_version="1.2.2")
            report = check_release_versions(root, tag="v1.2.4")
            self.assertEqual("failed", report["status"])
            self.assertIn("packages/wqb-agent-ui/package.json", report["mismatches"])
            self.assertIn("does not match", report["tag_error"])

    @staticmethod
    def _workspace(root: Path, *, version: str, ui_version: str | None = None, npm_version: str | None = None) -> Path:
        (root / "packages/wqb-agent-mcp").mkdir(parents=True)
        (root / "packages/wqb-agent-ui").mkdir(parents=True)
        (root / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
        (root / "CITATION.cff").write_text(f"version: {version}\n", encoding="utf-8")
        (root / "packages/wqb-agent-mcp/package.json").write_text(
            json.dumps({"version": npm_version or version}), encoding="utf-8"
        )
        (root / "packages/wqb-agent-ui/package.json").write_text(
            json.dumps({"version": ui_version or npm_version or version}), encoding="utf-8"
        )
        return root
