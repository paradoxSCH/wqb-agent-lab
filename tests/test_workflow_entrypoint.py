from __future__ import annotations

import unittest


class WorkflowEntrypointTests(unittest.TestCase):
    def test_canonical_module_exports_the_workflow_main(self) -> None:
        from scripts.run import workflow
        from wqb_agent_lab.workflow.engine import main as engine_main

        self.assertTrue(callable(workflow.main))
        self.assertIs(workflow.main, engine_main)


if __name__ == "__main__":
    unittest.main()
