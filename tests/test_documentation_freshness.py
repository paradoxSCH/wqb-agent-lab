from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import re
import unittest

from src.wqb_agent_lab.platform import load_operator_names


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KNOWLEDGE_FILES = (
    "README.md",
    "PRODUCT.md",
    "docs/README.md",
    "docs/architecture/README.md",
    "docs/maintainers/OPEN_SOURCE_READINESS.md",
    "docs/user/GETTING_STARTED.md",
    "docs/user/LLM_PROVIDERS.md",
    "docs/user/MIGRATING.md",
    "docs/user/RESEARCH_POLICY.md",
    "docs/user/TROUBLESHOOTING.md",
)


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class DocumentationFreshnessTests(unittest.TestCase):
    def test_current_markdown_links_resolve(self) -> None:
        current_paths = [ROOT / path for path in PUBLIC_KNOWLEDGE_FILES]
        for directory in (ROOT / "docs/user", ROOT / "docs/architecture", ROOT / "docs/maintainers"):
            current_paths.extend(directory.rglob("*.md"))

        broken: list[str] = []
        for path in sorted(set(current_paths)):
            content = path.read_text(encoding="utf-8")
            for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", content):
                target = target.strip().strip("<>").split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                if not (path.parent / target).resolve().exists():
                    broken.append(f"{path.relative_to(ROOT)} -> {target}")

        self.assertEqual([], broken)

    def test_documented_python_modules_exist(self) -> None:
        missing: list[str] = []
        for relative_path in PUBLIC_KNOWLEDGE_FILES:
            content = read(relative_path)
            for module in re.findall(r"python(?:\.exe)?\s+-m\s+([A-Za-z0-9_.]+)", content):
                if importlib.util.find_spec(module) is None:
                    missing.append(f"{relative_path}: {module}")

        self.assertEqual([], missing)

    def test_historical_docs_are_excluded_from_packages_and_public_snapshot(self) -> None:
        manifest = json.loads(read("release/public_snapshot_manifest.json"))
        package_manifest = read("MANIFEST.in")

        self.assertIn("docs/archive", manifest["exclude"]["trees"])
        self.assertNotIn("docs/archive", manifest["include"]["trees"])
        self.assertNotIn("docs/archive", package_manifest)

    def test_public_operator_examples_only_use_packaged_catalog_names(self) -> None:
        known = load_operator_names()
        platform_prefixes = ("ts_", "group_")

        for relative_path in PUBLIC_KNOWLEDGE_FILES:
            text = read(relative_path)
            calls = set(re.findall(r"(?<![.\w])([a-z][a-z0-9_]*)\s*\(", text))
            stale = sorted(name for name in calls if name.startswith(platform_prefixes) and name not in known)
            self.assertEqual([], stale, relative_path)

    def test_operator_catalog_is_packaged_below_wqb_boundary(self) -> None:
        catalog = ROOT / "src" / "wqb_agent_lab" / "platform" / "resources" / "operators.json"
        payload = json.loads(catalog.read_text(encoding="utf-8"))
        rows = payload.get("operators", []) if isinstance(payload, dict) else payload

        self.assertGreater(len(rows), 50)
        self.assertIn("ts_std_dev", load_operator_names())
        self.assertNotIn("ts_std", load_operator_names())

    def test_llm_generator_prompt_uses_current_operator_names(self) -> None:
        source = read("src/llm_template_generator.py")
        self.assertIn("ts_std_dev(field, days)", source)
        self.assertNotIn("ts_std(field, days)", source)
        self.assertIn('"ts_std_dev"', source)

    def test_public_example_is_local_first_and_submission_disabled(self) -> None:
        config = json.loads(read("configs/examples/production-workflow.example.json"))

        self.assertEqual(200, config["research_policy"]["budget"]["daily_simulation_limit"])
        self.assertNotIn("daily_budget_modes", config)
        self.assertNotIn("stage_order", config)
        self.assertNotIn("max_daily_budget", config["autonomous_loop"])
        self.assertFalse(config["auto_submit_direct"])
        self.assertFalse(config["autonomous_loop"]["auto_submit"])
        self.assertTrue(all(str(value).startswith(".local/") for value in config["paths"].values()))

    def test_public_example_uses_only_the_canonical_disabled_llm_provider(self) -> None:
        config = json.loads(read("configs/examples/production-workflow.example.json"))
        serialized = json.dumps(config, ensure_ascii=False)

        self.assertEqual(
            {"provider": "disabled"},
            config["llm_provider"],
        )
        self.assertEqual(1, serialized.count('"llm_provider"'))
        for legacy_key in ("llm_adapter", "deepseek_v4_pro", "kimi_cli"):
            self.assertNotIn(legacy_key, config)

    def test_llm_provider_docs_cover_supported_modes_and_runtime_boundaries(self) -> None:
        docs = read("docs/user/LLM_PROVIDERS.md")

        for marker in (
            '"provider": "disabled"',
            '"provider": "openai_compatible"',
            '"provider": "anthropic"',
            '"provider": "gemini"',
            '"provider": "ollama"',
            '"provider": "cli"',
            r".\.venv\Scripts\wqb-engine.exe llm.validate --config .local\research\workflows\production.json",
            r".\.venv\Scripts\wqb-engine.exe llm.show --config .local\research\workflows\production.json",
            r".\.venv\Scripts\wqb-engine.exe llm.probe --config .local\research\workflows\production.json",
            "llm_provider > llm_adapter > deepseek_v4_pro > kimi_cli > KIMI_* > disabled",
            "WQB_LIVE_SIMULATION_CAPABILITY",
            "WQB_LIVE_SUBMIT_CAPABILITY",
            ".cmd",
            ".bat",
        ):
            self.assertIn(marker, docs)

        self.assertIn("`llm.validate` 和 `llm.show` 不访问网络", docs)
        self.assertIn("不能自动生成生产 scan config", docs)

    def test_research_policy_docs_match_current_contract_and_runtime_boundary(self) -> None:
        docs = read("docs/user/RESEARCH_POLICY.md")

        for marker in (
            "daily_simulation_limit",
            "exploration_share_limit",
            "exploration_stages",
            "stage_allocations",
            "behavioral_mechanism",
            "fields",
            "kill_conditions",
            "research_policy_evaluation.json",
            "daily_budget_ledger.json",
            "agent 或本地生成器",
            "尚未自动闭合",
        ):
            self.assertIn(marker, docs)

    def test_readme_quick_start_uses_locked_uv_environment(self) -> None:
        readme = read("README.md")

        self.assertIn("scripts/bootstrap.ps1 -Profile runtime", readme)
        self.assertIn("scripts.dev doctor --profile runtime --json", readme)
        self.assertIn("uv run wqb-engine --help", readme)
        self.assertNotRegex(readme, re.compile(r"(?m)^wqb-engine\s"))

    def test_public_docs_use_current_product_boundaries(self) -> None:
        readme = read("README.md")
        product = read("PRODUCT.md")

        self.assertIn("src.wqb_agent_lab.workflow.ResearchWorkflow", readme)
        self.assertIn("src.wqb_agent_lab.platform.WQBClient", readme)
        self.assertIn("WQB_LIVE_SIMULATION_CAPABILITY", readme)
        self.assertIn("WQB_LIVE_SUBMIT_CAPABILITY", readme)
        self.assertIn("ResearchWorkflow", product)

    def test_readme_uses_current_versioned_architecture_diagram(self) -> None:
        readme = read("README.md")
        self.assertIn("docs/assets/wqb-agent-architecture-current-zh.svg", readme)
        self.assertNotIn("wqb-agent-architecture-ai-zh-vertical.png", readme)


if __name__ == "__main__":
    unittest.main()
