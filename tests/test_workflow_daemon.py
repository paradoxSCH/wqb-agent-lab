from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from wqb_agent_lab.workflow.callbacks import emit_agent_callback
from wqb_agent_lab.workflow.daemon import (
    LaunchResult,
    ProcessSpec,
    _notify_completion,
    build_dashboard_spec,
    build_workflow_spec,
    check_process_status,
    ensure_process,
    resolve_autonomous_run_identity,
    precheck_wqb_session,
    resolve_autonomous_run_date,
    run_completion_hooks,
)
from scripts.run.daemon import (
    build_launch_specs,
    launch_evaluation_worker,
    main as launch_daemon_main,
    require_launch_capabilities,
    supervise_once,
)


class WorkflowDaemonTests(unittest.TestCase):
    def test_live_scan_launch_requires_simulation_capability(self) -> None:
        from wqb_agent_lab.governance.side_effects import SideEffectCapabilityDisabled

        with self.assertRaises(SideEffectCapabilityDisabled) as raised:
            require_launch_capabilities(execute_scans=True, auto_submit=False, env={})

        self.assertEqual(raised.exception.decision.operation, "simulation")

    def test_auto_submit_launch_requires_both_capabilities_when_scans_are_live(self) -> None:
        from wqb_agent_lab.governance.side_effects import SideEffectCapabilityDisabled

        with self.assertRaises(SideEffectCapabilityDisabled) as raised:
            require_launch_capabilities(
                execute_scans=True,
                auto_submit=True,
                env={"WQB_LIVE_SIMULATION_CAPABILITY": "1"},
            )

        self.assertEqual(raised.exception.decision.operation, "submission")

    def test_dry_daemon_launch_needs_no_live_capability(self) -> None:
        decisions = require_launch_capabilities(execute_scans=False, auto_submit=False, env={})

        self.assertEqual(decisions, [])

    def test_emit_agent_callback_writes_outbox_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = emit_agent_callback(
                root,
                "stage_scan_complete",
                {"run_tag": "daily-run", "stage": "direction_probe", "recommended_control_action": "continue_mining"},
                now=datetime(2026, 7, 4, 23, 30, 0),
            )

            self.assertIsNotNone(result.event_path)
            assert result.event_path is not None
            event = json.loads(result.event_path.read_text(encoding="utf-8"))
            self.assertEqual(event["event_type"], "stage_scan_complete")
            self.assertEqual(event["payload"]["run_tag"], "daily-run")
            self.assertIn(".local/data/callbacks/wqb-agent", result.event_path.as_posix())

    def test_build_workflow_spec_uses_supplied_date_without_hardcoded_legacy_date(self) -> None:
        spec = build_workflow_spec(
            python_exe=Path("python.exe"),
            workflow_config=Path(".local/research/workflows/continuous-alpha/deepseek_v4_pro_daily_budget.json"),
            run_date=date(2026, 7, 4),
            budget_mode="standard",
            poll_seconds=600,
            execute_scans=True,
            stop_after_summary=True,
        )

        command = " ".join(spec.command)

        self.assertIn("--date 2026-07-04", command)
        self.assertNotIn("2026-06-01", command)
        self.assertIn("--daemon", spec.command)
        self.assertIn("--execute-scans", spec.command)
        self.assertIn("--stop-after-summary", spec.command)

    def test_build_dashboard_spec_uses_requested_host_and_port(self) -> None:
        spec = build_dashboard_spec(python_exe=Path("python.exe"), host="127.0.0.1", port=8765)

        self.assertEqual(spec.pid_file.name, ".dashboard.pid")
        self.assertIn("--host", spec.command)
        self.assertIn("127.0.0.1", spec.command)
        self.assertIn("--port", spec.command)
        self.assertIn("8765", spec.command)

    def test_resolve_autonomous_run_date_advances_past_completed_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "deepseek-v4-pro-daily-budget-20260704"
            self._write_json(
                run_dir / "daily_budget_ledger.json",
                {
                    "daily_run_tag": "deepseek-v4-pro-daily-budget-20260704",
                    "date": "2026-07-04",
                    "daily_budget": 1000,
                    "spent_simulations": 1000,
                    "remaining_simulations_after_commitments": 0,
                    "last_budget_complete_report": "summary.md",
                },
            )

            resolved = resolve_autonomous_run_date(
                root,
                requested_date=date(2026, 7, 4),
                run_tag_prefix="deepseek-v4-pro-daily-budget",
            )

            self.assertEqual(resolved, date(2026, 7, 5))

    def test_resolve_autonomous_run_identity_creates_same_day_cycle_when_requested_run_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "deepseek-v4-pro-daily-budget-20260704"
            self._write_json(
                run_dir / "daily_budget_ledger.json",
                {
                    "daily_run_tag": "deepseek-v4-pro-daily-budget-20260704",
                    "daily_budget": 1000,
                    "spent_simulations": 1000,
                    "remaining_simulations_after_commitments": 0,
                    "last_budget_complete_report": "summary.md",
                },
            )

            identity = resolve_autonomous_run_identity(
                root,
                requested_date=date(2026, 7, 4),
                run_tag_prefix="deepseek-v4-pro-daily-budget",
            )

            self.assertEqual(identity["run_date"], date(2026, 7, 4))
            self.assertEqual(identity["run_tag_prefix"], "deepseek-v4-pro-daily-budget-cycle2")

    def test_build_launch_specs_uses_runtime_config_for_autonomous_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".local" / "research" / "workflows" / "continuous-alpha" / "deepseek_v4_pro_daily_budget.json"
            self._write_json(config_path, {"daily_run_tag_prefix": "deepseek-v4-pro-daily-budget"})
            self._write_json(
                root / ".local" / "data" / "runs" / "continuous-alpha" / "deepseek-v4-pro-daily-budget-20260704" / "daily_budget_ledger.json",
                {
                    "daily_budget": 1000,
                    "spent_simulations": 1000,
                    "remaining_simulations_after_commitments": 0,
                    "last_budget_complete_report": "summary.md",
                },
            )

            workflow_spec, _dashboard_spec, resolved_date = build_launch_specs(
                root=root,
                workflow_config=Path(".local/research/workflows/continuous-alpha/deepseek_v4_pro_daily_budget.json"),
                run_date=date(2026, 7, 4),
                budget_mode="standard",
                poll_seconds=900,
                execute_scans=True,
                dashboard_host="127.0.0.1",
                dashboard_port=8765,
                autonomous_loop=True,
                auto_submit=True,
                stop_after_summary=False,
            )

            command = " ".join(workflow_spec.command)
            self.assertEqual(resolved_date, date(2026, 7, 4))
            self.assertIn("runtime/deepseek-v4-pro-daily-budget-cycle2.json", command)

    def test_launch_evaluation_worker_starts_background_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launches: list[dict[str, object]] = []

            class FakeProcess:
                pid = 4321

            result = launch_evaluation_worker(
                root,
                popen=lambda command, **kwargs: launches.append({"command": command, **kwargs}) or FakeProcess(),
            )

            self.assertEqual(result, "evaluation_worker_started pid=4321")
            command = launches[0]["command"]
            self.assertIn("scripts.workers.evaluation", command)
            self.assertIn("--once", command)

    def test_launch_evaluation_worker_skips_when_latest_completed_run_already_evaluated_recently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "run-a"
            run_dir.mkdir(parents=True)
            (run_dir / "daily_budget_ledger.json").write_text(
                json.dumps({
                    "daily_run_tag": "run-a",
                    "last_budget_complete_report": ".local/data/runs/continuous-alpha/run-a/submit_summary_budget_complete.md",
                }),
                encoding="utf-8",
            )
            state_path = root / ".local" / "data" / "evaluations" / "evaluation_worker_state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps({
                    "status": "no_completed_evaluation",
                    "run_tag": "run-a",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "message": "already evaluated",
                }),
                encoding="utf-8",
            )
            launches: list[dict[str, object]] = []

            result = launch_evaluation_worker(
                root,
                popen=lambda command, **kwargs: launches.append({"command": command, **kwargs}),
            )

            self.assertEqual(result, "evaluation_worker_skipped fresh_state run_tag=run-a")
            self.assertEqual(launches, [])

    def test_supervise_once_isolates_evaluation_worker_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow_spec = ProcessSpec(
                name="workflow",
                command=["python", "-m", "scripts.run.workflow"],
                pid_file=Path(".workflow.pid"),
                log_file=Path(".local/logs/workflow.log"),
                expected_command_tokens=["scripts.run.workflow"],
            )
            dashboard_spec = ProcessSpec(
                name="dashboard",
                command=["python", "-m", "scripts.run.dashboard"],
                pid_file=Path(".dashboard.pid"),
                log_file=Path(".local/logs/dashboard.log"),
                expected_command_tokens=["scripts.run.dashboard"],
            )
            (root / workflow_spec.pid_file).write_text("111", encoding="utf-8")
            (root / dashboard_spec.pid_file).write_text("222", encoding="utf-8")

            messages = supervise_once(
                root,
                workflow_spec,
                dashboard_spec,
                inspector=lambda pid, _tokens: pid in {111, 222},
                evaluation_launcher=lambda _root: (_ for _ in ()).throw(RuntimeError("evaluation boom")),
            )

            self.assertIn("workflow: running pid=111", messages)
            self.assertIn("dashboard: running pid=222", messages)
            self.assertTrue(any(message.startswith("evaluation_worker_failed:") for message in messages))

    def test_ensure_process_skips_launch_when_pid_matches_expected_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = ProcessSpec(
                name="workflow",
                command=["python", "-m", "scripts.run.workflow"],
                pid_file=Path(".workflow.pid"),
                log_file=Path(".local/logs/workflow.log"),
                expected_command_tokens=["scripts.run.workflow"],
            )
            (root / spec.pid_file).write_text("123", encoding="utf-8")
            launches: list[ProcessSpec] = []

            result = ensure_process(
                root,
                spec,
                inspector=lambda pid, tokens: pid == 123 and tokens == ["scripts.run.workflow"],
                launcher=lambda _root, launch_spec: launches.append(launch_spec) or LaunchResult(pid=456, launched=True),
            )

            self.assertFalse(result.launched)
            self.assertEqual(result.pid, 123)
            self.assertEqual(launches, [])

    def test_ensure_process_restarts_when_pid_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = ProcessSpec(
                name="workflow",
                command=["python", "-m", "scripts.run.workflow"],
                pid_file=Path(".workflow.pid"),
                log_file=Path(".local/logs/workflow.log"),
                expected_command_tokens=["scripts.run.workflow"],
            )
            (root / spec.pid_file).write_text("123", encoding="utf-8")

            result = ensure_process(
                root,
                spec,
                inspector=lambda _pid, _tokens: False,
                launcher=lambda _root, _spec: LaunchResult(pid=456, launched=True),
            )

            self.assertTrue(result.launched)
            self.assertEqual(result.pid, 456)
            self.assertEqual((root / spec.pid_file).read_text(encoding="utf-8"), "456")

    def test_check_process_status_marks_running_and_dead(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = ProcessSpec(
                name="dashboard",
                command=["python", "-m", "scripts.run.dashboard"],
                pid_file=Path(".dashboard.pid"),
                log_file=Path(".local/logs/dashboard.log"),
                expected_command_tokens=["scripts.run.dashboard"],
            )
            (root / spec.pid_file).write_text("789", encoding="utf-8")

            running = check_process_status(root, spec, inspector=lambda pid, _tokens: pid == 789)
            dead = check_process_status(root, spec, inspector=lambda _pid, _tokens: False)

            self.assertTrue(running.running)
            self.assertFalse(dead.running)
            self.assertEqual(dead.pid, 789)

    def test_precheck_wqb_session_returns_ok_when_validate_session_passes(self) -> None:
        class FakeSession:
            def validate_session(self) -> bool:
                return True

        result = precheck_wqb_session(
            config_loader=lambda: object(),
            session_factory=lambda _config: FakeSession(),
        )

        self.assertTrue(result.ok)
        self.assertIsNone(result.error)

    def test_precheck_wqb_session_sanitizes_failure(self) -> None:
        result = precheck_wqb_session(
            config_loader=lambda: object(),
            session_factory=lambda _config: (_ for _ in ()).throw(RuntimeError("password=secret-token")),
        )

        self.assertFalse(result.ok)
        self.assertNotIn("secret-token", result.error or "")
        self.assertIn("session precheck failed", result.error or "")

    def test_precheck_wqb_session_rejects_empty_and_placeholder_credentials_before_session_factory(self) -> None:
        for email, password in (("", ""), ("your_email@example.com", "your_password")):
            with self.subTest(email=email):
                calls: list[object] = []
                result = precheck_wqb_session(
                    config_loader=lambda: SimpleNamespace(email=email, password=password),
                    session_factory=lambda config: calls.append(config),
                )

                self.assertFalse(result.ok)
                self.assertIn("credentials", result.error or "")
                self.assertEqual([], calls)

    def test_public_launcher_requires_research_policy_before_building_specs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "workflow.json"
            self._write_json(config_path, {"daily_run_tag_prefix": "legacy-only"})
            args = SimpleNamespace(
                workspace_root=str(root),
                workflow_config=str(config_path),
                date="2026-07-12",
                budget_mode="standard",
                poll_seconds=900,
                dashboard_host="127.0.0.1",
                dashboard_port=8765,
                skip_session_precheck=True,
                no_execute_scans=True,
                autonomous_loop=False,
                auto_submit=False,
                stop_after_summary=False,
                once=True,
                watchdog_seconds=300,
            )

            with patch("scripts.run.daemon.parse_args", return_value=args), patch(
                "scripts.run.daemon.build_launch_specs",
                side_effect=AssertionError("launcher must fail before building process specs"),
            ):
                exit_code = launch_daemon_main()

            self.assertEqual(2, exit_code)

    def test_completion_hooks_run_evaluation_once_and_notify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "daily-run"
            run_dir.mkdir(parents=True)
            ledger_path = run_dir / "daily_budget_ledger.json"
            self._write_json(
                ledger_path,
                {
                    "daily_run_tag": "daily-run",
                    "date": "2026-07-04",
                    "daily_budget": 1000,
                    "spent_simulations": 1000,
                    "remaining_simulations_after_commitments": 0,
                    "last_budget_complete_report": ".local/data/runs/continuous-alpha/daily-run/summary.md",
                },
            )
            calls: list[list[str]] = []
            notices: list[dict[str, object]] = []

            result = run_completion_hooks(
                root,
                now=datetime(2026, 7, 4, 23, 0, 0),
                command_runner=lambda command: calls.append(command) or 0,
                notifier=lambda payload: notices.append(payload),
            )

            updated = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertTrue(result.evaluation_ran)
            self.assertEqual(len(calls), 3)
            self.assertIn("scripts.evaluation.agent_ablation", calls[0])
            self.assertIn("daemon_post_complete_evaluation_at", updated)
            self.assertEqual(notices[0]["run_tag"], "daily-run")

            second = run_completion_hooks(
                root,
                now=datetime(2026, 7, 4, 23, 5, 0),
                command_runner=lambda command: calls.append(command) or 0,
                notifier=lambda payload: notices.append(payload),
            )

            self.assertFalse(second.evaluation_ran)
            self.assertEqual(len(calls), 3)

    def test_notify_completion_never_sends_email_even_when_env_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WQB_NOTIFY_EMAIL_ENABLED": "1",
                "WQB_NOTIFY_EMAIL_TO": "notify@example.com",
                "WQB_NOTIFY_EMAIL_FROM": "from@example.com",
                "WQB_SMTP_HOST": "smtp.example.com",
            },
            clear=False,
        ), patch("smtplib.SMTP", side_effect=AssertionError("SMTP should not be called")), patch(
            "smtplib.SMTP_SSL",
            side_effect=AssertionError("SMTP_SSL should not be called"),
        ):
            _notify_completion({"run_tag": "daily-run", "verdict": "watch", "comparison_type": "observational", "report_path": "report.md"})

    def test_completion_hooks_run_full_self_evolving_chain_and_guardrail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "daily-run"
            run_dir.mkdir(parents=True)
            ledger_path = run_dir / "daily_budget_ledger.json"
            self._write_json(
                ledger_path,
                {
                    "daily_run_tag": "daily-run",
                    "date": "2026-07-04",
                    "daily_budget": 1000,
                    "spent_simulations": 1000,
                    "remaining_simulations_after_commitments": 0,
                    "last_budget_complete_report": ".local/data/runs/continuous-alpha/daily-run/summary.md",
                },
            )
            self._write_json(
                run_dir / "output_evaluation_report.json",
                {
                    "status_counts": {"block": 2},
                    "budget_saved_estimate": 25,
                },
            )
            self._write_json(
                run_dir / "policy_effectiveness_report.json",
                {
                    "policies": [
                        {"diagnosis_type": "weak_behavior_proxy", "low_value_rate": 0.95, "simulations_spent": 40}
                    ]
                },
            )
            calls: list[list[str]] = []

            result = run_completion_hooks(
                root,
                now=datetime(2026, 7, 4, 23, 0, 0),
                command_runner=lambda command: calls.append(command) or 0,
                notifier=lambda _payload: None,
            )

            modules = [" ".join(call) for call in calls]
            self.assertTrue(result.evaluation_ran)
            self.assertTrue(any("scripts.evaluation.agent_ablation" in item for item in modules))
            self.assertTrue(any("scripts.evaluation.output_artifacts" in item for item in modules))
            self.assertTrue(any("scripts.evaluation.policy_effectiveness" in item for item in modules))
            guardrail = json.loads((run_dir / "daemon_guardrail_state.json").read_text(encoding="utf-8"))
            updated = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertTrue(guardrail["pause_next_run"])
            self.assertIn("daemon_guardrail_state", updated)
            self.assertIn("memory_governance_report", updated)
            self.assertIn("daemon_callback_event", updated)
            callback_path = root / updated["daemon_callback_event"]
            callback = json.loads(callback_path.read_text(encoding="utf-8"))
            self.assertEqual(callback["event_type"], "run_evaluation_complete")
            self.assertTrue(callback["payload"]["guardrail_state"]["pause_next_run"])

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
