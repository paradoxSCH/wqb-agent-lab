from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "packages" / "wqb-agent-mcp"


class TSMCPSkeletonTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (PACKAGE_ROOT / relative_path).read_text(encoding="utf-8")

    def test_package_files_exist(self) -> None:
        required = (
            "package.json",
            "tsconfig.json",
            "src/engineClient.ts",
            "src/server.ts",
            "src/main.ts",
            "src/toolManifest.ts",
            "src/index.ts",
            "test/manifest.test.mjs",
        )

        missing = [path for path in required if not (PACKAGE_ROOT / path).is_file()]

        self.assertEqual([], missing)

    def test_package_metadata_is_local_first_mcp_shell(self) -> None:
        package = json.loads(self.read("package.json"))

        self.assertEqual("@wqb-agent-lab/mcp", package["name"])
        self.assertEqual("module", package["type"])
        self.assertIn("test", package["scripts"])
        self.assertIn("typecheck", package["scripts"])
        self.assertIn("build", package["scripts"])
        self.assertIn("start", package["scripts"])
        self.assertIn("@modelcontextprotocol/sdk", package["dependencies"])
        self.assertIn("wqb-engine", self.read("src/engineClient.ts"))

    def test_server_uses_official_mcp_sdk_and_readonly_annotations(self) -> None:
        server_source = self.read("src/server.ts")
        main_source = self.read("src/main.ts")

        self.assertIn("@modelcontextprotocol/sdk/server/mcp.js", server_source)
        self.assertIn("@modelcontextprotocol/sdk/server/stdio.js", main_source)
        self.assertIn("createWQBAgentMcpServer", server_source)
        self.assertIn("readOnlyHint: true", server_source)
        self.assertIn("destructiveHint: false", server_source)
        self.assertIn("server.connect(transport)", main_source)

    def test_tool_manifest_matches_engine_cli_p0_operations(self) -> None:
        manifest = self.read("src/toolManifest.ts")

        for operation in (
            "schemas.list",
            "schemas.digest",
            "contracts.validate",
            "submission.evaluate",
            "submission.submit_intent",
            "submission.execute_live",
            "submission.audit_tail",
            "loop.dry_run_validate",
            "policy.validate",
            "policy.show",
        ):
            self.assertIn(f'name: "{operation}"', manifest)

    def test_typescript_package_does_not_directly_touch_wqb_platform_or_credentials(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PACKAGE_ROOT / "src").glob("*.ts")
        )

        forbidden = (
            "api.worldquantbrain.com",
            "WQB_EMAIL",
            "WQB_PASSWORD",
            "WorldQuantBrain",
            "requests",
        )

        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
