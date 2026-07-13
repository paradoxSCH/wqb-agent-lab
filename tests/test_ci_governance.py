from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CIGovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    def test_python_matrix_covers_supported_versions_and_operating_systems(self) -> None:
        for marker in ('os: [ubuntu-latest, windows-latest]', 'python: ["3.11", "3.12"]'):
            self.assertIn(marker, self.workflow)
        self.assertIn("runs-on: ${{ matrix.os }}", self.workflow)
        self.assertIn("python-version: ${{ matrix.python }}", self.workflow)

    def test_ci_uses_frozen_dependency_installs(self) -> None:
        self.assertGreaterEqual(
            self.workflow.count("uv sync --extra dev --extra mcp --frozen"),
            2,
        )
        self.assertGreaterEqual(self.workflow.count("npm ci --prefix packages/wqb-agent-mcp"), 2)
        self.assertGreaterEqual(self.workflow.count("npm ci --prefix packages/wqb-agent-ui"), 2)
        self.assertNotIn('pip install -e ".[dev]"', self.workflow)

    def test_ci_reuses_canonical_development_commands(self) -> None:
        for command in ("check", "test", "build", "release-check"):
            self.assertIn(f"python -m scripts.dev {command}", self.workflow)

    def test_windows_python_matrix_streams_pytest_diagnostics(self) -> None:
        self.assertIn("if: runner.os != 'Windows'", self.workflow)
        self.assertIn("if: runner.os == 'Windows'", self.workflow)
        self.assertIn("uv run python -m pytest -vv", self.workflow)

    def test_node_and_release_jobs_use_pinned_runtime_majors(self) -> None:
        self.assertIn("node-verification:", self.workflow)
        self.assertIn("release-verification:", self.workflow)
        self.assertIn('node: ["22", "24"]', self.workflow)
        self.assertIn('node-version: ${{ matrix.node }}', self.workflow)
        self.assertIn('node-version: "24"', self.workflow)
        self.assertIn('python-version: "3.12"', self.workflow)

    def test_ci_is_credential_free_and_does_not_launch_live_workers(self) -> None:
        for forbidden in (
            "WQB_EMAIL",
            "WQB_PASSWORD",
            "WQB_LIVE_SIMULATION_CAPABILITY=1",
            "WQB_LIVE_SUBMIT_CAPABILITY=1",
            "submission_worker --daemon",
        ):
            self.assertNotIn(forbidden, self.workflow)

    def test_release_job_uploads_reports_even_after_failure(self) -> None:
        self.assertIn("if: always()", self.workflow)
        self.assertIn("dist/audit", self.workflow)
        self.assertIn("dist/packages", self.workflow)


if __name__ == "__main__":
    unittest.main()
