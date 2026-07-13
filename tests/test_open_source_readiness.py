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
            "本地优先",
            "agent-native",
            "WorldQuant BRAIN",
            "Not affiliated with WorldQuant",
            "默认不会自动提交",
            "uv run python -m scripts.dev check",
            "scripts.dev doctor --profile runtime --json",
            "wqb-agent-lab",
            "uv run wqb-engine --help",
            "packages/wqb-agent-mcp",
            "packages/wqb-agent-ui",
            "scripts.checks.public_snapshot_smoke",
            "publish_ready",
            "Apache-2.0",
            "CC BY 4.0",
            "CITATION.cff",
        )

        for marker in required_markers:
            self.assertIn(marker, readme)
        self.assertNotIn("## License\n\nMIT. See [LICENSE](LICENSE).", readme)

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
        self.assertIn('"wqb==0.2.5"', pyproject)
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
        self.assertIn("npm ci --prefix packages/wqb-agent-mcp", ci)
        self.assertIn("npm ci --prefix packages/wqb-agent-ui", ci)
        self.assertNotIn("WQB_EMAIL", ci)
        self.assertNotIn("WQB_PASSWORD", ci)
        self.assertNotIn("submit-worker --daemon", ci)

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

    def test_readme_documents_clean_clone_research_policy_journey(self) -> None:
        readme = self.read("README.md")

        required_markers = (
            r"copy configs\examples\production-workflow.example.json .local\research\workflows\production.json",
            r".\.venv\Scripts\wqb-engine.exe policy.validate --config .local\research\workflows\production.json",
            r".\.venv\Scripts\wqb-engine.exe policy.show --config .local\research\workflows\production.json",
            '"daily_simulation_limit": 20',
            '"direction_probe": 8',
            '"scale_winners": 8',
            '"holdout": 4',
            "research_policy_evaluation.json",
            "behavioral_mechanism",
            "kill_conditions",
            "WQB_LIVE_SIMULATION_CAPABILITY=0",
            "WQB_LIVE_SUBMIT_CAPABILITY=0",
            "scripts.launch_daemon",
        )
        for marker in required_markers:
            self.assertIn(marker, readme)



if __name__ == "__main__":
    unittest.main()
