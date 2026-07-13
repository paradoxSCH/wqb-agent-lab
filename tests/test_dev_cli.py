from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts import dev


class RecordingRunner:
    def __init__(self, *, fail_stage: str = "", missing_tool: str = "") -> None:
        self.fail_stage = fail_stage
        self.missing_tool = missing_tool
        self.calls: list[dev.Stage] = []

    def __call__(self, stage: dev.Stage, cwd: Path) -> dev.ProcessResult:
        self.calls.append(stage)
        if stage.name == self.missing_tool:
            raise FileNotFoundError(stage.command[0])
        return dev.ProcessResult(
            returncode=1 if stage.name == self.fail_stage else 0,
            stdout="password=super-secret" if stage.name == self.fail_stage else "ok",
            stderr="",
        )


class DevCLITests(unittest.TestCase):
    def test_invalid_command_returns_two(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = dev.run(["unknown"], stdout=stdout, stderr=stderr)

        self.assertEqual(2, exit_code)
        self.assertIn("invalid command", stderr.getvalue().lower())

    def test_check_runs_canonical_stages_in_order(self) -> None:
        runner = RecordingRunner()

        exit_code = dev.run(["check"], runner=runner, stdout=io.StringIO(), stderr=io.StringIO())

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [
                "environment-doctor",
                "python-ruff",
                "python-compile",
                "mcp-typecheck",
                "ui-typecheck",
                "schema-tests",
            ],
            [stage.name for stage in runner.calls],
        )
        self.assertTrue(all(isinstance(stage.command, tuple) for stage in runner.calls))

    def test_failed_stage_returns_one_and_stops(self) -> None:
        runner = RecordingRunner(fail_stage="python-compile")
        stderr = io.StringIO()

        exit_code = dev.run(["check"], runner=runner, stdout=io.StringIO(), stderr=stderr)

        self.assertEqual(1, exit_code)
        self.assertEqual(
            ["environment-doctor", "python-ruff", "python-compile"],
            [stage.name for stage in runner.calls],
        )
        self.assertNotIn("super-secret", stderr.getvalue())
        self.assertIn("[REDACTED]", stderr.getvalue())

    def test_missing_tool_returns_two(self) -> None:
        runner = RecordingRunner(missing_tool="mcp-typecheck")

        exit_code = dev.run(["check"], runner=runner, stdout=io.StringIO(), stderr=io.StringIO())

        self.assertEqual(2, exit_code)

    def test_doctor_returns_machine_readable_report(self) -> None:
        stdout = io.StringIO()

        with patch(
            "scripts.onboarding.build_doctor_report",
            return_value={
                "command": "doctor",
                "status": "blocked",
                "profile": "full",
                "checks": [],
                "actions": [{"check_id": "node", "command": "install node"}],
                "next_command": "install node",
            },
        ):
            exit_code = dev.run(
                ["doctor", "--profile", "full", "--json"],
                stdout=stdout,
                stderr=io.StringIO(),
            )

        self.assertEqual(2, exit_code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual("blocked", payload["status"])
        self.assertEqual("full", payload["profile"])

    def test_json_output_contains_one_final_object(self) -> None:
        stdout = io.StringIO()

        exit_code = dev.run(["build", "--json"], runner=RecordingRunner(), stdout=stdout, stderr=io.StringIO())

        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual("build", payload["command"])
        self.assertEqual("pass", payload["status"])
        self.assertEqual("", payload["failed_stage"])
        self.assertGreaterEqual(payload["duration_seconds"], 0)

    def test_json_report_is_safe_for_non_utf8_console_encodings(self) -> None:
        stdout = io.StringIO()
        report = dev.DevReport(
            command="release-check",
            status="fail",
            duration_seconds=0,
            message="invalid output \ufffd with Chinese \u4e2d\u6587",
        )

        dev._write_report(report, json_output=True, stdout=stdout, stderr=io.StringIO())

        encoded = stdout.getvalue().encode("ascii")
        self.assertEqual(report.message, json.loads(encoded)["message"])

    def test_release_check_composes_commands_and_release_stages(self) -> None:
        runner = RecordingRunner()

        exit_code = dev.run(["release-check"], runner=runner, stdout=io.StringIO(), stderr=io.StringIO())

        self.assertEqual(0, exit_code)
        names = [stage.name for stage in runner.calls]
        self.assertEqual(1, names.count("python-ruff"))
        self.assertIn("python-tests", names)
        self.assertIn("python-build", names)
        self.assertIn("release-audit", names)
        self.assertIn("public-snapshot-check", names)
        self.assertIn("public-snapshot-smoke", names)
        self.assertIn("public-snapshot-secret-scan", names)
        self.assertIn("supply-chain-reports", names)

    def test_release_check_reports_pass_after_machine_and_manual_gates_close(self) -> None:
        stdout = io.StringIO()

        exit_code = dev.run(
            ["release-check", "--json"],
            runner=RecordingRunner(),
            stdout=stdout,
            stderr=io.StringIO(),
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual("pass", payload["status"])
        self.assertEqual([], payload["manual_gates"])

    def test_skip_completed_accepts_only_known_stage_names(self) -> None:
        runner = RecordingRunner()

        exit_code = dev.run(
            ["release-check", "--skip-completed", "python-ruff,python-tests"],
            runner=runner,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

        self.assertEqual(0, exit_code)
        names = [stage.name for stage in runner.calls]
        self.assertNotIn("python-ruff", names)
        self.assertNotIn("python-tests", names)

        invalid_exit = dev.run(
            ["release-check", "--skip-completed", "made-up"],
            runner=runner,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        self.assertEqual(2, invalid_exit)


if __name__ == "__main__":
    unittest.main()
