from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DependencyGovernanceTests(unittest.TestCase):
    def test_pyproject_and_uv_lock_are_the_only_committed_python_dependency_sources(self) -> None:
        self.assertTrue((ROOT / "pyproject.toml").is_file())
        self.assertTrue((ROOT / "uv.lock").is_file())
        self.assertFalse((ROOT / "requirements.txt").exists())

    def test_lock_matches_project_name_and_supported_python(self) -> None:
        lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

        self.assertIn('name = "wqb-agent-lab"', lock)
        self.assertIn('requires-python = ">=3.11, <3.13"', lock)

    def test_node_lockfiles_use_the_public_npm_registry(self) -> None:
        for relative_path in (
            "packages/wqb-agent-mcp/package-lock.json",
            "packages/wqb-agent-ui/package-lock.json",
        ):
            lock = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("https://registry.npmjs.org/", lock)
            self.assertNotIn("npmmirror.com", lock)

    def test_dependency_groups_have_single_ownership(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        runtime = set(project["project"]["dependencies"])
        optional = project["project"]["optional-dependencies"]

        self.assertIn("wqb==0.2.5", runtime)
        self.assertNotIn("openai>=1.0", runtime)
        self.assertIn("pytest>=8.0", optional["dev"])
        self.assertIn("mcp>=1.0.0", optional["mcp"])
        self.assertTrue(runtime.isdisjoint(optional["dev"]))
        self.assertTrue(runtime.isdisjoint(optional["mcp"]))

    def test_ruff_targets_supported_python_without_baseline_suppression(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        ruff = project["tool"]["ruff"]

        self.assertEqual("py311", ruff["target-version"])
        self.assertNotIn("ignore", ruff.get("lint", {}))


if __name__ == "__main__":
    unittest.main()
