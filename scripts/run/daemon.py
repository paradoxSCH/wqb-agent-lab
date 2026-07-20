"""Launch and supervise the production workflow daemon."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Mapping

from src.research_policy import ResearchPolicyError, load_research_policy

from wqb_agent_lab.governance.side_effects import (
    CapabilityDecision,
    SideEffectCapabilityDisabled,
    require_side_effect_capability,
)

from src.workflow_daemon import (
    build_dashboard_spec,
    build_workflow_spec,
    check_process_status,
    ensure_process,
    precheck_wqb_session,
    resolve_autonomous_run_identity,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch and supervise the local WQB alpha-mining daemon.")
    parser.add_argument("--workspace-root", default=".", help="Workspace root.")
    parser.add_argument(
        "--workflow-config",
        default=".local/research/workflows/production.json",
        help="Workflow budget config JSON.",
    )
    parser.add_argument("--date", help="Daily run date, YYYY-MM-DD. Defaults to local today.")
    parser.add_argument(
        "--budget-mode",
        default="standard",
        choices=["conservative", "standard", "aggressive", "expanded_1500"],
        help="Daily budget mode.",
    )
    parser.add_argument("--poll-seconds", type=int, default=900, help="Workflow daemon polling interval.")
    parser.add_argument("--dashboard-host", default="127.0.0.1", help="Dashboard host.")
    parser.add_argument("--dashboard-port", type=int, default=8765, help="Dashboard port.")
    parser.add_argument("--skip-session-precheck", action="store_true", help="Skip WQB session validation before launch.")
    parser.add_argument("--no-execute-scans", action="store_true", help="Launch workflow without consuming WQB simulation budget.")
    parser.add_argument("--autonomous-loop", action="store_true", help="Advance to the next unfinished run date automatically.")
    parser.add_argument("--auto-submit", action="store_true", help="Enable automatic direct-submit posting after budget completion.")
    parser.add_argument("--stop-after-summary", action="store_true", help="Workflow daemon stops after daily summary.")
    parser.add_argument("--once", action="store_true", help="Run one supervise tick and exit.")
    parser.add_argument("--watchdog-seconds", type=int, default=300, help="Supervisor health-check interval.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.workspace_root).resolve()
    config_path = Path(args.workflow_config)
    public_config_path = config_path if config_path.is_absolute() else root / config_path
    try:
        _validate_public_workflow_config(public_config_path)
    except (OSError, json.JSONDecodeError, ResearchPolicyError) as exc:
        code = exc.code if isinstance(exc, ResearchPolicyError) else "invalid_workflow_config"
        print(
            json.dumps(
                {"ok": False, "error": {"code": code, "message": str(exc), "config": str(public_config_path)}},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 2
    try:
        require_launch_capabilities(
            execute_scans=not args.no_execute_scans,
            auto_submit=args.auto_submit,
        )
    except SideEffectCapabilityDisabled as exc:
        print(json.dumps(exc.decision.to_dict(), ensure_ascii=False), flush=True)
        return 2
    run_date = _parse_date(args.date) if args.date else date.today()
    workflow_spec, dashboard_spec, run_date = build_launch_specs(
        root=root,
        workflow_config=config_path,
        run_date=run_date,
        budget_mode=args.budget_mode,
        poll_seconds=args.poll_seconds,
        execute_scans=not args.no_execute_scans,
        dashboard_host=args.dashboard_host,
        dashboard_port=args.dashboard_port,
        autonomous_loop=args.autonomous_loop,
        auto_submit=args.auto_submit,
        stop_after_summary=args.stop_after_summary,
    )
    if not args.skip_session_precheck:
        precheck = precheck_wqb_session()
        if not precheck.ok:
            print(precheck.error or "session precheck failed", flush=True)
            return 2
        print("session precheck ok", flush=True)

    while True:
        for message in supervise_once(root, workflow_spec, dashboard_spec):
            print(message, flush=True)

        if args.once:
            return 0
        time.sleep(max(30, args.watchdog_seconds))


def _validate_public_workflow_config(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"workflow config does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    load_research_policy(payload)


def supervise_once(
    root: Path,
    workflow_spec,
    dashboard_spec,
    *,
    inspector=None,
    launcher=None,
    evaluation_launcher=None,
) -> list[str]:
    messages: list[str] = []
    for spec in (workflow_spec, dashboard_spec):
        status = check_process_status(root, spec, inspector=inspector)
        if status.running:
            messages.append(f"{spec.name}: running pid={status.pid}")
            continue
        result = ensure_process(root, spec, inspector=inspector, launcher=launcher)
        action = "started" if result.launched else "kept"
        messages.append(f"{spec.name}: {action} pid={result.pid} {result.message}")

    try:
        launcher_fn = evaluation_launcher or launch_evaluation_worker
        messages.append(launcher_fn(root))
    except Exception as exc:
        messages.append(f"evaluation_worker_failed: {exc}")
    return messages


def require_launch_capabilities(
    *,
    execute_scans: bool,
    auto_submit: bool,
    env: Mapping[str, str] | None = None,
) -> list[CapabilityDecision]:
    decisions: list[CapabilityDecision] = []
    if execute_scans:
        decisions.append(require_side_effect_capability("simulation", env=env))
    if auto_submit:
        decisions.append(require_side_effect_capability("submission", env=env))
    return decisions


def build_launch_specs(
    *,
    root: Path,
    workflow_config: Path,
    run_date: date,
    budget_mode: str,
    poll_seconds: int,
    execute_scans: bool,
    dashboard_host: str,
    dashboard_port: int,
    autonomous_loop: bool,
    auto_submit: bool,
    stop_after_summary: bool,
):
    config_path = workflow_config
    workflow_config = _read_json(root / config_path, {})
    run_tag_prefix = str(workflow_config.get("daily_run_tag_prefix") or "wqb-agent-research") if isinstance(workflow_config, dict) else "wqb-agent-research"
    if autonomous_loop:
        identity = resolve_autonomous_run_identity(root, requested_date=run_date, run_tag_prefix=run_tag_prefix)
        run_date = identity["run_date"]
        if identity["run_tag_prefix"] != run_tag_prefix:
            config_path = _write_runtime_config(root, config_path, workflow_config, str(identity["run_tag_prefix"]))
            run_tag_prefix = str(identity["run_tag_prefix"])
    if auto_submit:
        _enable_auto_submit(root / config_path)
    python_exe = Path(sys.executable)

    workflow_spec = build_workflow_spec(
        python_exe=python_exe,
        workflow_config=config_path,
        run_date=run_date,
        budget_mode=budget_mode,
        poll_seconds=poll_seconds,
        execute_scans=execute_scans,
        stop_after_summary=stop_after_summary,
    )
    dashboard_spec = build_dashboard_spec(
        python_exe=python_exe,
        host=dashboard_host,
        port=dashboard_port,
    )
    return workflow_spec, dashboard_spec, run_date


def launch_evaluation_worker(root: Path, *, popen=None, state_max_age_seconds: int = 900) -> str:
    popen = popen or subprocess.Popen
    log_path = root / ".local" / "data" / "evaluations" / "evaluation_worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    latest_completed_run = _latest_completed_run_tag(root)
    state = _read_json(root / ".local" / "data" / "evaluations" / "evaluation_worker_state.json", {})
    if _evaluation_state_fresh_for_run(state, latest_completed_run, state_max_age_seconds):
        return f"evaluation_worker_skipped fresh_state run_tag={latest_completed_run or state.get('run_tag')}"
    command = [
        str(Path(sys.executable)),
        "-m",
        "scripts.workers.evaluation",
        "--workspace-root",
        str(root),
        "--once",
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with open(log_path, "a", encoding="utf-8") as log_fh:
        process = popen(
            command,
            cwd=root,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    return f"evaluation_worker_started pid={process.pid}"


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _latest_completed_run_tag(root: Path) -> str | None:
    runs_root = root / ".local" / "data" / "runs" / "continuous-alpha"
    latest_path: Path | None = None
    latest_ledger: dict[str, object] | None = None
    for ledger_path in runs_root.glob("*/daily_budget_ledger.json"):
        ledger = _read_json(ledger_path, {})
        if not isinstance(ledger, dict) or not ledger.get("last_budget_complete_report"):
            continue
        if latest_path is None or ledger_path.stat().st_mtime > latest_path.stat().st_mtime:
            latest_path = ledger_path
            latest_ledger = ledger
    if latest_ledger is None or latest_path is None:
        return None
    return str(latest_ledger.get("daily_run_tag") or latest_path.parent.name)


def _evaluation_state_fresh_for_run(state: object, latest_completed_run: str | None, max_age_seconds: int) -> bool:
    if not isinstance(state, dict) or max_age_seconds <= 0:
        return False
    status = str(state.get("status") or "")
    if status not in {"ok", "no_completed_evaluation"}:
        return False
    run_tag = str(state.get("run_tag") or "")
    if latest_completed_run and run_tag != latest_completed_run:
        return False
    updated_at = state.get("updated_at")
    if not updated_at:
        return False
    try:
        updated = datetime.fromisoformat(str(updated_at))
    except ValueError:
        return False
    return (datetime.now() - updated).total_seconds() <= max_age_seconds


def _enable_auto_submit(path: Path) -> None:
    payload = _read_json(path, {})
    if not isinstance(payload, dict):
        return
    payload["autonomous_loop"] = {
        "enabled": True,
        "max_daily_budget": 1000,
        "max_consecutive_days": 3,
        "auto_submit": True,
        "pause_on_guardrail": True,
    }
    payload["auto_submit_direct"] = {
        "enabled": True,
        "source": "submission_backlog",
        "post_only": True,
        "verify_after": True,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_runtime_config(root: Path, config_path: Path, payload: object, run_tag_prefix: str) -> Path:
    if not isinstance(payload, dict):
        return config_path
    runtime = dict(payload)
    runtime["daily_run_tag_prefix"] = run_tag_prefix
    runtime.setdefault("autonomous_loop", {})
    if isinstance(runtime["autonomous_loop"], dict):
        runtime["autonomous_loop"]["runtime_parent_config"] = config_path.as_posix()
    target = root / ".local" / "research" / "workflows" / "continuous-alpha" / "runtime" / f"{run_tag_prefix}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
    return target.relative_to(root)


if __name__ == "__main__":
    raise SystemExit(main())
