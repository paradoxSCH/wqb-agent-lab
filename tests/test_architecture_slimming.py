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


if __name__ == "__main__":
    unittest.main()
