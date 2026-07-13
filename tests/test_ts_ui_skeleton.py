from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "packages" / "wqb-agent-ui"


class TSUISkeletonTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (PACKAGE_ROOT / relative_path).read_text(encoding="utf-8")

    def test_package_files_exist(self) -> None:
        required = (
            "package.json",
            "tsconfig.json",
            "index.html",
            "public/index.html",
            "src/App.tsx",
            "src/main.tsx",
            "src/sampleRunSummary.ts",
            "src/styles.css",
            "src/runSummaryView.ts",
            "src/index.ts",
            "test/run-summary-view.test.mjs",
        )

        missing = [path for path in required if not (PACKAGE_ROOT / path).is_file()]

        self.assertEqual([], missing)

    def test_package_metadata_is_dashboard_shell(self) -> None:
        package = json.loads(self.read("package.json"))

        self.assertEqual("@wqb-agent-lab/ui", package["name"])
        self.assertEqual("module", package["type"])
        self.assertIn("test", package["scripts"])
        self.assertIn("typecheck", package["scripts"])
        self.assertIn("build", package["scripts"])
        self.assertIn("react", package["dependencies"])
        self.assertIn("vite", package["dependencies"])

    def test_run_summary_view_consumes_public_contract(self) -> None:
        source = self.read("src/runSummaryView.ts")

        self.assertIn("run_summary", source)
        self.assertIn("toRunSummaryViewModel", source)
        self.assertIn("submitReady", source)
        self.assertIn("budgetRemaining", source)

    def test_app_is_readonly_chinese_run_monitor(self) -> None:
        app_source = self.read("src/App.tsx")
        css_source = self.read("src/styles.css")

        self.assertIn("只读", app_source)
        self.assertIn("预算", app_source)
        self.assertIn("提交就绪", app_source)
        self.assertIn("toRunSummaryViewModel", app_source)
        self.assertIn("oklch", css_source)
        self.assertNotIn("hero", app_source.lower())

    def test_ui_shell_does_not_touch_wqb_platform_or_python_internals(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in (PACKAGE_ROOT / "src").glob("*.ts"))

        forbidden = (
            "api.worldquantbrain.com",
            "WQB_EMAIL",
            "WQB_PASSWORD",
            "src/wqb",
            "src\\\\wqb",
            ".local/data/runs/continuous-alpha",
        )

        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
