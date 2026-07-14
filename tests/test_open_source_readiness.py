from __future__ import annotations

from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OpenSourceReadinessTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_required_public_project_files_exist(self) -> None:
        required_files = (
            "README.md",
            ".python-version",
            ".nvmrc",
            "AGENTS.md",
            "CHANGELOG.md",
            "NOTICE",
            "LICENSE",
            "LICENSES/CC-BY-4.0.txt",
            "CITATION.cff",
            "GOVERNANCE.md",
            "pyproject.toml",
            "uv.lock",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "CODE_OF_CONDUCT.md",
            "docs/README.md",
            "docs/architecture/README.md",
            "docs/user/GETTING_STARTED.md",
            "docs/maintainers/OPEN_SOURCE_READINESS.md",
            "docs/maintainers/PUBLICATION_DECISIONS.md",
            "docs/user/LLM_PROVIDERS.md",
            "docs/user/RESEARCH_POLICY.md",
            "docs/user/TROUBLESHOOTING.md",
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
            ".github/CODEOWNERS",
            ".github/dependabot.yml",
            ".github/pull_request_template.md",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
            "schemas/submission_job.schema.json",
            "schemas/__init__.py",
            "packages/wqb-agent-mcp/package.json",
            "packages/wqb-agent-ui/package.json",
            "release/public_snapshot_manifest.json",
            "scripts/release/export_public_snapshot.py",
            "tests/test_public_snapshot_export.py",
            "src/side_effect_governance/capabilities.py",
            "run_scan.py",
            "scripts/bootstrap.ps1",
            "scripts/bootstrap.sh",
        )

        missing = [path for path in required_files if not (ROOT / path).is_file()]

        self.assertEqual([], missing)

    def test_readme_declares_positioning_setup_and_safety_boundary(self) -> None:
        readme = self.read("README.md")

        required_markers = (
            "WorldQuant BRAIN",
            "可审计的研究工作流",
            "Not affiliated with WorldQuant",
            "真实平台副作用初始为关闭状态",
            "uv run python -m scripts.dev check",
            "scripts.dev doctor --profile runtime --json",
            "wqb-agent-lab",
            "uv run wqb-engine demo",
            "docs/user/GETTING_STARTED.md",
            "docs/user/RESEARCH_POLICY.md",
            "docs/user/LLM_PROVIDERS.md",
            "Apache-2.0",
            "CC BY 4.0",
            "CITATION.cff",
        )

        for marker in required_markers:
            self.assertIn(marker, readme)
        self.assertNotIn("## License\n\nMIT. See [LICENSE](LICENSE).", readme)

        for vague_positioning in ("本地优先", "local-first", "agent-native"):
            self.assertNotIn(vague_positioning, readme.lower())

    def test_readme_declares_exact_dual_license_scope_and_citation_request(self) -> None:
        readme = self.read("README.md")

        for marker in (
            "Software, schemas, tests, and machine-executable project files are licensed\n"
            "under Apache-2.0.",
            "Documentation prose and visual assets are licensed under\nCC BY 4.0.",
            "Code snippets in documentation remain Apache-2.0 unless marked\notherwise.",
            "This citation request does not add a\nrestriction beyond the applicable licenses.",
        ):
            self.assertIn(marker, readme)

    def test_public_positioning_uses_concrete_product_language(self) -> None:
        positioning_files = (
            "AGENTS.md",
            "CITATION.cff",
            "PRODUCT.md",
            "README.md",
            "pyproject.toml",
            "docs/architecture/README.md",
            "docs/architecture/decisions/0001-layered-python-typescript-runtime.md",
            "docs/maintainers/OPEN_SOURCE_READINESS.md",
            "docs/maintainers/PUBLICATION_DECISIONS.md",
            "scripts/dashboard_assets.py",
            "src/wqb_agent_lab/__init__.py",
        )

        for relative_path in positioning_files:
            content = self.read(relative_path).lower()
            for vague_positioning in ("本地优先", "local-first", "agent-native"):
                self.assertNotIn(vague_positioning, content, relative_path)

    def test_notice_declares_scope_and_boundary_terms(self) -> None:
        notice = self.read("NOTICE")

        for marker in (
            "Software in this distribution is licensed under the Apache License, Version 2.0.",
            "Documentation prose and visual assets, including README.md, docs/**, and\n"
            "docs/assets/**, are licensed under the Creative Commons Attribution 4.0\n"
            "International License (CC BY 4.0), except where otherwise noted.",
            "Code snippets\nin documentation are licensed under Apache-2.0 unless explicitly marked.",
            "WorldQuant, WorldQuant BRAIN, and related names and marks belong to their\n"
            "respective owners.",
            "Third-party software and content remain under their respective licenses.",
            "maintainer-reviewed factual interoperability metadata",
        ):
            self.assertIn(marker, notice)

    def test_publication_decisions_close_all_gates_after_pvr_verification(self) -> None:
        decisions = self.read("docs/maintainers/PUBLICATION_DECISIONS.md")
        manifest = json.loads(self.read("release/public_snapshot_manifest.json"))

        for marker in (
            "paradoxSCH",
            "v0.1.0-alpha",
            "real alpha expressions",
            "submission-worker",
            "Private Vulnerability Reporting",
        ):
            self.assertIn(marker, decisions)
        self.assertIn("enabled=true", decisions)
        self.assertIn("GHSA-qjp9-4g3h-34jv", decisions)
        self.assertEqual([], manifest["release_blockers"])
        self.assertNotIn("third_party_asset_review", json.dumps(manifest))
        self.assertNotIn("security_contact", json.dumps(manifest))

    def test_public_author_identity_is_consistent(self) -> None:
        self.assertIn('name: "paradoxSCH"', self.read("CITATION.cff"))
        self.assertIn('{ name = "paradoxSCH" }', self.read("pyproject.toml"))
        self.assertIn("Copyright 2026 paradoxSCH", self.read("NOTICE"))
        self.assertIn(
            'repository-code: "https://github.com/paradoxSCH/wqb-agent-lab"',
            self.read("CITATION.cff"),
        )

    def test_public_license_metadata_has_no_stale_mit_declarations(self) -> None:
        public_license_metadata_files = (
            "README.md",
            "NOTICE",
            "LICENSE",
            "LICENSES/CC-BY-4.0.txt",
            "CITATION.cff",
            "pyproject.toml",
            "packages/wqb-agent-mcp/package.json",
            "packages/wqb-agent-ui/package.json",
        )
        stale_mit_declaration = re.compile(
            r"(?im)(?:license|licence)\s*[:=]\s*[\"']?MIT\b|\bMIT\s+License\b"
        )

        for relative_path in public_license_metadata_files:
            self.assertNotRegex(self.read(relative_path), stale_mit_declaration)

    def test_package_lockfiles_declare_apache_root_license(self) -> None:
        for relative_path in (
            "packages/wqb-agent-mcp/package-lock.json",
            "packages/wqb-agent-ui/package-lock.json",
        ):
            package_lock = json.loads(self.read(relative_path))
            self.assertEqual("Apache-2.0", package_lock["packages"][""]["license"])

    def test_public_package_versions_are_synchronized(self) -> None:
        pyproject = self.read("pyproject.toml")
        citation = self.read("CITATION.cff")
        mcp_package = json.loads(self.read("packages/wqb-agent-mcp/package.json"))
        ui_package = json.loads(self.read("packages/wqb-agent-ui/package.json"))
        mcp_server = self.read("packages/wqb-agent-mcp/src/server.ts")

        self.assertIn('version = "0.1.1"', pyproject)
        self.assertIn("version: 0.1.1", citation)
        self.assertEqual("0.1.1", mcp_package["version"])
        self.assertEqual("0.1.1", ui_package["version"])
        self.assertIn('version: "0.1.1"', mcp_server)

    def test_gitignore_blocks_private_runtime_artifacts_recursively(self) -> None:
        gitignore = self.read(".gitignore")

        required_patterns = (
            ".env",
            "*.pid",
            ".local/logs/",
            "*.log",
            ".local/data/runs/**",
            ".local/data/callbacks/**",
            ".local/data/memory/**",
            ".local/data/registry/**",
            "!.local/data/registry/.gitkeep",
            "output/playwright/**",
            "/run/",
            "dist/public-snapshot/",
            ".local/research/scans/**",
            ".local/research/workflows/**",
            ".claude/settings.local.json",
        )

        for pattern in required_patterns:
            self.assertIn(pattern, gitignore)

        self.assertNotIn("\nrun\n", gitignore)

    def test_pyproject_declares_package_metadata_and_optional_extras(self) -> None:
        pyproject = self.read("pyproject.toml")

        self.assertIn('name = "wqb-agent-lab"', pyproject)
        self.assertIn('requires-python = ">=3.11,<3.13"', pyproject)
        self.assertNotIn("Programming Language :: Python :: 3.10", pyproject)
        self.assertIn('"requests>=2.31"', pyproject)
        self.assertNotRegex(pyproject, re.compile(r'(?m)^\s*"wqb(?:[<>=!~].*)?"'))
        self.assertIn("[project.optional-dependencies]", pyproject)
        self.assertRegex(pyproject, re.compile(r"mcp\s*=\s*\[", re.MULTILINE))
        self.assertRegex(pyproject, re.compile(r"dev\s*=\s*\[", re.MULTILINE))
        self.assertIn('"build>=1.2"', pyproject)
        self.assertIn('"pip-audit>=2.7"', pyproject)
        self.assertNotIn("your-org", pyproject)
        self.assertIn('license = "Apache-2.0"', pyproject)
        self.assertNotIn('license = "MIT"', pyproject)
        self.assertNotIn("License :: OSI Approved :: MIT License", pyproject)
        self.assertIn('license-files = ["LICENSE", "NOTICE", "LICENSES/*.txt"]', pyproject)
        for package_path in (
            "packages/wqb-agent-mcp/package.json",
            "packages/wqb-agent-ui/package.json",
        ):
            package_json = self.read(package_path)
            self.assertIn('"license": "Apache-2.0"', package_json)
            self.assertNotIn('"license": "MIT"', package_json)
        self.assertIn('include = ["src*", "scripts*", "schemas"]', pyproject)
        self.assertIn('py-modules = ["run_scan"]', pyproject)
        self.assertIn('"schemas" = ["*.json"]', pyproject)

    def test_public_snapshot_excludes_private_recipe_and_direct_submit_sources(self) -> None:
        manifest = json.loads(self.read("release/public_snapshot_manifest.json"))
        excluded_paths = set(manifest["exclude"]["paths"])
        required_private_paths = {
            "scripts/build_behavioral_101_scan.py",
            "scripts/build_behavioral_breadth_1200_scan.py",
            "scripts/build_behavioral_foundations_1200_scan.py",
            "scripts/build_behavioral_trend_refine_scan.py",
            "scripts/build_behavioral_trend_scan.py",
            "scripts/build_budget_aligned_scan.py",
            "scripts/build_good_plus_aligned_scan.py",
            "scripts/build_post_review_scan.py",
            "scripts/manual_closeout_20260519.py",
            "scripts/submit/batch_submit_direct.py",
            "tests/test_batch_submit_direct.py",
        }

        self.assertEqual(set(), required_private_paths - excluded_paths)

    def test_public_snapshot_includes_dual_license_attribution_files(self) -> None:
        manifest = json.loads(self.read("release/public_snapshot_manifest.json"))
        required_attribution_files = {
            "CITATION.cff",
            "LICENSE",
            "LICENSES/CC-BY-4.0.txt",
            "NOTICE",
        }

        included_files = set(manifest["include"]["files"])
        required_files = set(manifest["required_files"])

        self.assertEqual(set(), required_attribution_files - included_files)
        self.assertEqual(set(), required_attribution_files - required_files)
        self.assertNotIn("NOTICE.md", included_files)
        self.assertNotIn("NOTICE.md", required_files)

    def test_ci_is_credential_free(self) -> None:
        ci = self.read(".github/workflows/ci.yml")

        self.assertIn("python -m scripts.dev check --json", ci)
        self.assertIn("python -m scripts.dev test --json", ci)
        self.assertIn("python -m scripts.dev build --json", ci)
        self.assertIn("python -m scripts.dev release-check --json", ci)
        self.assertIn("uv sync --extra dev --extra mcp --frozen", ci)
        self.assertIn("gitleaks/gitleaks-action@v2", ci)
        self.assertIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", ci)
        self.assertIn("pull-requests: read", ci)
        self.assertIn("npm ci --prefix packages/wqb-agent-mcp", ci)
        self.assertIn("npm ci --prefix packages/wqb-agent-ui", ci)
        self.assertNotIn("WQB_EMAIL", ci)
        self.assertNotIn("WQB_PASSWORD", ci)
        self.assertNotIn("submit-worker --daemon", ci)

    def test_release_workflow_publishes_verifiable_assets(self) -> None:
        workflow = self.read(".github/workflows/release.yml")

        for marker in (
            "uv run python -m scripts.dev release-check --json",
            "*.whl",
            "*.tar.gz",
            "*.cdx.json",
            "SHA256SUMS",
            "gh release create",
        ):
            self.assertIn(marker, workflow)

    def test_public_examples_disable_live_side_effect_capabilities(self) -> None:
        env_example = self.read(".env.example")
        readme = self.read("README.md")

        for marker in (
            "WQB_LIVE_SIMULATION_CAPABILITY=0",
            "WQB_LIVE_SUBMIT_CAPABILITY=0",
        ):
            self.assertIn(marker, env_example)
            self.assertIn(marker, readme)
        self.assertNotIn("WQB_AUTO_SUBMIT_ENABLED", env_example)

    def test_env_example_uses_empty_credentials_and_fail_closed_capabilities(self) -> None:
        env_example = self.read(".env.example")

        for key in (
            "WQB_EMAIL",
            "WQB_PASSWORD",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
        ):
            self.assertRegex(env_example, rf"(?m)^{key}=\s*$")
        self.assertNotIn("your_email", env_example)
        self.assertNotIn("your_password", env_example)
        self.assertNotIn("your_kimi", env_example)
        self.assertNotIn("your_deepseek", env_example)

        for line in env_example.splitlines():
            if line.startswith(("OPENAI_API_KEY=", "ANTHROPIC_API_KEY=", "GEMINI_API_KEY=")):
                self.assertEqual("", line.partition("=")[2].strip())

    def test_public_snapshot_includes_llm_provider_documentation(self) -> None:
        manifest = json.loads(self.read("release/public_snapshot_manifest.json"))

        self.assertIn("docs/user", manifest["include"]["trees"])
        self.assertIn("docs/user/LLM_PROVIDERS.md", manifest["required_files"])

    def test_public_snapshot_includes_reproducible_runtime_inputs(self) -> None:
        manifest = json.loads(self.read("release/public_snapshot_manifest.json"))

        for path in (".python-version", ".nvmrc", "pyproject.toml", "uv.lock"):
            self.assertIn(path, manifest["include"]["files"])
            self.assertIn(path, manifest["required_files"])

    def test_public_snapshot_includes_supply_chain_policy(self) -> None:
        manifest = json.loads(self.read("release/public_snapshot_manifest.json"))
        policy_path = "release/allowed_dependency_licenses.json"

        self.assertIn(policy_path, manifest["include"]["files"])
        self.assertIn(policy_path, manifest["required_files"])

    def test_readme_is_a_concise_public_project_entry_point(self) -> None:
        readme = self.read("README.md")

        required_markers = (
            "scripts/bootstrap.ps1 -Profile runtime",
            "scripts/bootstrap.sh --profile runtime",
            "scripts.dev doctor --profile runtime --json",
            "uv run wqb-engine demo",
            "configs\\examples\\production-workflow.example.json",
            "uv run wqb-engine policy.validate",
            "uv run wqb-engine llm.validate",
            "WQB_LIVE_SIMULATION_CAPABILITY=0",
            "WQB_LIVE_SUBMIT_CAPABILITY=0",
            "docs/architecture/README.md",
            "docs/user/TROUBLESHOOTING.md",
        )
        for marker in required_markers:
            self.assertIn(marker, readme)

        for maintainer_detail in (
            "## 公开快照",
            "scripts.release.export_public_snapshot",
            "scripts.checks.public_snapshot_smoke",
            "publish_ready",
            "当前私有工作仓库不应直接推送",
            "## Python / TypeScript Contract",
        ):
            self.assertNotIn(maintainer_detail, readme)

        self.assertLessEqual(len(readme.splitlines()), 160)



if __name__ == "__main__":
    unittest.main()
