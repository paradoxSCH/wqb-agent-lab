from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .artifacts import read_json, relative_path, write_json, write_text
from .candidates import (
    candidate_score,
    check_value,
    failed_checks_from_check_list,
    live_checks_from_result,
    normalize_expression,
    pending_checks_from_check_list,
    row_metric_pass,
    units_warning_from_check_list,
)


REPORT_BASENAME = "submit_summary_budget_complete"


class ReportingWorkflow(Protocol):
    root: Path
    run_dir: Path
    ledger_path: Path
    run_tag: str
    dry_run: bool

    def _submitted_registry(self) -> tuple[set[str], set[str]]: ...
    def _failed_submit_attempt_alpha_ids(self) -> set[str]: ...
    def _preferred_live_check_paths(self) -> list[Path]: ...
    def _current_scan_result_paths(self) -> list[Path]: ...
    def _candidate_row_paths(self) -> list[Path]: ...
    def run_diagnosis_triage(
        self,
        ledger: dict[str, Any],
        *,
        ready: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]: ...
    def _auto_submit_direct(self) -> str | None: ...
    def _emit_progress_callback(
        self,
        event_type: str,
        ledger: dict[str, Any],
        *,
        stage: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None: ...


class WorkflowReporter:
    """Build submission shortlists and durable daily reports for one workflow run."""

    def __init__(self, workflow: ReportingWorkflow) -> None:
        self.workflow = workflow

    def collect_submit_ready(self) -> list[dict[str, Any]]:
        workflow = self.workflow
        submitted_ids, submitted_expressions = workflow._submitted_registry()
        excluded_ids = submitted_ids | workflow._failed_submit_attempt_alpha_ids()
        candidate_rows = self._load_candidate_rows_by_alpha()
        ready: dict[str, dict[str, Any]] = {}
        for live_path in workflow._preferred_live_check_paths():
            payload = read_json(live_path, [])
            results = payload if isinstance(payload, list) else [payload]
            for result in results:
                if not isinstance(result, dict):
                    continue
                alpha_id = str(result.get("alpha_id") or "")
                if not alpha_id or alpha_id in excluded_ids:
                    continue
                checks = live_checks_from_result(result)
                if not checks or failed_checks_from_check_list(checks):
                    continue
                row = dict(candidate_rows.get(alpha_id) or {})
                expression = normalize_expression(
                    str(row.get("expression") or result.get("expression") or "")
                )
                if expression and expression in submitted_expressions:
                    continue
                row.update(
                    {
                        "alpha_id": alpha_id,
                        "live_check_path": relative_path(live_path, workflow.root),
                        "live_checks": checks,
                        "validation_source": "live_check",
                        "requires_live_recheck": False,
                        "pending_checks": [
                            check.get("name")
                            for check in pending_checks_from_check_list(checks)
                        ],
                        "self_corr": check_value(checks, "SELF_CORRELATION"),
                        "sub_universe_sharpe": check_value(
                            checks, "LOW_SUB_UNIVERSE_SHARPE"
                        ),
                        "units_warning": units_warning_from_check_list(checks),
                    }
                )
                row["score"] = round(candidate_score(row), 4)
                self._upsert_ready_candidate(ready, row)

        for row in self._collect_current_scan_pass_rows(
            excluded_ids, submitted_expressions
        ):
            self._upsert_ready_candidate(ready, row)
        return sorted(
            ready.values(), key=lambda row: row.get("score", 0.0), reverse=True
        )

    def _collect_current_scan_pass_rows(
        self,
        submitted_ids: set[str],
        submitted_expressions: set[str],
    ) -> list[dict[str, Any]]:
        workflow = self.workflow
        rows: list[dict[str, Any]] = []
        for path in workflow._current_scan_result_paths():
            payload = read_json(path, [])
            if not isinstance(payload, list):
                continue
            for result in payload:
                if not isinstance(result, dict):
                    continue
                alpha_id = str(result.get("alpha_id") or "")
                if not alpha_id or alpha_id in submitted_ids:
                    continue
                expression = normalize_expression(str(result.get("expression") or ""))
                if expression and expression in submitted_expressions:
                    continue
                raw_checks = result.get("checks")
                checks = [
                    dict(check)
                    for check in raw_checks or []
                    if isinstance(check, Mapping)
                ]
                if not row_metric_pass(result) or failed_checks_from_check_list(checks):
                    continue
                row = dict(result)
                row.update(
                    {
                        "alpha_id": alpha_id,
                        "expression": expression,
                        "source_path": relative_path(path, workflow.root),
                        "validation_source": "scan_result",
                        "requires_live_recheck": True,
                        "pending_checks": [
                            check.get("name")
                            for check in pending_checks_from_check_list(checks)
                        ],
                        "self_corr": check_value(checks, "SELF_CORRELATION"),
                        "sub_universe_sharpe": check_value(
                            checks, "LOW_SUB_UNIVERSE_SHARPE"
                        ),
                        "units_warning": units_warning_from_check_list(checks),
                    }
                )
                row["score"] = round(candidate_score(row), 4)
                rows.append(row)
        return rows

    @staticmethod
    def _upsert_ready_candidate(
        ready: dict[str, dict[str, Any]], row: dict[str, Any]
    ) -> None:
        alpha_id = str(row.get("alpha_id") or "")
        if not alpha_id:
            return
        existing = ready.get(alpha_id)
        if existing is None:
            ready[alpha_id] = row
            return
        existing_live = existing.get("validation_source") == "live_check"
        row_live = row.get("validation_source") == "live_check"
        if row_live and not existing_live:
            ready[alpha_id] = row
        elif row_live == existing_live and float(row.get("score") or 0.0) > float(
            existing.get("score") or 0.0
        ):
            ready[alpha_id] = row

    def _load_candidate_rows_by_alpha(self) -> dict[str, dict[str, Any]]:
        workflow = self.workflow
        rows_by_alpha: dict[str, dict[str, Any]] = {}
        for path in reversed(workflow._candidate_row_paths()):
            payload = read_json(path, [])
            if not isinstance(payload, list):
                continue
            for row in payload:
                if not isinstance(row, dict) or not row.get("alpha_id"):
                    continue
                merged = dict(row)
                merged["source_path"] = relative_path(path, workflow.root)
                rows_by_alpha[str(row["alpha_id"])] = merged
        return rows_by_alpha

    def write_daily_report(
        self,
        ledger: dict[str, Any],
        *,
        now: datetime | None = None,
        reason: str = "budget_complete",
        force: bool = False,
    ) -> tuple[Path, Path]:
        workflow = self.workflow
        now = now or datetime.now()
        summary_json = workflow.run_dir / f"{REPORT_BASENAME}.json"
        summary_md = workflow.run_dir / f"{REPORT_BASENAME}.md"
        existing = ledger.get("last_budget_complete_report") or ledger.get(
            "last_daily_report"
        )
        if existing and not force and not workflow.dry_run:
            ready = self.collect_submit_ready()
            workflow.run_diagnosis_triage(ledger, ready=ready, now=now)
            write_json(workflow.ledger_path, ledger)
            return summary_json, workflow.root / str(existing)

        ready = self.collect_submit_ready()
        closed_loop = workflow.run_diagnosis_triage(ledger, ready=ready, now=now)
        payload = {
            "daily_run_tag": workflow.run_tag,
            "generated_at": now.isoformat(timespec="seconds"),
            "report_reason": reason,
            "budget": {
                "daily_budget": ledger.get("daily_budget"),
                "spent_simulations": ledger.get("spent_simulations"),
                "remaining_simulations_after_commitments": ledger.get(
                    "remaining_simulations_after_commitments"
                ),
                "stage_spend": ledger.get("stage_spend", {}),
            },
            "closed_loop": closed_loop,
            "submit_ready_count": len(ready),
            "submit_ready": ready,
            "recommendation": ready[0]["alpha_id"] if ready else None,
        }
        if workflow.dry_run:
            return summary_json, summary_md

        write_json(summary_json, payload)
        write_json(workflow.run_dir / "current_submit_candidate_snapshot.json", ready)
        write_text(summary_md, self._markdown(payload, ready, ledger, reason))
        ledger["last_daily_report"] = relative_path(summary_md, workflow.root)
        ledger["last_budget_complete_report"] = relative_path(summary_md, workflow.root)
        ledger["current_stage"] = "budget_complete_report_written"
        ledger.pop("completion_email_sent_at", None)
        ledger.pop("completion_email_error", None)
        write_json(workflow.ledger_path, ledger)
        if reason == "budget_complete":
            submit_message = workflow._auto_submit_direct()
            if submit_message:
                print(submit_message, flush=True)
            workflow._emit_progress_callback(
                "budget_complete",
                ledger,
                stage="budget_complete_report_written",
                extra={
                    "summary_json": relative_path(summary_json, workflow.root),
                    "summary_md": relative_path(summary_md, workflow.root),
                    "submit_ready_count": len(ready),
                    "auto_submit_result": submit_message,
                },
            )
            write_json(workflow.ledger_path, ledger)
        return summary_json, summary_md

    @staticmethod
    def _markdown(
        payload: Mapping[str, Any],
        ready: Sequence[Mapping[str, Any]],
        ledger: Mapping[str, Any],
        reason: str,
    ) -> str:
        lines = [
            "# Daily Budget Complete Report",
            "",
            f"Daily run: `{payload['daily_run_tag']}`",
            f"Generated at: `{payload['generated_at']}`",
            f"Reason: `{reason}`",
            f"Budget: `{ledger.get('spent_simulations')}` / `{ledger.get('daily_budget')}` spent",
            f"Submit-ready count: `{len(ready)}`",
            "",
        ]
        if not ready:
            lines.append(
                "No scan-result or live-check PASS candidates were found when the budget completed."
            )
            return "\n".join(lines) + "\n"

        best = ready[0]
        raw_metrics = best.get("metrics")
        metrics = raw_metrics if isinstance(raw_metrics, Mapping) else {}
        lines.extend(
            [
                "## Best Candidate",
                "",
                f"- Alpha: `{best.get('alpha_id')}`",
                f"- Score: `{best.get('score')}`",
                f"- Sharpe: `{metrics.get('sharpe')}`",
                f"- Fitness: `{metrics.get('fitness')}`",
                f"- Turnover: `{metrics.get('turnover')}`",
                f"- Self-corr: `{best.get('self_corr')}`",
                f"- Validation: `{best.get('validation_source')}`",
                f"- Requires live re-check: `{best.get('requires_live_recheck')}`",
                f"- Source: `{best.get('source_path')}`",
                "",
                "Expression:",
                "",
                "```text",
                str(best.get("expression") or ""),
                "```",
                "",
                "## Shortlist",
                "",
            ]
        )
        for row in ready[:10]:
            raw_row_metrics = row.get("metrics")
            row_metrics = (
                raw_row_metrics if isinstance(raw_row_metrics, Mapping) else {}
            )
            lines.append(
                f"- `{row.get('alpha_id')}` S={row_metrics.get('sharpe')} "
                f"F={row_metrics.get('fitness')} T={row_metrics.get('turnover')} "
                f"self_corr={row.get('self_corr')} source={row.get('validation_source')} "
                f"recheck={row.get('requires_live_recheck')} score={row.get('score')}"
            )
        return "\n".join(lines) + "\n"
