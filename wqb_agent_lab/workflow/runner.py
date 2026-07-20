from __future__ import annotations

import time
import traceback
from datetime import date, datetime, time as day_time, timedelta
from pathlib import Path
from typing import Any, Protocol

from .artifacts import read_json, relative_path, write_json
from .candidates import budget_exhausted
from .models import StagePlan


DAY_START_TIME = day_time(0, 0)


class RunnableWorkflow(Protocol):
    root: Path
    ledger_path: Path
    run_date: date
    dry_run: bool
    execute_scans: bool

    def drain_workflow_outbox(self) -> int: ...
    def load_or_create_ledger(self) -> dict[str, Any]: ...
    def reconcile_existing_stage_progress(self, ledger: dict[str, Any]) -> bool: ...
    def run_diagnosis_triage(
        self, ledger: dict[str, Any], *, now: datetime | None = None
    ) -> dict[str, Any]: ...
    def _emit_progress_callback(
        self,
        event_type: str,
        ledger: dict[str, Any],
        *,
        stage: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None: ...
    def run_registry_stage(self, *, now: datetime | None = None) -> str: ...
    def write_daily_report(
        self,
        ledger: dict[str, Any],
        *,
        now: datetime | None = None,
        reason: str = "budget_complete",
        force: bool = False,
    ) -> tuple[Path, Path]: ...
    def run_llm_plan(
        self, ledger: dict[str, Any], *, now: datetime | None = None
    ) -> Path | None: ...
    def run_scan_preflight(
        self, ledger: dict[str, Any], *, now: datetime | None = None
    ) -> tuple[StagePlan, str]: ...
    def execute_scan(self, plan: StagePlan, ledger: dict[str, Any]) -> int: ...
    def write_run_manifest(
        self, *, now: datetime, status: str, error_type: str = ""
    ) -> Path: ...
    def _set_run_date(self, run_date: date) -> None: ...
    def _run_once_tick(
        self, *, now: datetime, summary_only: bool = False
    ) -> list[str]: ...
    def run_once(
        self, *, now: datetime | None = None, summary_only: bool = False
    ) -> list[str]: ...


class WorkflowRunner:
    """Drive workflow ticks and polling without owning research-stage decisions."""

    def __init__(self, workflow: RunnableWorkflow) -> None:
        self.workflow = workflow

    def run_tick(self, *, now: datetime, summary_only: bool = False) -> list[str]:
        workflow = self.workflow
        replayed = workflow.drain_workflow_outbox() if not workflow.dry_run else 0
        ledger = workflow.load_or_create_ledger()
        messages = [f"ledger: {relative_path(workflow.ledger_path, workflow.root)}"]
        if replayed:
            messages.append(f"replayed workflow outbox events={replayed}")
        if workflow.reconcile_existing_stage_progress(ledger):
            messages.append("reconciled existing stage progress")
            if not workflow.dry_run:
                workflow.run_diagnosis_triage(ledger, now=now)
                workflow._emit_progress_callback(
                    "stage_progress_reconciled",
                    ledger,
                    stage=str(ledger.get("current_stage") or "reconciled"),
                    extra={"reason": "existing_stage_results_detected"},
                )
                write_json(workflow.ledger_path, ledger)
                messages.append("refreshed closed-loop artifacts after reconcile")
        if now.date() < workflow.run_date:
            messages.append(
                "waiting for daily start: "
                f"{workflow.run_date.isoformat()}T{DAY_START_TIME.isoformat()}"
            )
            return messages
        sync_status = workflow.run_registry_stage(now=now)
        expected_sync_statuses = {
            "ok",
            "skipped_dry_run",
            "skipped_disabled",
            "skipped_missing_credentials",
            "skipped_env",
        }
        if sync_status not in expected_sync_statuses:
            messages.append(f"submitted registry sync: {sync_status}")
        if summary_only:
            _, summary_md = workflow.write_daily_report(
                ledger, now=now, reason="manual_summary", force=True
            )
            messages.append(
                f"daily report: {relative_path(summary_md, workflow.root)}"
            )
            return messages
        if budget_exhausted(ledger):
            _, summary_md = workflow.write_daily_report(
                ledger, now=now, reason="budget_complete"
            )
            messages.append(
                f"budget complete report: {relative_path(summary_md, workflow.root)}"
            )
            return messages
        workflow.run_llm_plan(ledger, now=now)
        plan, initial_action = workflow.run_scan_preflight(ledger, now=now)
        messages.append(f"stage action: {plan.stage} -> {initial_action}")
        if initial_action == "slice_scan_config":
            messages.append(
                f"prepared {plan.candidate_count} candidates: "
                f"{relative_path(plan.sliced_config or Path(), workflow.root)}"
            )
            spent = workflow.execute_scan(plan, ledger)
            if spent:
                messages.append(f"executed scan spend={spent}")
                if budget_exhausted(ledger):
                    _, summary_md = workflow.write_daily_report(
                        ledger, now=now, reason="budget_complete"
                    )
                    messages.append(
                        "budget complete report: "
                        f"{relative_path(summary_md, workflow.root)}"
                    )
            elif not workflow.execute_scans:
                messages.append("scan not executed; pass --execute-scans to consume budget")
        return messages

    def run_once(
        self, *, now: datetime | None = None, summary_only: bool = False
    ) -> list[str]:
        workflow = self.workflow
        now = now or datetime.now()
        try:
            messages = workflow._run_once_tick(now=now, summary_only=summary_only)
        except Exception as exc:
            if not workflow.dry_run:
                try:
                    workflow.write_run_manifest(
                        now=now, status="failed", error_type=type(exc).__name__
                    )
                except Exception as manifest_exc:
                    exc.add_note(
                        "run manifest checkpoint also failed: "
                        f"{type(manifest_exc).__name__}"
                    )
            raise
        if not workflow.dry_run:
            try:
                manifest_path = workflow.write_run_manifest(
                    now=now, status="checkpointed"
                )
                messages.append(
                    f"run manifest: {relative_path(manifest_path, workflow.root)}"
                )
            except Exception as exc:
                messages.append(f"run manifest unavailable: {type(exc).__name__}")
        return messages

    def run_daemon(
        self, *, poll_seconds: int = 900, continue_next_day: bool = True
    ) -> None:
        workflow = self.workflow
        while True:
            try:
                now = datetime.now()
                if now.date() < workflow.run_date:
                    time.sleep(max(60, poll_seconds))
                    continue
                existing_ledger = read_json(workflow.ledger_path, {})
                if now.date() > workflow.run_date and budget_exhausted(existing_ledger):
                    if not continue_next_day:
                        break
                    next_date = workflow.run_date + timedelta(days=1)
                    workflow._set_run_date(min(next_date, now.date()))
                    existing_ledger = read_json(workflow.ledger_path, {})
                if budget_exhausted(existing_ledger) and existing_ledger.get(
                    "last_budget_complete_report"
                ):
                    if not continue_next_day:
                        break
                    time.sleep(max(60, poll_seconds))
                    continue
                messages = workflow.run_once(now=now)
                for message in messages:
                    print(message)
                ledger = read_json(workflow.ledger_path, {})
                if budget_exhausted(ledger) and ledger.get(
                    "last_budget_complete_report"
                ):
                    if not continue_next_day:
                        break
                    time.sleep(max(60, poll_seconds))
                    continue
                if any(
                    message.startswith("executed scan spend=") for message in messages
                ):
                    continue
                time.sleep(max(60, poll_seconds))
            except Exception as exc:
                print(f"ERROR: daemon tick failed: {exc}", flush=True)
                traceback.print_exc()
                time.sleep(max(60, poll_seconds))

    def run_until_budget_complete(self, *, poll_seconds: int = 900) -> None:
        workflow = self.workflow
        while True:
            now = datetime.now()
            existing_ledger = read_json(workflow.ledger_path, {})
            if budget_exhausted(existing_ledger) and existing_ledger.get(
                "last_budget_complete_report"
            ):
                break
            messages = workflow.run_once(now=now)
            for message in messages:
                print(message)
            ledger = read_json(workflow.ledger_path, {})
            if budget_exhausted(ledger) and ledger.get("last_budget_complete_report"):
                break
            if any(
                message.startswith("executed scan spend=") for message in messages
            ):
                continue
            time.sleep(max(60, poll_seconds))
