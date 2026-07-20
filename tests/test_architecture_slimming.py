from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureSlimmingTests(unittest.TestCase):
    def test_retired_scheduler_stack_is_absent(self) -> None:
        retired = (
            "src/continuous_alpha_scheduler.py",
            "src/llm_template_generator.py",
            "src/wq/workflows/__init__.py",
            "scripts/run/scheduler.py",
        )

        self.assertEqual([], [path for path in retired if (ROOT / path).exists()])

    def test_engineering_checks_do_not_reference_retired_scheduler(self) -> None:
        source = (ROOT / "scripts/dev.py").read_text(encoding="utf-8")

        self.assertNotIn("continuous_alpha_scheduler.py", source)

    def test_expired_compatibility_surfaces_are_absent(self) -> None:
        retired = (
            "run_scan.py",
            "scripts/research_workflow.py",
            "src/wqb",
            "src/wqb_agent_lab",
            "src/wq",
            "src/research_workflow.py",
        )

        remaining = []
        for relative in retired:
            path = ROOT / relative
            if path.is_file() or (path.is_dir() and any(path.rglob("*.py"))):
                remaining.append(relative)

        self.assertEqual([], remaining)

    def test_distribution_does_not_package_legacy_modules(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertNotIn("py-modules", pyproject)

    def test_llm_runtime_has_no_legacy_config_resolver(self) -> None:
        runtime_sources = (
            ROOT / "wqb_agent_lab" / "llm" / "provider" / "config.py",
            ROOT / "wqb_agent_lab" / "llm" / "provider" / "identity.py",
            ROOT / "wqb_agent_lab" / "workflow" / "llm_planning.py",
        )
        source = "\n".join(path.read_text(encoding="utf-8") for path in runtime_sources)

        self.assertNotIn("_normalize_legacy_adapter", source)
        self.assertNotIn("_kimi_environment_config", source)
        self.assertNotIn('provider == "kimi_cli"', source)

    def test_current_docs_do_not_advertise_retired_module_commands(self) -> None:
        migration = (ROOT / "docs" / "user" / "MIGRATING.md").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("python -m scripts.kimi_daily_workflow", migration)

    def test_workflow_foundations_are_split_from_engine(self) -> None:
        workflow = ROOT / "wqb_agent_lab" / "workflow"

        self.assertTrue((workflow / "artifacts.py").is_file())
        self.assertTrue((workflow / "candidates.py").is_file())
        self.assertTrue((workflow / "config_selection.py").is_file())
        self.assertTrue((workflow / "diagnosis.py").is_file())
        self.assertTrue((workflow / "models.py").is_file())
        self.assertTrue((workflow / "postprocessing.py").is_file())
        self.assertTrue((workflow / "reporting.py").is_file())
        self.assertTrue((workflow / "submitted_registry.py").is_file())
        self.assertLess(
            len((workflow / "engine.py").read_text(encoding="utf-8").splitlines()),
            2425,
        )


if __name__ == "__main__":
    unittest.main()
