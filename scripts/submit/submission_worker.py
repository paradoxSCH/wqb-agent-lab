from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from src.atomic_json import atomic_write_json
from src.process_lock import PidFileLock
from src.side_effect_governance import evaluate_side_effect_capability
from wqb_agent_lab.platform import CheckReadiness, evaluate_check_snapshot
from wqb_agent_lab.runtime import OperationJournal, OperationRecord, SideEffectUncertainError


STATE_FILE = "submission_state.json"
BACKLOG_FILE = "submission_backlog.json"
SUBMITTED_STATUSES = {"ACTIVE", "SUBMITTED"}
FINAL_STATUSES = {"submitted_confirmed", "rejected", "failed", "manual_review_or_platform_lag"}


class SubmissionClient(Protocol):
    def check(self, alpha_id: str) -> dict[str, Any]:
        ...

    def submit(self, alpha_id: str) -> dict[str, Any]:
        ...

    def detail(self, alpha_id: str) -> dict[str, Any]:
        ...

    def record_submission(self, alpha_id: str) -> None:
        ...


class WorkerLock(PidFileLock):
    def __init__(self, path: Path | str, *, pid_checker: Callable[[int], bool] | None = None) -> None:
        super().__init__(path, owner="submission worker", pid_checker=pid_checker)


def enqueue_submission_jobs(run_dir: Path | str, *, now: datetime | None = None) -> dict[str, Any]:
    run_path = Path(run_dir)
    now = now or datetime.now()
    state = _read_json(run_path / STATE_FILE, {"jobs": []})
    jobs = state.setdefault("jobs", [])
    existing = {str(job.get("alpha_id")) for job in jobs if isinstance(job, dict)}
    backlog = _read_json(run_path / BACKLOG_FILE, [])
    for row in backlog if isinstance(backlog, list) else []:
        if not isinstance(row, dict):
            continue
        alpha_id = str(row.get("alpha_id") or "").strip()
        if not alpha_id or alpha_id in existing:
            continue
        jobs.append(
            {
                "alpha_id": alpha_id,
                "status": "queued",
                "recommended_action": str(row.get("recommended_action") or "submit"),
                "requires_live_recheck": bool(row.get("requires_live_recheck")),
                "score": row.get("score"),
                "source_path": row.get("source_path"),
                "attempts": 0,
                "created_at": now.isoformat(timespec="seconds"),
                "updated_at": now.isoformat(timespec="seconds"),
            }
        )
        existing.add(alpha_id)
    state["generated_at"] = state.get("generated_at") or now.isoformat(timespec="seconds")
    state["updated_at"] = now.isoformat(timespec="seconds")
    state["source_backlog"] = BACKLOG_FILE
    state["summary"] = _summarize_jobs(jobs)
    _write_json(run_path / STATE_FILE, state)
    return state


