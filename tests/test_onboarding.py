from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import onboarding


ROOT = Path(__file__).resolve().parents[1]


class OnboardingTests(unittest.TestCase):
    @staticmethod
    def create_checkout(root: Path, *, with_node_modules: bool = False) -> None:
        for relative_path in (
            "pyproject.toml",
            "uv.lock",
            ".env.example",
            "configs/examples/production-workflow.example.json",
        ):
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        (root / ".env").write_text("WQB_LIVE_SIMULATION_CAPABILITY=0\n", encoding="utf-8")
        config = root / ".local/research/workflows/production.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('{"research_policy": {}}', encoding="utf-8")
        if with_node_modules:
            for package in ("wqb-agent-mcp", "wqb-agent-ui"):
                (root / "packages" / package / "node_modules").mkdir(parents=True)

    def test_runtime_profile_does_not_require_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.create_checkout(root)
            with (
                patch.object(onboarding.sys, "version_info", (3, 12, 7)),
                patch("scripts.onboarding.shutil.which", side_effect=lambda name: "uv" if name == "uv" else None),
                patch("scripts.onboarding._command_version", return_value="uv 0.11.27"),
                patch("scripts.onboarding.importlib.metadata.version", return_value="1.0.0"),
            ):
                report = onboarding.build_doctor_report("runtime", root)

        self.assertEqual("ready", report["status"])
        self.assertNotIn("node", [item["id"] for item in report["checks"]])
        self.assertIn("wqb-engine demo", report["next_command"])

    def test_runtime_dependency_check_uses_only_declared_owned_runtime(self) -> None:
        requested: list[str] = []

        def version(distribution: str) -> str:
            requested.append(distribution)
            return "1.0.0"

        with patch("scripts.onboarding.importlib.metadata.version", side_effect=version):
            check = onboarding._runtime_dependency_check()

        self.assertEqual("pass", check.status)
        self.assertEqual(["wqb-agent-lab", "pandas", "python-dotenv", "requests"], requested)

    def test_full_profile_reports_missing_node_with_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.create_checkout(root, with_node_modules=True)
            with (
                patch.object(onboarding.sys, "version_info", (3, 12, 7)),
                patch("scripts.onboarding.shutil.which", side_effect=lambda name: "uv" if name == "uv" else None),
                patch("scripts.onboarding._command_version", return_value="uv 0.11.27"),
                patch("scripts.onboarding.importlib.metadata.version", return_value="1.0.0"),
            ):
                report = onboarding.build_doctor_report("full", root)

        self.assertEqual("blocked", report["status"])
        node = next(item for item in report["checks"] if item["id"] == "node")
        self.assertEqual("fail", node["status"])
        self.assertIn("nodejs.org", node["fix_command"])

    def test_full_profile_detects_node_and_npm_runtime_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.create_checkout(root, with_node_modules=True)

            def which(name: str) -> str | None:
                return {"uv": "uv", "node": "node", "npm": "npm", "npm.cmd": "npm"}.get(name)

            def command_version(executable: str, argument: str = "--version") -> str:
                return {"uv": "uv 0.11.27", "node": "v24.18.0", "npm": "11.16.0"}[executable]

            with (
                patch.object(onboarding.sys, "version_info", (3, 12, 7)),
                patch("scripts.onboarding.shutil.which", side_effect=which),
                patch("scripts.onboarding._command_version", side_effect=command_version),
                patch("scripts.onboarding._npm_runtime_version", return_value="25.9.0"),
                patch("scripts.onboarding.importlib.metadata.version", return_value="1.0.0"),
            ):
                report = onboarding.build_doctor_report("full", root)

        mismatch = next(item for item in report["checks"] if item["id"] == "npm_node_runtime")
        self.assertEqual("blocked", report["status"])
        self.assertEqual("fail", mismatch["status"])
        self.assertIn("PATH", mismatch["fix_command"])

    def test_full_profile_allows_canonical_build_before_dashboard_artifact_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.create_checkout(root, with_node_modules=True)

            def which(name: str) -> str | None:
                return {"uv": "uv", "node": "node", "npm": "npm", "npm.cmd": "npm"}.get(name)

            def command_version(executable: str, argument: str = "--version") -> str:
                return {"uv": "uv 0.11.27", "node": "v24.18.0", "npm": "11.16.0"}[executable]

            with (
                patch.object(onboarding.sys, "version_info", (3, 12, 7)),
                patch("scripts.onboarding.shutil.which", side_effect=which),
                patch("scripts.onboarding._command_version", side_effect=command_version),
                patch("scripts.onboarding._npm_runtime_version", return_value="24.18.0"),
                patch("scripts.onboarding.importlib.metadata.version", return_value="1.0.0"),
            ):
                report = onboarding.build_doctor_report("full", root)

        dashboard = next(item for item in report["checks"] if item["id"] == "dashboard_build")
        self.assertEqual("attention", report["status"])
        self.assertEqual("warn", dashboard["status"])
        self.assertEqual("npm run build --prefix packages/wqb-agent-ui", dashboard["fix_command"])
        self.assertIn("scripts.dev check", report["next_command"])

    def test_version_parser_and_supported_baselines_are_explicit(self) -> None:
        self.assertEqual((24, 18, 0), onboarding._version_tuple("v24.18.0"))
        self.assertEqual((0, 11, 27), onboarding._version_tuple("uv 0.11.27"))
        self.assertIsNone(onboarding._version_tuple("unknown"))

        self.assertEqual("3.12", (ROOT / ".python-version").read_text(encoding="utf-8").strip())
        for package in ("wqb-agent-mcp", "wqb-agent-ui"):
            payload = json.loads((ROOT / "packages" / package / "package.json").read_text(encoding="utf-8"))
            self.assertEqual("^22.12.0 || ^24.0.0", payload["engines"]["node"])
            self.assertEqual(">=10 <12", payload["engines"]["npm"])

    def test_bootstrap_paths_never_enable_live_capabilities(self) -> None:
        for relative_path in ("scripts/bootstrap.ps1", "scripts/bootstrap.sh", "AGENTS.md"):
            content = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("WQB_LIVE_SIMULATION_CAPABILITY=1", content)
            self.assertNotIn("WQB_LIVE_SUBMIT_CAPABILITY=1", content)
            self.assertIn("doctor", content)


if __name__ == "__main__":
    unittest.main()
