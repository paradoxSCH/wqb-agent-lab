from __future__ import annotations

import json
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.checks.artifact_smoke import ProcessResult, export_clean_checkout, select_wheel, smoke_wheel


class RecordingRunner:
    def __init__(self, *, fail_stage: str = "") -> None:
        self.fail_stage = fail_stage
        self.calls = []

    def __call__(self, stage, cwd: Path) -> ProcessResult:
        self.calls.append((stage, cwd))
        if stage.name == self.fail_stage:
            return ProcessResult(1, "", "failed")
        payloads = {
            "engine-help": {"ok": True, "data": {"operations": []}},
            "schemas-list": {"ok": True, "data": {"schemas": ["candidate"]}},
            "schema-digest": {"ok": True, "data": {"digest": "abc"}},
            "llm-disabled": {"ok": True, "data": {"provider": "disabled"}},
        }
        return ProcessResult(0, json.dumps(payloads.get(stage.name, {})), "")


class ArtifactSmokeTests(unittest.TestCase):
    def test_select_wheel_requires_an_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(FileNotFoundError):
                select_wheel(Path(raw))

    def test_select_wheel_uses_latest_matching_wheel(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            old = root / "wqb_agent_lab-0.1.0-py3-none-any.whl"
            new = root / "wqb_agent_lab-0.2.0-py3-none-any.whl"
            old.write_bytes(b"old")
            new.write_bytes(b"new")
            old.touch()
            new.touch()

            selected = select_wheel(root)

            self.assertEqual(new.resolve(), selected)

    def test_smoke_runs_outside_checkout_and_uses_non_editable_install(self) -> None:
        runner = RecordingRunner()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            wheel = root / "wqb_agent_lab-0.1.0-py3-none-any.whl"
            wheel.write_bytes(b"wheel")

            report = smoke_wheel(wheel, runner=runner)

        self.assertEqual("pass", report.status)
        self.assertEqual(
            ["create-venv", "install-wheel", "engine-help", "schemas-list", "schema-digest", "llm-disabled"],
            [stage.name for stage, _ in runner.calls],
        )
        install = runner.calls[1][0].command
        self.assertNotIn("-e", install)
        self.assertNotIn("--no-deps", install)
        self.assertTrue(all(cwd != Path.cwd() for _, cwd in runner.calls))

    def test_smoke_stops_on_first_failure(self) -> None:
        runner = RecordingRunner(fail_stage="schemas-list")
        with tempfile.TemporaryDirectory() as raw:
            wheel = Path(raw) / "wqb_agent_lab-0.1.0-py3-none-any.whl"
            wheel.write_bytes(b"wheel")

            report = smoke_wheel(wheel, runner=runner)

        self.assertEqual("fail", report.status)
        self.assertEqual("schemas-list", runner.calls[-1][0].name)

    def test_clean_checkout_rejects_archive_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            archive = root / "source.tar"
            with tarfile.open(archive, "w") as handle:
                info = tarfile.TarInfo("../outside.txt")
                content = b"nope"
                info.size = len(content)
                handle.addfile(info, io.BytesIO(content))

            def fake_run(*_args, **_kwargs):
                class Result:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return Result()

            from unittest.mock import patch

            with patch("scripts.checks.artifact_smoke.subprocess.run", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "unsafe path"):
                    export_clean_checkout(root, root / "checkout")


if __name__ == "__main__":
    unittest.main()
