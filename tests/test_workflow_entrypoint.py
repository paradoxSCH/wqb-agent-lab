from __future__ import annotations

import unittest


class WorkflowEntrypointTests(unittest.TestCase):
    def test_canonical_module_exports_the_workflow_main(self) -> None:
        from scripts.run import workflow
        from wqb_agent_lab.workflow.cli import main as workflow_main

        self.assertTrue(callable(workflow.main))
        self.assertIs(workflow.main, workflow_main)


if __name__ == "__main__":
    unittest.main()
