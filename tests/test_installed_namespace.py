from __future__ import annotations

import unittest

from wqb_agent_lab import RepositoryLayout
from wqb_agent_lab.platform import WQBClient, WQBSession
from wqb_agent_lab.platform.client import WQBClient as DirectWQBClient
from wqb_agent_lab.workflow import ResearchWorkflow


class InstalledNamespaceTests(unittest.TestCase):
    def test_standard_namespace_exports_product_boundaries(self) -> None:
        self.assertIsNotNone(RepositoryLayout)
        self.assertIs(WQBClient, DirectWQBClient)
        self.assertIsNotNone(WQBSession)
        self.assertIsNotNone(ResearchWorkflow)
