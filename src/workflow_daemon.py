from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence

from src.agent_callbacks import emit_agent_callback


RUNS_ROOT = Path(".local/data/runs/continuous-alpha")
EVALUATIONS_ROOT = Path(".local/data/evaluations")


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    command: list[str]
    pid_file: Path
    log_file: Path
    expected_command_tokens: list[str]


@dataclass(frozen=True)
class LaunchResult:
    pid: int | None
    launched: bool
    message: str = ""


@dataclass(frozen=True)
class ProcessStatus:
    name: str
    pid: int | None
    running: bool
    pid_file: Path
    log_file: Path
    message: str = ""


@dataclass(frozen=True)
class SessionPrecheckResult:
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class CompletionHookResult:
    evaluation_ran: bool
    run_tag: str | None = None
    report_path: str | None = None
    message: str = ""


Inspector = Callable[[int, Sequence[str]], bool]
Launcher = Callable[[Path, ProcessSpec], LaunchResult]
CommandRunner = Callable[[list[str]], int]
Notifier = Callable[[dict[str, object]], None]


def build_workflow_spec(
    *,
    python_exe: Path,
    workflow_config: Path,
    run_date: date,
    budget_mode: str,
    poll_seconds: int,
    execute_scans: bool,
    stop_after_summary: bool = False,
) -> ProcessSpec:
    command = [
        str(python_exe),
        "-m",
        "scripts.run.workflow",
        "--workflow-config",
        workflow_config.as_posix(),
        "--date",
        run_date.isoformat(),
        "--budget-mode",
        budget_mode,
        "--daemon",
        "--poll-seconds",
        str(poll_seconds),
    ]
    if execute_scans:
        command.append("--execute-scans")
    if stop_after_summary:
        command.append("--stop-after-summary")
    return ProcessSpec(
        name="workflow",
        command=command,
        pid_file=Path(".workflow.pid"),
        log_file=Path("logs") / f"workflow_{run_date.strftime('%Y%m%d')}.log",
        expected_command_tokens=["scripts.run.workflow"],
    )


def build_dashboard_spec(*, python_exe: Path, host: str, port: int) -> ProcessSpec:
    command = [
        str(python_exe),
        "-m",
        "scripts.daily_workflow_dashboard",
        "--host",
        host,
        "--port",
        str(port),
    ]
    return ProcessSpec(
        name="dashboard",
        command=command,
        pid_file=Path(".dashboard.pid"),
        log_file=Path("logs") / "dashboard.log",
        expected_command_tokens=["scripts.daily_workflow_dashboard"],
    )


def resolve_autonomous_run_date(
    root: Path | str,
    *,
    requested_date: date,
    run_tag_prefix: str,
    runs_root: Path = RUNS_ROOT,
) -> date:
    workspace = Path(root)
    current = requested_date
    for _ in range(366):
        run_tag = f"{run_tag_prefix}-{current.strftime('%Y%m%d')}"
        ledger = _read_json(workspace / runs_root / run_tag / "daily_budget_ledger.json", {})
        if not isinstance(ledger, dict) or not _ledger_completed(ledger):
            return current
        current = current + timedelta(days=1)
    return current


def resolve_autonomous_run_identity(
    root: Path | str,
    *,
    requested_date: date,
    run_tag_prefix: str,
    runs_root: Path = RUNS_ROOT,
) -> dict[str, Any]:
    workspace = Path(root)
    date_token = requested_date.strftime("%Y%m%d")
    for index in range(1, 100):
        prefix = run_tag_prefix if index == 1 else f"{run_tag_prefix}-cycle{index}"
        run_tag = f"{prefix}-{date_token}"
        ledger = _read_json(workspace / runs_root / run_tag / "daily_budget_ledger.json", {})
        if not isinstance(ledger, dict) or not _ledger_completed(ledger):
            return {"run_date": requested_date, "run_tag_prefix": prefix, "run_tag": run_tag, "cycle_index": index}
    fallback = f"{run_tag_prefix}-cycle100"
    return {
        "run_date": requested_date,
        "run_tag_prefix": fallback,
        "run_tag": f"{fallback}-{date_token}",
        "cycle_index": 100,
    }


