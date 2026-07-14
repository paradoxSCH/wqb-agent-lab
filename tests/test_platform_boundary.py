from __future__ import annotations

import ast
import unittest
from pathlib import Path

from src.wqb import WQBClient as CompatibilityClient
from src.wqb.check_readiness import evaluate_check_snapshot as compatibility_readiness
from src.wqb_agent_lab.platform import WQBClient, evaluate_check_snapshot, load_operator_names


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PLATFORM = ROOT / "src/wqb_agent_lab/platform"


class PlatformBoundaryTests(unittest.TestCase):
    def test_legacy_imports_delegate_to_canonical_objects(self) -> None:
        self.assertIs(CompatibilityClient, WQBClient)
        self.assertIs(compatibility_readiness, evaluate_check_snapshot)

    def test_operator_catalog_is_owned_by_canonical_package(self) -> None:
        catalog = CANONICAL_PLATFORM / "resources/operators.json"

        self.assertTrue(catalog.is_file())
        self.assertGreater(len(load_operator_names()), 20)
        self.assertFalse((ROOT / "src/wqb/resources/operators.json").exists())

    def test_no_runtime_module_imports_third_party_wqb(self) -> None:
        violations = []
        for path in _python_files(ROOT / "src", ROOT / "scripts", ROOT / "run_scan.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(alias.name == "wqb" for alias in node.names):
                    violations.append(str(path.relative_to(ROOT)))
                if isinstance(node, ast.ImportFrom) and node.module == "wqb":
                    violations.append(str(path.relative_to(ROOT)))

        self.assertEqual([], sorted(set(violations)))
        self.assertFalse((CANONICAL_PLATFORM / "third_party.py").exists())

    def test_wqb_api_origin_is_declared_only_by_canonical_platform(self) -> None:
        violations = []
        for path in _python_files(ROOT / "src", ROOT / "scripts", ROOT / "run_scan.py"):
            if path.parent == CANONICAL_PLATFORM:
                continue
            if "api.worldquantbrain.com" in path.read_text(encoding="utf-8-sig"):
                violations.append(str(path.relative_to(ROOT)))

        self.assertEqual([], violations)

    def test_product_and_operational_modules_do_not_import_compatibility_package(self) -> None:
        violations = []
        for path in _python_files(ROOT / "src", ROOT / "scripts", ROOT / "run_scan.py"):
            if ROOT / "src/wqb" in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src.wqb"):
                    if not (node.module or "").startswith("src.wqb_agent_lab"):
                        violations.append(str(path.relative_to(ROOT)))

        self.assertEqual([], sorted(set(violations)))


def _python_files(*paths: Path) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        else:
            files.extend(
                child
                for child in path.rglob("*.py")
                if "__pycache__" not in child.parts and "node_modules" not in child.parts
            )
    return sorted(files)


if __name__ == "__main__":
    unittest.main()