class SubmissionWorker:
    def __init__(
        self,
        run_dir: Path | str,
        *,
        client: SubmissionClient | None = None,
        confirm_polls: int = 12,
        confirm_wait_seconds: float = 30.0,
        max_confirmation_attempts: int = 24,
        max_confirmation_age_seconds: float = 24 * 60 * 60,
        max_jobs_per_tick: int = 10,
        live_check_polls: int = 2,
        live_check_wait_seconds: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
        env: Mapping[str, str] | None = None,
        operation_journal: OperationJournal | None = None,
        reconciliation_max_attempts: int = 3,
        reconciliation_retry_seconds: int = 300,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.client = client
        self.env = env
        self.confirm_polls = max(1, int(confirm_polls))
        self.confirm_wait_seconds = max(0.0, float(confirm_wait_seconds))
        self.max_confirmation_attempts = max(1, int(max_confirmation_attempts))
        self.max_confirmation_age_seconds = max(0.0, float(max_confirmation_age_seconds))
        self.max_jobs_per_tick = max(1, int(max_jobs_per_tick))
        self.live_check_polls = max(2, int(live_check_polls))
        self.live_check_wait_seconds = max(0.0, float(live_check_wait_seconds))
        self.sleep = sleep
        self.operation_journal = operation_journal or OperationJournal(self.run_dir / "operations.db")
        self.reconciliation_max_attempts = max(1, int(reconciliation_max_attempts))
        self.reconciliation_retry_seconds = max(0, int(reconciliation_retry_seconds))

    def run_once(self) -> dict[str, Any]:
        state = enqueue_submission_jobs(self.run_dir)
        jobs = state.setdefault("jobs", [])
        capability = evaluate_side_effect_capability("submission", env=self.env)
        if not capability.enabled:
            state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            state["summary"] = _summarize_jobs(jobs)
            state["capability"] = capability.to_dict()
            _write_json(self.run_dir / STATE_FILE, state)
            return {
                "status": "capability_disabled",
                "processed_count": 0,
                "capability": capability.to_dict(),
                **state["summary"],
            }
        if self.client is None:
            self.client = BrainSubmissionClient(
                run_dir=self.run_dir,
                operation_journal=self.operation_journal,
            )
        processed = 0
        for job in jobs:
            if not isinstance(job, dict) or processed >= self.max_jobs_per_tick:
                continue
            self._normalize_terminal_job(job)
            if str(job.get("status") or "") in FINAL_STATUSES:
                continue
            self._process_job(job)
            processed += 1
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        state["summary"] = _summarize_jobs(jobs)
        _write_json(self.run_dir / STATE_FILE, state)
        return {"processed_count": processed, **state["summary"]}

    def _process_job(self, job: dict[str, Any]) -> None:
        status = str(job.get("status") or "queued")
        operation = self._submission_operation(job)
        if operation is not None:
            job["submission_operation_id"] = operation.operation_id
            job["submission_operation_outcome"] = operation.outcome
            if operation.outcome == "manual_review":
                job["status"] = "manual_review_or_platform_lag"
                job["error"] = operation.reconciliation_reason or operation.reason
                return
            if operation.outcome in {"started", "unknown_commit", "reconciliation_pending"}:
                self._reconcile_unknown_submission(job, operation)
                return
            if operation.outcome == "accepted" and status not in {
                "pending_confirmation",
                "accepted_but_unconfirmed",
                "post_accepted",
            }:
                job["status"] = "post_accepted"
                self._confirm_submission(job)
                return
        if status in {"pending_confirmation", "accepted_but_unconfirmed", "post_accepted"}:
            self._confirm_submission(job)
            return
        if status == "throttled_retry_later":
            job["status"] = str(job.pop("retry_status", None) or "queued")
            if job["status"] in {"accepted_but_unconfirmed", "post_accepted", "pending_confirmation"}:
                self._confirm_submission(job)
                return

        if not self._live_check(job):
            return

        try:
            response = self.client.submit(str(job["alpha_id"]))
        except SideEffectUncertainError as exc:
            job["attempts"] = int(job.get("attempts") or 0) + 1
            job["submission_operation_id"] = exc.record.operation_id
            job["submission_operation_outcome"] = exc.record.outcome
            job["status"] = "submission_unknown_commit"
            job["error"] = exc.record.reason
            job["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._reconcile_unknown_submission(job, exc.record)
            return
        job["attempts"] = int(job.get("attempts") or 0) + 1
        job["last_submit_status_code"] = response.get("status_code")
        job["last_submit_response_text"] = str(response.get("text") or "")[:500]
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        status_code = int(response.get("status_code") or 0)
        if status_code == 429:
            self._mark_throttled(job, response)
            return
        if _contains_already_submitted(job.get("last_submit_response_text")):
            self._mark_submitted_confirmed(job)
            return
        if status_code >= 400:
            job["status"] = "rejected"
            job["error"] = job["last_submit_response_text"] or f"submit_http_{status_code}"
            return
        job["status"] = "post_accepted"
        latest_operation = self._submission_operation(job)
        if latest_operation is not None:
            job["submission_operation_id"] = latest_operation.operation_id
            job["submission_operation_outcome"] = latest_operation.outcome
        self._confirm_submission(job)

    def _submission_operation(self, job: dict[str, Any]) -> OperationRecord | None:
        alpha_id = str(job.get("alpha_id") or "")
        operation_id = str(job.get("submission_operation_id") or "")
        if operation_id:
            try:
                return self.operation_journal.get(operation_id)
            except KeyError:
                pass
        candidates = self.operation_journal.records(
            "submission.create",
            outcomes=(
                "started",
                "accepted",
                "unknown_commit",
                "reconciliation_pending",
                "manual_review",
            ),
        )
        matches = [
            record
            for record in candidates
            if str((record.payload or {}).get("alpha_id") or "") == alpha_id
        ]
        return matches[-1] if matches else None

    def _reconcile_unknown_submission(
        self,
        job: dict[str, Any],
        operation: OperationRecord,
    ) -> None:
        if operation.outcome == "manual_review":
            job["status"] = "manual_review_or_platform_lag"
            job["error"] = operation.reconciliation_reason or operation.reason
            return
        if not _reconciliation_due(operation):
            job["status"] = "submission_reconciliation_pending"
            job["next_reconcile_at"] = operation.next_reconcile_at
            return
        latest = self.client.detail(str(job["alpha_id"]))
        job["last_detail_status_code"] = latest.get("status_code")
        job["platform_status"] = latest.get("status")
        job["dateSubmitted"] = latest.get("dateSubmitted")
        if _is_submitted(latest):
            resolved = self.operation_journal.finish(
                operation.operation_id,
                "accepted",
                reason=f"{operation.reason};reconciled_alpha_detail".strip(";"),
                remote_ref=f"/alphas/{job['alpha_id']}",
            )
            job["submission_operation_outcome"] = resolved.outcome
            job["reconciliation_evidence"] = "submitted_alpha_detail"
            self._mark_submitted_confirmed(job)
            return
        status_code = int(latest.get("status_code") or 0)
        reason = (
            f"alpha_detail_http_{status_code}"
            if status_code >= 400
            else "no_positive_submission_evidence"
        )
        updated = self.operation_journal.record_reconciliation_attempt(
            operation.operation_id,
            reason=reason,
            retry_after_seconds=self.reconciliation_retry_seconds,
            max_attempts=self.reconciliation_max_attempts,
            now=datetime.now(timezone.utc),
        )
        job["submission_operation_outcome"] = updated.outcome
        job["reconciliation_attempts"] = updated.reconcile_attempts
        job["next_reconcile_at"] = updated.next_reconcile_at
        job["error"] = reason
        job["status"] = (
            "manual_review_or_platform_lag"
            if updated.outcome == "manual_review"
            else "submission_reconciliation_pending"
        )
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def _live_check(self, job: dict[str, Any]) -> bool:
        job["status"] = "live_checking"
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        previous_fingerprint: str | None = None
        for poll_index in range(self.live_check_polls):
            if poll_index > 0 and self.live_check_wait_seconds:
                self.sleep(self.live_check_wait_seconds)
            response = self.client.check(str(job["alpha_id"]))
            job["last_check_status_code"] = response.get("status_code")
            status_code = int(response.get("status_code") or 0)
            if status_code == 429:
                self._mark_throttled(job, response, retry_status="queued")
                return False
            legacy_failed = [str(name).upper() for name in response.get("failed_checks") or []]
            if "ALREADY_SUBMITTED" in legacy_failed:
                self._mark_submitted_confirmed(job)
                return False
            if status_code >= 400:
                job["status"] = "failed"
                job["error"] = str(response.get("text") or f"check_http_{status_code}")[:500]
                return False

            decision = evaluate_check_snapshot(response.get("checks") or [])
            self._record_check_decision(job, decision)
            if decision.status == "already_submitted":
                self._mark_submitted_confirmed(job)
                return False
            if decision.status == "failed":
                job["status"] = "rejected"
                job["error"] = "live_check_failed"
                job["diagnosis_type"] = "pre_submit_check_failed"
                return False
            if decision.status == "ready":
                if decision.fingerprint == previous_fingerprint:
                    job["status"] = "live_check_passed"
                    job["error"] = ""
                    job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    return True
                previous_fingerprint = decision.fingerprint
            else:
                previous_fingerprint = None

        job["status"] = "waiting_for_checks"
        job["error"] = (
            "live_check_snapshot_not_stable"
            if previous_fingerprint is not None
            else "live_check_incomplete_or_pending"
        )
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return False

    def _confirm_submission(self, job: dict[str, Any]) -> None:
        now = datetime.now()
        job["confirmation_attempts"] = int(job.get("confirmation_attempts") or 0) + 1
        job.setdefault("post_accepted_at", now.isoformat(timespec="seconds"))
        latest: dict[str, Any] = {}
        for poll_index in range(self.confirm_polls):
            if poll_index > 0 and self.confirm_wait_seconds:
                self.sleep(self.confirm_wait_seconds)
            latest = self.client.detail(str(job["alpha_id"]))
            job["last_detail_status_code"] = latest.get("status_code")
            job["platform_status"] = latest.get("status")
            job["dateSubmitted"] = latest.get("dateSubmitted")
            if _is_submitted(latest):
                self._mark_submitted_confirmed(job)
                return
            if int(latest.get("status_code") or 0) == 429:
                self._mark_throttled(job, latest, retry_status="accepted_but_unconfirmed")
                return
            check_response = self.client.check(str(job["alpha_id"]))
            check_status = int(check_response.get("status_code") or 0)
            if check_status == 429:
                self._mark_throttled(job, check_response, retry_status="accepted_but_unconfirmed")
                return
            if check_status >= 400:
                job["last_post_check_error"] = str(
                    check_response.get("text") or f"check_http_{check_status}"
                )[:500]
                continue
            decision = evaluate_check_snapshot(check_response.get("checks") or [])
            self._record_check_decision(job, decision)
            if decision.status == "already_submitted":
                self._mark_submitted_confirmed(job)
                return
            if decision.status == "failed":
                job["status"] = "rejected"
                job["error"] = "post_accept_check_failed"
                job["diagnosis_type"] = "post_accept_check_failed"
                job["updated_at"] = datetime.now().isoformat(timespec="seconds")
                return
        if self._confirmation_exhausted(job, now=datetime.now()):
            job["status"] = "manual_review_or_platform_lag"
            job["error"] = "post_accepted_but_platform_still_unsubmitted"
        else:
            job["status"] = "accepted_but_unconfirmed"
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def _record_check_decision(self, job: dict[str, Any], decision: CheckReadiness) -> None:
        job["last_checks"] = list(decision.checks)
        job["last_failed_checks"] = list(decision.failed_checks)
        job["last_pending_checks"] = list(decision.pending_checks)
        job["last_missing_checks"] = list(decision.missing_checks)
        job["last_unknown_checks"] = list(decision.unknown_checks)
        job["last_check_fingerprint"] = decision.fingerprint

    def _mark_throttled(
        self,
        job: dict[str, Any],
        response: dict[str, Any],
        *,
        retry_status: str = "queued",
    ) -> None:
        job["status"] = "throttled_retry_later"
        job["retry_status"] = retry_status
        job["retry_after_seconds"] = int(response.get("retry_after_seconds") or 60)
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def _mark_submitted_confirmed(self, job: dict[str, Any]) -> None:
        job["status"] = "submitted_confirmed"
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.client.record_submission(str(job["alpha_id"]))

    def _normalize_terminal_job(self, job: dict[str, Any]) -> None:
        if str(job.get("status") or "") != "rejected":
            return
        if _contains_already_submitted(job.get("error")) or _contains_already_submitted(job.get("last_submit_response_text")):
            self._mark_submitted_confirmed(job)

    def _confirmation_exhausted(self, job: dict[str, Any], *, now: datetime) -> bool:
        attempts = int(job.get("confirmation_attempts") or 0)
        if attempts >= self.max_confirmation_attempts:
            return True
        if self.max_confirmation_age_seconds <= 0:
            return False
        accepted_at = _parse_datetime(job.get("post_accepted_at"))
        if accepted_at is None:
            return False
        return (now - accepted_at).total_seconds() >= self.max_confirmation_age_seconds


class BrainSubmissionClient:
    def __init__(
        self,
        *,
        wqb_client: Any | None = None,
        run_dir: Path | str | None = None,
        operation_journal: OperationJournal | None = None,
    ) -> None:
        from scripts.submit.submit_alpha_v2 import _record_submission
        from wqb_agent_lab.platform import WQBClient

        run_path = Path(run_dir) if run_dir is not None else None
        self.wqb_client = wqb_client or WQBClient.from_config(
            run_id=run_path.name if run_path is not None else None,
            operation_journal=operation_journal,
        )
        self._record_submission = _record_submission

    def check(self, alpha_id: str) -> dict[str, Any]:
        checks = self.wqb_client.get_alpha_checks(alpha_id)
        serialized = [check.to_dict() for check in checks]
        return {
            "status_code": 200,
            "retry_after_seconds": 60,
            "failed_checks": [check.name for check in checks if str(check.result or "").upper() in {"FAIL", "ERROR"}],
            "pending_checks": [check.name for check in checks if str(check.result or "").upper() == "PENDING"],
            "checks": serialized,
            "text": "",
        }

    def submit(self, alpha_id: str) -> dict[str, Any]:
        result = self.wqb_client.submit_alpha(alpha_id, confirm_polls=1, confirm_wait_seconds=0.0)
        return {
            "status_code": result.post_status_code,
            "retry_after_seconds": result.retry_after_seconds or 60,
            "text": result.diagnosis,
            "headers": {},
            "post_status": result.post_status,
            "confirmation_status": result.confirmation_status,
            "platform_status": result.platform_status,
            "dateSubmitted": result.date_submitted,
        }

    def detail(self, alpha_id: str) -> dict[str, Any]:
        detail = self.wqb_client.get_alpha(alpha_id)
        return {
            "status_code": detail.http_status,
            "retry_after_seconds": 60,
            "status": detail.status,
            "dateSubmitted": detail.date_submitted,
            "sharpe": detail.metrics.get("sharpe"),
            "fitness": detail.metrics.get("fitness"),
            "turnover": detail.metrics.get("turnover"),
            "text": "",
        }

    def record_submission(self, alpha_id: str) -> None:
        recorder = getattr(self.wqb_client, "record_submission", None)
        if callable(recorder):
            recorder(alpha_id)
            return
        self._record_submission(alpha_id)


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir)
    try:
        with WorkerLock(run_dir / "submission_worker.lock"):
            worker = SubmissionWorker(
                run_dir,
                confirm_polls=args.confirm_polls,
                confirm_wait_seconds=args.confirm_wait_seconds,
                max_confirmation_attempts=args.max_confirmation_attempts,
                max_confirmation_age_seconds=args.max_confirmation_age_seconds,
                max_jobs_per_tick=args.max_jobs_per_tick,
                live_check_polls=args.live_check_polls,
                live_check_wait_seconds=args.live_check_wait_seconds,
            )
            while True:
                result = worker.run_once()
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if args.once or not args.daemon:
                    return 0
                time.sleep(max(10.0, float(args.poll_seconds)))
    except RuntimeError as exc:
        print(json.dumps({"status": "already_running", "message": str(exc)}, ensure_ascii=False), flush=True)
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Asynchronous WQB submission worker.")
    parser.add_argument("--run-dir", required=True, help="Run directory containing submission_backlog.json.")
    parser.add_argument("--once", action="store_true", help="Process one tick and exit.")
    parser.add_argument("--daemon", action="store_true", help="Keep processing pending submissions.")
    parser.add_argument("--poll-seconds", type=float, default=300.0, help="Daemon polling interval.")
    parser.add_argument("--confirm-polls", type=int, default=20, help="Submission confirmation polls per job.")
    parser.add_argument("--confirm-wait-seconds", type=float, default=30.0, help="Seconds between confirmation polls.")
    parser.add_argument("--max-confirmation-attempts", type=int, default=24, help="Refresh attempts before platform-lag review.")
    parser.add_argument(
        "--max-confirmation-age-seconds",
        type=float,
        default=24 * 60 * 60,
        help="Accepted-but-unconfirmed age before platform-lag review.",
    )
    parser.add_argument("--max-jobs-per-tick", type=int, default=10, help="Maximum jobs processed per tick.")
    parser.add_argument("--live-check-polls", type=int, default=2, help="Consecutive live-check snapshots per submission.")
    parser.add_argument("--live-check-wait-seconds", type=float, default=15.0, help="Seconds between live-check snapshots.")
    return parser.parse_args()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload)


