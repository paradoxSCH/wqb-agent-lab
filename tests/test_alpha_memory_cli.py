from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class AlphaMemoryCliTests(unittest.TestCase):
    def test_ingest_query_integrity_rebuild_and_export_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / ".local" / "data" / "runs" / "continuous-alpha"
            run_dir = runs_root / "daily-20260602"
            db_path = root / ".local" / "data" / "memory" / "alpha_memory.db"
            export_path = root / ".local" / "data" / "exports" / "alpha_memory.jsonl"
            run_dir.mkdir(parents=True)
            self._write_json(
                run_dir / "daily_budget_ledger.json",
                {
                    "daily_run_tag": "daily-20260602",
                    "daily_budget": 1000,
                    "spent_simulations": 100,
                    "current_stage": "direction_probe",
                    "stage_order": ["direction_probe"],
                },
            )

            ingest = self._run_module(
                "scripts.memory.ingest",
                "--runs-root",
                str(runs_root),
                "--db",
                str(db_path),
            )
            self.assertIn("nodes=", ingest.stdout)
            self.assertIn("edges=", ingest.stdout)

            query = self._run_module(
                "scripts.memory.query",
                "--query",
                "daily 20260602",
                "--db",
                str(db_path),
            )
            self.assertIn("daily-20260602", query.stdout)

            integrity = self._run_module("scripts.memory.integrity_check", "--db", str(db_path))
            self.assertIn("ok", integrity.stdout)

            rebuild = self._run_module("scripts.memory.rebuild_indexes", "--db", str(db_path))
            self.assertEqual(rebuild.stdout.strip(), "rebuilt")

            export = self._run_module(
                "scripts.memory.export",
                "--db",
                str(db_path),
                "--out",
                str(export_path),
            )
            self.assertIn(str(export_path), export.stdout)
            self.assertTrue(export_path.exists())

    def test_hypothesis_ledger_cli_validates_complete_draft(self) -> None:
        result = self._run_module(
            "scripts.research.hypothesis_ledger",
            "--run",
            "daily-20260602",
            "--behavior-thesis",
            "Quality value mispricing",
            "--mechanism",
            "Investors underreact to improving cashflow quality.",
            "--proxy",
            "cashflow_quality",
            "--operator-skeleton",
            "rank(ts_mean(cashflow_quality, 60)) - rank(close)",
            "--kill-condition",
            "high self-corr",
            "--success-criterion",
            "near-pass with low self-corr",
        )

        payload = json.loads(result.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["missing_fields"], [])
        self.assertIn("valid", result.stdout)

    def test_memory_eval_cli_outputs_report_json(self) -> None:
        result = self._run_module(
            "scripts.memory.eval",
            "--from",
            "2026-06-01",
            "--to",
            "2026-06-30",
            "--ablation",
            "memory-off",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["range"], ["2026-06-01", "2026-06-30"])
        self.assertEqual(payload["ablation"], "memory-off")
        self.assertIn("report", payload)
        self.assertIn("baseline", payload["report"])
        self.assertIn("hybrid", payload["report"])
        self.assertIn("delta", payload["report"])
        for variant in ("baseline", "hybrid", "delta"):
            self.assertIn("submit_ready_per_1000", payload["report"][variant])
            self.assertIn("near_pass_per_1000", payload["report"][variant])
            self.assertIn("high_self_corr_rate", payload["report"][variant])
            self.assertIn("duplicate_rate", payload["report"][variant])

    def test_read_cli_default_db_is_repo_local_not_cwd_local(self) -> None:
        repo_default_db = REPO_ROOT / ".local" / "data" / "memory" / "alpha_memory.db"
        if not repo_default_db.exists():
            self.skipTest("repo-local default memory db is absent; avoiding mutation of repo data")

        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            result = self._run_module("scripts.memory.integrity_check", cwd=cwd)

            self.assertIn("ok", result.stdout)
            self.assertFalse((cwd / ".local" / "data" / "memory" / "alpha_memory.db").exists())

    def test_query_cli_fails_when_explicit_db_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_db = Path(tmp) / "missing" / "alpha_memory.db"

            result = self._run_module(
                "scripts.memory.query",
                "--query",
                "daily 20260602",
                "--db",
                str(missing_db),
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("database does not exist", result.stderr)
            self.assertIn(str(missing_db), result.stderr)
            self.assertFalse(missing_db.exists())

    def test_export_cli_fails_when_explicit_db_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing_db = root / "missing" / "alpha_memory.db"
            export_path = root / "exports" / "memory.jsonl"

            result = self._run_module(
                "scripts.memory.export",
                "--db",
                str(missing_db),
                "--out",
                str(export_path),
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("database does not exist", result.stderr)
            self.assertIn(str(missing_db), result.stderr)
            self.assertFalse(missing_db.exists())
            self.assertFalse(export_path.exists())

    def test_integrity_and_rebuild_cli_fail_when_explicit_db_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_db = Path(tmp) / "missing" / "alpha_memory.db"

            for module in ("scripts.memory.integrity_check", "scripts.memory.rebuild_indexes"):
                with self.subTest(module=module):
                    result = self._run_module(module, "--db", str(missing_db), check=False)

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("database does not exist", result.stderr)
                    self.assertIn(str(missing_db), result.stderr)
                    self.assertFalse(missing_db.exists())

    def test_read_clis_fail_cleanly_when_db_file_is_uninitialized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty_db = root / "empty.db"
            empty_db.touch()
            export_path = root / "exports" / "memory.jsonl"
            cases = [
                ("scripts.memory.query", ["--query", "daily 20260602", "--db", str(empty_db)]),
                ("scripts.memory.integrity_check", ["--db", str(empty_db)]),
                ("scripts.memory.export", ["--db", str(empty_db), "--out", str(export_path)]),
                ("scripts.memory.rebuild_indexes", ["--db", str(empty_db)]),
            ]

            for module, args in cases:
                with self.subTest(module=module):
                    result = self._run_module(module, *args, check=False)

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("initialized alpha memory database schema", result.stderr)
                    self.assertIn(str(empty_db), result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

            self.assertFalse(export_path.exists())

    def _run_module(
        self,
        module: str,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(REPO_ROOT) if not existing_pythonpath else f"{REPO_ROOT}{os.pathsep}{existing_pythonpath}"
        return subprocess.run(
            [sys.executable, "-m", module, *args],
            cwd=cwd or REPO_ROOT,
            check=check,
            capture_output=True,
            env=env,
            text=True,
        )

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