def ensure_process(
    root: Path | str,
    spec: ProcessSpec,
    *,
    inspector: Inspector = None,  # type: ignore[assignment]
    launcher: Launcher = None,  # type: ignore[assignment]
) -> LaunchResult:
    workspace = Path(root)
    inspector = inspector or is_pid_running
    launcher = launcher or launch_process
    pid_path = workspace / spec.pid_file
    existing_pid = _read_pid(pid_path)
    if existing_pid is not None and inspector(existing_pid, spec.expected_command_tokens):
        return LaunchResult(pid=existing_pid, launched=False, message=f"{spec.name} already running")

    result = launcher(workspace, spec)
    if result.pid is not None:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(result.pid), encoding="utf-8")
    return result


def check_process_status(
    root: Path | str,
    spec: ProcessSpec,
    *,
    inspector: Inspector = None,  # type: ignore[assignment]
) -> ProcessStatus:
    workspace = Path(root)
    inspector = inspector or is_pid_running
    pid_path = workspace / spec.pid_file
    pid = _read_pid(pid_path)
    running = bool(pid is not None and inspector(pid, spec.expected_command_tokens))
    return ProcessStatus(
        name=spec.name,
        pid=pid,
        running=running,
        pid_file=pid_path,
        log_file=workspace / spec.log_file,
        message="running" if running else "not running",
    )


def precheck_wqb_session(
    *,
    config_loader: Callable[[], Any] | None = None,
    session_factory: Callable[[Any], Any] | None = None,
) -> SessionPrecheckResult:
    try:
        if config_loader is None:
            from src.config import load_config

            config_loader = load_config
        if session_factory is None:
            from src.session import create_brain_session

            session_factory = create_brain_session
        config = config_loader()
        credential_error = _credential_precheck_error(config)
        if credential_error:
            return SessionPrecheckResult(ok=False, error=f"session precheck failed: {credential_error}")
        session = session_factory(config)
        if not bool(session.validate_session()):
            return SessionPrecheckResult(ok=False, error="session precheck failed: validation returned false")
        return SessionPrecheckResult(ok=True)
    except Exception as exc:
        return SessionPrecheckResult(ok=False, error=f"session precheck failed: {_sanitize_error(str(exc))}")


def _credential_precheck_error(config: Any) -> str | None:
    missing = object()
    if isinstance(config, dict):
        email = config.get("email", missing)
        password = config.get("password", missing)
    else:
        email = getattr(config, "email", missing)
        password = getattr(config, "password", missing)
    if email is missing and password is missing:
        return None
    values = (str(email or "").strip().lower(), str(password or "").strip().lower())
    placeholders = {
        "your_email@example.com",
        "your_password",
        "your_wqb_email",
        "your_wqb_password",
    }
    if any(not value or value in placeholders or value.startswith("your_") for value in values):
        return "WQB credentials are missing or still use placeholder values"
    return None


