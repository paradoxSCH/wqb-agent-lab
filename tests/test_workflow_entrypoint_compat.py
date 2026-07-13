from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from scripts import kimi_daily_workflow
from scripts.run import workflow


class WorkflowEntrypointCompatibilityTests(unittest.TestCase):
    def test_legacy_entrypoint_warns_and_forwards_once(self) -> None:
        stderr = io.StringIO()
        with patch.object(kimi_daily_workflow, "workflow_main", return_value=7) as canonical, patch(
            "sys.stderr", stderr
        ):
            exit_code = kimi_daily_workflow.main()

        self.assertEqual(7, exit_code)
        canonical.assert_called_once_with()
        self.assertIn("scripts.run.workflow", stderr.getvalue())
        self.assertIn("0.3.0", stderr.getvalue())

    def test_canonical_module_exports_the_workflow_main(self) -> None:
        from src.kimi_daily_workflow import main as engine_main

        self.assertTrue(callable(workflow.main))
        self.assertIs(workflow.main, engine_main)


if __name__ == "__main__":
    unittest.main()
