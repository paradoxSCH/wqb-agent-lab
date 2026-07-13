from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts.checks.release_audit import audit_candidates, run


class ReleaseAuditTests(unittest.TestCase):
    def make_root(self) -> tempfile.TemporaryDirectory[str]:
        return tempfile.TemporaryDirectory()

    @staticmethod
    def write(root: Path, relative_path: str, content: str) -> None:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_clean_publish_candidates_pass(self) -> None:
        with self.make_root() as temp_dir:
            root = Path(temp_dir)
            self.write(
                root,
                "pyproject.toml",
                '[project]\nname = "wqb-agent-lab"\nversion = "0.1.0"\n',
            )
            self.write(
                root,
                ".env.example",
                "WQB_EMAIL=your_email@example.com\n"
                "WQB_PASSWORD=your_password\n"
                "WQB_LIVE_SUBMIT_CAPABILITY=0\n",
            )
            self.write(root, "README.md", "Not affiliated with WorldQuant.\n")

            report = audit_candidates(
                root,
                ["pyproject.toml", ".env.example", "README.md"],
            )

            self.assertTrue(report.ok)
            self.assertEqual([], report.findings)

    def test_private_runtime_candidates_are_rejected(self) -> None:
        with self.make_root() as temp_dir:
            root = Path(temp_dir)
            candidates = [
                ".env",
                "worker.pid",
                ".local/logs/loop.log",
                ".local/data/runs/live/result.json",
                ".local/research/scans/continuous-alpha/live/config.json",
                ".local/research/scans/custom/config.json",
                ".local/research/workflows/live.json",
                "worldquant_interview_defense.html",
            ]
            for path in candidates:
                self.write(root, path, "local state")

            report = audit_candidates(root, candidates)

            self.assertFalse(report.ok)
            self.assertEqual(
                {"private_artifact"},
                {finding.code for finding in report.findings},
            )
            self.assertEqual(set(candidates), {finding.path for finding in report.findings})

    def test_missing_index_path_is_not_a_publish_candidate(self) -> None:
        with self.make_root() as temp_dir:
            root = Path(temp_dir)

            report = audit_candidates(root, ["output/playwright/deleted-test.js"])

            self.assertTrue(report.ok)
            self.assertEqual(0, report.candidate_count)

    def test_placeholder_project_metadata_is_rejected(self) -> None:
        with self.make_root() as temp_dir:
            root = Path(temp_dir)
            self.write(
                root,
                "pyproject.toml",
                '[project.urls]\nHomepage = "https://github.com/your-org/wqb-agent-lab"\n',
            )

            report = audit_candidates(root, ["pyproject.toml"])

            self.assertEqual(["placeholder_metadata"], [item.code for item in report.findings])

    def test_real_looking_credential_value_is_rejected_but_examples_are_allowed(self) -> None:
        with self.make_root() as temp_dir:
            root = Path(temp_dir)
            real_looking = "".join(("real", "Password", "Value", "938475"))
            self.write(root, "configs/public.env", f"WQB_PASSWORD={real_looking}\n")
            self.write(root, ".env.example", "WQB_PASSWORD=your_password\n")

            report = audit_candidates(root, ["configs/public.env", ".env.example"])

            self.assertEqual(["credential_value"], [item.code for item in report.findings])
            self.assertEqual("configs/public.env", report.findings[0].path)
            self.assertNotIn(real_looking, report.findings[0].message)

    def test_quoted_credential_in_source_code_is_rejected(self) -> None:
        with self.make_root() as temp_dir:
            root = Path(temp_dir)
            real_looking = "".join(("source", "Credential", "Value", "573920"))
            self.write(root, "src/leak.py", f'api_key = "{real_looking}"\n')

            report = audit_candidates(root, ["src/leak.py"])

            self.assertEqual(["credential_value"], [item.code for item in report.findings])
            self.assertNotIn(real_looking, report.findings[0].message)

    def test_enabled_live_capability_in_public_example_is_rejected(self) -> None:
        with self.make_root() as temp_dir:
            root = Path(temp_dir)
            self.write(
                root,
                "configs/templates/live.env",
                "WQB_LIVE_SUBMIT_CAPABILITY=true\n",
            )

            report = audit_candidates(root, ["configs/templates/live.env"])

            self.assertEqual(["unsafe_live_default"], [item.code for item in report.findings])

    def test_live_capability_documentation_is_not_treated_as_a_default(self) -> None:
        with self.make_root() as temp_dir:
            root = Path(temp_dir)
            self.write(
                root,
                "docs/capability-spec.md",
                "Enable the capability explicitly when governance approves:\n"
                "WQB_LIVE_SUBMIT_CAPABILITY=1\n",
            )

            report = audit_candidates(root, ["docs/capability-spec.md"])

            self.assertTrue(report.ok)

    def test_json_cli_output_is_machine_readable(self) -> None:
        with self.make_root() as temp_dir:
            root = Path(temp_dir)
            self.write(root, "pyproject.toml", '[project]\nname = "wqb-agent-lab"\n')
            stdout = io.StringIO()

            exit_code = run(
                ["--root", str(root), "--json"],
                stdout=stdout,
                candidate_paths=["pyproject.toml"],
            )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(0, exit_code)
            self.assertTrue(payload["ok"])
            self.assertEqual(1, payload["candidate_count"])
            self.assertEqual([], payload["findings"])


if __name__ == "__main__":
    unittest.main()