def run_completion_hooks(
    root: Path | str,
    *,
    now: datetime | None = None,
    command_runner: CommandRunner | None = None,
    notifier: Notifier | None = None,
    runs_root: Path = RUNS_ROOT,
    evaluations_root: Path = EVALUATIONS_ROOT,
) -> CompletionHookResult:
    workspace = Path(root)
    now = now or datetime.now()
    command_runner = command_runner or _run_command
    notifier = notifier or _notify_completion
    ledger_path, ledger = _latest_completed_ledger(workspace / runs_root)
    if ledger_path is None or ledger is None:
        return CompletionHookResult(evaluation_ran=False, message="no completed run")
    if ledger.get("daemon_post_complete_evaluation_at"):
        return CompletionHookResult(
            evaluation_ran=False,
            run_tag=str(ledger.get("daily_run_tag") or ledger_path.parent.name),
            message="already evaluated",
        )

    output_dir = workspace / evaluations_root / "latest-ablation-suite"
    command = [
        sys.executable,
        "-m",
        "scripts.evaluate_agent_ablation",
        "--auto-runs-root",
        (workspace / runs_root).as_posix(),
        "--suite-output-dir",
        output_dir.as_posix(),
        "--allow-observational",
    ]
    exit_code = command_runner(command)
    if exit_code != 0:
        return CompletionHookResult(
            evaluation_ran=False,
            run_tag=str(ledger.get("daily_run_tag") or ledger_path.parent.name),
            message=f"evaluation failed exit={exit_code}",
        )

    run_dir = ledger_path.parent
    chained_commands = [
        [sys.executable, "-m", "scripts.evaluate_output_artifacts", "--run-dir", run_dir.as_posix()],
        [sys.executable, "-m", "scripts.evaluate_policy_effectiveness", "--run-dir", run_dir.as_posix()],
    ]
    for chained in chained_commands:
        chained_exit = command_runner(chained)
        if chained_exit != 0:
            return CompletionHookResult(
                evaluation_ran=False,
                run_tag=str(ledger.get("daily_run_tag") or ledger_path.parent.name),
                message=f"evaluation failed exit={chained_exit}",
            )

    from src.memory_governance import write_memory_governance_report

    memory_report_path = write_memory_governance_report(run_dir)
    guardrail_path = _write_guardrail_state(run_dir)
    report_path = output_dir / "ablation_report.json"
    report = _read_json(report_path, {})
    ledger["daemon_post_complete_evaluation_at"] = now.isoformat(timespec="seconds")
    ledger["daemon_post_complete_evaluation_report"] = _relative(report_path, workspace)
    ledger["output_evaluation_report"] = _relative(run_dir / "output_evaluation_report.json", workspace)
    ledger["policy_effectiveness_report"] = _relative(run_dir / "policy_effectiveness_report.json", workspace)
    ledger["memory_governance_report"] = _relative(memory_report_path, workspace)
    ledger["daemon_guardrail_state"] = _relative(guardrail_path, workspace)
    _write_json(ledger_path, ledger)
    payload = {
        "run_tag": str(ledger.get("daily_run_tag") or ledger_path.parent.name),
        "verdict": report.get("verdict", "unknown") if isinstance(report, dict) else "unknown",
        "comparison_type": ((report.get("fairness") or {}).get("comparison_type") if isinstance(report, dict) else "unknown"),
        "report_path": _relative(report_path, workspace),
        "run_dir": _relative(run_dir, workspace),
        "output_evaluation_report": _relative(run_dir / "output_evaluation_report.json", workspace),
        "policy_effectiveness_report": _relative(run_dir / "policy_effectiveness_report.json", workspace),
        "memory_governance_report": _relative(memory_report_path, workspace),
        "guardrail_state": _read_json(guardrail_path, {}),
    }
    callback_result = emit_agent_callback(workspace, "run_evaluation_complete", payload, now=now)
    if callback_result.event_path is not None:
        ledger["daemon_callback_event"] = _relative(callback_result.event_path, workspace)
    if callback_result.webhook_status:
        ledger["daemon_callback_webhook_status"] = callback_result.webhook_status
    if callback_result.error:
        ledger["daemon_callback_error"] = callback_result.error
    _write_json(ledger_path, ledger)
    try:
        notifier(payload)
    except Exception as exc:
        ledger["daemon_post_complete_notification_error"] = _sanitize_error(str(exc))
        _write_json(ledger_path, ledger)
    return CompletionHookResult(
        evaluation_ran=True,
        run_tag=str(payload["run_tag"]),
        report_path=str(payload["report_path"]),
        message="evaluation complete",
    )