def _summarize_jobs(jobs: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        status = str(job.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "queued": counts.get("queued", 0),
        "live_checking_count": counts.get("live_checking", 0),
        "waiting_for_checks_count": counts.get("waiting_for_checks", 0),
        "pending_confirmation_count": counts.get("pending_confirmation", 0),
        "accepted_but_unconfirmed_count": counts.get("accepted_but_unconfirmed", 0),
        "submission_unknown_commit_count": counts.get("submission_unknown_commit", 0),
        "submission_reconciliation_pending_count": counts.get("submission_reconciliation_pending", 0),
        "manual_review_or_platform_lag_count": counts.get("manual_review_or_platform_lag", 0),
        "submitted_confirmed_count": counts.get("submitted_confirmed", 0),
        "rejected_count": counts.get("rejected", 0),
        "failed_count": counts.get("failed", 0),
        "throttled_count": counts.get("throttled_retry_later", 0),
        "total": sum(counts.values()),
    }


def _is_submitted(detail: dict[str, Any]) -> bool:
    status = str(detail.get("status") or "").upper()
    return status in SUBMITTED_STATUSES or bool(detail.get("dateSubmitted"))


def _extract_checks(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        direct = payload.get("checks")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
        nested = payload.get("is")
        if isinstance(nested, dict) and isinstance(nested.get("checks"), list):
            return [item for item in nested["checks"] if isinstance(item, dict)]
    return []


def _retry_after_seconds(response: Any) -> int:
    try:
        value = response.headers.get("Retry-After")
    except AttributeError:
        value = None
    try:
        return int(float(value)) if value else 60
    except (TypeError, ValueError):
        return 60


def _contains_already_submitted(value: Any) -> bool:
    return "ALREADY_SUBMITTED" in str(value or "").upper()


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _reconciliation_due(record: OperationRecord) -> bool:
    if not record.next_reconcile_at:
        return True
    try:
        next_at = datetime.fromisoformat(record.next_reconcile_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if next_at.tzinfo is None:
        next_at = next_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= next_at


if __name__ == "__main__":
    raise SystemExit(main())
