from __future__ import annotations

import re
import unittest
from importlib import import_module
from pathlib import Path

from src.memory_governance.policy import EvidenceAssessment, resolve_action_permission


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_product_namespaces_are_importable(self) -> None:
        for namespace in ("platform", "workflow", "research", "memory", "evaluation", "governance"):
            module = import_module(f"src.wqb_agent_lab.{namespace}")
            self.assertTrue(module.__all__, namespace)

    def test_workflow_boundary_exports_only_the_production_orchestrator(self) -> None:
        from src.kimi_daily_workflow import KimiDailyWorkflow
        from src.wqb_agent_lab import workflow

        self.assertIs(workflow.ResearchWorkflow, KimiDailyWorkflow)
        self.assertNotIn("ContinuousAlphaScheduler", workflow.__all__)

    def test_active_agent_layers_do_not_call_wqb_http_directly(self) -> None:
        active_paths = [
            ROOT / "src" / "continuous_alpha_scheduler.py",
            ROOT / "src" / "kimi_daily_workflow.py",
            ROOT / "src" / "workflow_daemon.py",
            ROOT / "src" / "submission_governance",
            ROOT / "src" / "wqb_mcp",
            ROOT / "packages" / "wqb-agent-mcp" / "src",
        ]

        violations: list[str] = []
        for path in _python_and_ts_files(active_paths):
            text = path.read_text(encoding="utf-8")
            if "api.worldquantbrain.com" in text or re.search(r"\brequests\.Session\s*\(", text):
                violations.append(str(path.relative_to(ROOT)))

        self.assertEqual(
            violations,
            [],
            "Active agent layers must route WQB access through src.wqb_agent_lab.platform.WQBClient.",
        )

    def test_production_scan_uses_canonical_wqb_client(self) -> None:
        source = (ROOT / "run_scan.py").read_text(encoding="utf-8")

        self.assertIn("from src.wqb_agent_lab.platform import WQBClient", source)
        self.assertNotIn("from src.session import", source)
        self.assertNotIn("from src.simulator import", source)

    def test_memory_governance_does_not_grant_executor_permissions(self) -> None:
        permission = resolve_action_permission(EvidenceAssessment("L4", ("stable repeated evidence",)))

        self.assertTrue(permission.can_use_in_prompt)
        self.assertTrue(permission.can_promote)
        self.assertFalse(permission.can_block_generation)
        self.assertEqual(permission.max_budget_policy, "policy_evaluator_required")

    def test_ui_and_ts_mcp_do_not_read_credentials_or_call_wqb_http(self) -> None:
        roots = [
            ROOT / "packages" / "wqb-agent-mcp" / "src",
            ROOT / "packages" / "wqb-agent-ui" / "src",
        ]
        forbidden = ("WQB_EMAIL", "WQB_PASSWORD", "api.worldquantbrain.com")

        violations: list[str] = []
        for path in _python_and_ts_files(roots):
            text = path.read_text(encoding="utf-8")
            if any(token in text for token in forbidden):
                violations.append(str(path.relative_to(ROOT)))

        self.assertEqual(violations, [])


def _python_and_ts_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix in {".py", ".ts", ".tsx"}:
            files.append(path)
        elif path.is_dir():
            files.extend(
                child
                for child in path.rglob("*")
                if child.is_file()
                and child.suffix in {".py", ".ts", ".tsx"}
                and "node_modules" not in child.parts
                and "__pycache__" not in child.parts
            )
    return sorted(files)