def _write_guardrail_state(run_dir: Path) -> Path:
    output_eval = _read_json(run_dir / "output_evaluation_report.json", {})
    effectiveness = _read_json(run_dir / "policy_effectiveness_report.json", {})
    memory = _read_json(run_dir / "memory_governance_report.json", {})
    reasons: list[str] = []
    status_counts = output_eval.get("status_counts") if isinstance(output_eval, dict) else {}
    block_count = int((status_counts or {}).get("block") or 0) if isinstance(status_counts, dict) else 0
    if block_count >= 2:
        reasons.append("repeated_output_blocks")
    for policy in effectiveness.get("policies") or [] if isinstance(effectiveness, dict) else []:
        if isinstance(policy, dict) and float(policy.get("low_value_rate") or 0.0) >= 0.9 and int(policy.get("simulations_spent") or 0) >= 20:
            reasons.append(f"low_quality_policy:{policy.get('diagnosis_type')}")
    for policy in memory.get("policies") or [] if isinstance(memory, dict) else []:
        if isinstance(policy, dict) and policy.get("memory_action") == "quarantine_candidate":
            reasons.append(f"memory_quarantine:{policy.get('diagnosis_type')}")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pause_next_run": bool(reasons),
        "reasons": sorted(set(reasons)),
    }
    path = run_dir / "daemon_guardrail_state.json"
    _write_json(path, payload)
    return path


def is_pid_running(pid: int, expected_command_tokens: Sequence[str]) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\";"
                "if ($null -eq $p) { exit 1 };"
                "[Console]::Out.Write($p.CommandLine)"
            ),
        ]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            return False
        command_line = completed.stdout or ""
        return all(token in command_line for token in expected_command_tokens)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def launch_process(root: Path, spec: ProcessSpec) -> LaunchResult:
    log_path = root / spec.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    pythonw_command = _prefer_pythonw(spec.command)
    log_handle = log_path.open("a", encoding="utf-8")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        pythonw_command,
        cwd=str(root),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    return LaunchResult(pid=process.pid, launched=True, message=f"started {spec.name}")


def _prefer_pythonw(command: list[str]) -> list[str]:
    if os.name != "nt" or not command:
        return command
    pythonw = Path(command[0]).with_name("pythonw.exe")
    if pythonw.exists():
        return [str(pythonw), *command[1:]]
    return command


def _latest_completed_ledger(runs_root: Path) -> tuple[Path | None, dict[str, Any] | None]:
    ledgers: list[tuple[str, Path, dict[str, Any]]] = []
    if not runs_root.exists():
        return None, None
    for ledger_path in runs_root.glob("*/daily_budget_ledger.json"):
        ledger = _read_json(ledger_path, {})
        if not isinstance(ledger, dict):
            continue
        if _ledger_completed(ledger):
            ledgers.append((str(ledger.get("date") or ""), ledger_path, ledger))
    if not ledgers:
        return None, None
    _, path, ledger = sorted(ledgers, key=lambda item: (item[0], item[1].parent.name), reverse=True)[0]
    return path, ledger


def _ledger_completed(ledger: dict[str, Any]) -> bool:
    daily_budget = int(ledger.get("daily_budget") or 0)
    spent = int(ledger.get("spent_simulations") or 0)
    remaining = int(ledger.get("remaining_simulations_after_commitments") or 0)
    return bool(ledger.get("last_budget_complete_report")) or (daily_budget > 0 and spent >= daily_budget and remaining <= 0)


def _read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _run_command(command: list[str]) -> int:
    completed = subprocess.run(command, text=True, check=False)
    return int(completed.returncode)


def _notify_completion(payload: dict[str, object]) -> None:
    line = (
        "completion notification: "
        f"run={payload.get('run_tag')} verdict={payload.get('verdict')} "
        f"comparison={payload.get('comparison_type')} report={payload.get('report_path')}"
    )
    print(line, flush=True)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _sanitize_error(text: str) -> str:
    sanitized = re.sub(r"(?i)(password|token|secret|key)\s*=\s*[^,\s;]+", r"\1=<redacted>", text)
    return sanitized[:300]
