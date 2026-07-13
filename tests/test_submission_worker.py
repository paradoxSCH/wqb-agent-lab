from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.submit.submission_worker import BrainSubmissionClient, SubmissionWorker, WorkerLock, enqueue_submission_jobs
from src.wqb.check_readiness import REQUIRED_SUBMISSION_CHECK_NAMES
from src.wqb.models import WQBAlphaDetail, WQBSubmitResult


class FakeClient:
    def __init__(self) -> None:
        self.checks: list[dict[str, object]] = []
        self.submits: list[dict[str, object]] = []
        self.details: list[dict[str, object]] = []
        self.submitted: list[str] = []

    def check(self, alpha_id: str) -> dict[str, object]:
        return self.checks.pop(0)

    def submit(self, alpha_id: str) -> dict[str, object]:
        return self.submits.pop(0)

    def detail(self, alpha_id: str) -> dict[str, object]:
        return self.details.pop(0)

    def record_submission(self, alpha_id: str) -> None:
        self.submitted.append(alpha_id)


class FakeWQBClient:
    def __init__(self) -> None:
        self.recorded: list[str] = []

    def get_alpha_checks(self, alpha_id: str) -> list[object]:
        return []

    def submit_alpha(self, alpha_id: str, **kwargs: object) -> WQBSubmitResult:
        return WQBSubmitResult(
            alpha_id=alpha_id,
            post_status="accepted",
            confirmation_status="still_unsubmitted",
            platform_status="UNSUBMITTED",
            diagnosis="post_accepted_but_still_unsubmitted",
            post_status_code=201,
        )

    def get_alpha(self, alpha_id: str) -> WQBAlphaDetail:
        return WQBAlphaDetail(alpha_id=alpha_id, http_status=200, status="UNSUBMITTED")

    def record_submission(self, alpha_id: str) -> None:
        self.recorded.append(alpha_id)


def complete_check_response(*, self_corr_result: str = "PASS", self_corr_value: float = 0.4) -> dict[str, object]:
    checks = [
        {"name": name, "result": "PASS"}
        for name in sorted(REQUIRED_SUBMISSION_CHECK_NAMES - {"SELF_CORRELATION"})
    ]
    checks.append(
        {
            "name": "SELF_CORRELATION",
            "result": self_corr_result,
            "value": self_corr_value,
            "limit": 0.7,
        }
    )
    return {"status_code": 200, "checks": checks, "failed_checks": [], "pending_checks": []}


class SubmissionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capability = patch.dict(os.environ, {"WQB_LIVE_SUBMIT_CAPABILITY": "1"})
        self.capability.start()

    def tearDown(self) -> None:
        self.capability.stop()

    def test_worker_preserves_queue_and_makes_no_client_calls_when_capability_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_backlog.json",
                [{"alpha_id": "A1", "recommended_action": "live_recheck_then_submit"}],
            )
            client = FakeClient()

            worker = SubmissionWorker(run_dir, client=client, env={})
            result = worker.run_once()

            self.assertEqual(result["status"], "capability_disabled")
            self.assertEqual(result["processed_count"], 0)
            self.assertEqual(result["queued"], 1)
            state = self._read_json(run_dir / "submission_state.json")
            self.assertEqual(state["jobs"][0]["status"], "queued")
            self.assertEqual(client.checks, [])
            self.assertEqual(client.submits, [])
            self.assertEqual(client.details, [])

    def test_worker_lock_prevents_second_worker_for_same_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "submission_worker.lock"
            with WorkerLock(lock_path):
                with self.assertRaises(RuntimeError):
                    with WorkerLock(lock_path):
                        pass
            self.assertFalse(lock_path.exists())

    def test_worker_lock_reclaims_stale_pid_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "submission_worker.lock"
            self._write_json(lock_path, {"pid": 999999, "created_at": "2026-07-06T03:30:00"})

            with WorkerLock(lock_path, pid_checker=lambda _pid: False):
                payload = self._read_json(lock_path)

            self.assertEqual(payload["pid"], os.getpid())
            self.assertFalse(lock_path.exists())

    def test_enqueue_uses_backlog_actions_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_backlog.json",
                [
                    {"alpha_id": "A1", "recommended_action": "live_recheck_then_submit", "score": 5.0},
                    {"alpha_id": "A1", "recommended_action": "live_recheck_then_submit", "score": 4.0},
                    {"alpha_id": "A2", "recommended_action": "submit", "score": 3.0},
                ],
            )

            first = enqueue_submission_jobs(run_dir)
            second = enqueue_submission_jobs(run_dir)

            self.assertEqual(first["summary"]["queued"], 2)
            self.assertEqual(second["summary"]["queued"], 2)
            jobs = {job["alpha_id"]: job for job in second["jobs"]}
            self.assertEqual(jobs["A1"]["recommended_action"], "live_recheck_then_submit")
            self.assertEqual(jobs["A1"]["status"], "queued")
            self.assertEqual(jobs["A2"]["recommended_action"], "submit")

    def test_worker_keeps_accepted_post_unconfirmed_until_platform_confirms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_backlog.json",
                [{"alpha_id": "A1", "recommended_action": "live_recheck_then_submit"}],
            )
            client = FakeClient()
            client.checks.extend([complete_check_response(), complete_check_response()])
            client.checks.append({"status_code": 200, "checks": [], "failed_checks": [], "pending_checks": []})
            client.submits.append({"status_code": 201, "ok": True, "text": ""})
            client.details.append({"status_code": 200, "status": "UNSUBMITTED", "dateSubmitted": None})

            worker = SubmissionWorker(run_dir, client=client, confirm_polls=1, sleep=lambda _seconds: None)
            result = worker.run_once()

            self.assertEqual(result["processed_count"], 1)
            state = self._read_json(run_dir / "submission_state.json")
            self.assertEqual(state["jobs"][0]["status"], "accepted_but_unconfirmed")
            self.assertEqual(state["jobs"][0]["last_submit_status_code"], 201)
            self.assertEqual(state["jobs"][0]["confirmation_attempts"], 1)
            self.assertEqual(client.submitted, [])

    def test_worker_does_not_submit_when_checks_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_backlog.json",
                [{"alpha_id": "A1", "recommended_action": "submit"}],
            )
            client = FakeClient()
            client.checks.extend(
                [
                    {"status_code": 200, "checks": [], "failed_checks": [], "pending_checks": []},
                    {"status_code": 200, "checks": [], "failed_checks": [], "pending_checks": []},
                ]
            )

            result = SubmissionWorker(run_dir, client=client, sleep=lambda _seconds: None).run_once()

            state = self._read_json(run_dir / "submission_state.json")
            self.assertEqual(result["waiting_for_checks_count"], 1)
            self.assertEqual(state["jobs"][0]["status"], "waiting_for_checks")
            self.assertIn("SELF_CORRELATION", state["jobs"][0]["last_missing_checks"])
            self.assertEqual(client.submits, [])

    def test_worker_requires_two_matching_complete_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_backlog.json",
                [{"alpha_id": "A1", "recommended_action": "submit"}],
            )
            client = FakeClient()
            client.checks.extend([complete_check_response(self_corr_value=0.4), complete_check_response(self_corr_value=0.41)])

            SubmissionWorker(run_dir, client=client, sleep=lambda _seconds: None).run_once()

            state = self._read_json(run_dir / "submission_state.json")
            self.assertEqual(state["jobs"][0]["status"], "waiting_for_checks")
            self.assertEqual(state["jobs"][0]["error"], "live_check_snapshot_not_stable")
            self.assertEqual(client.submits, [])

    def test_worker_rejects_post_accept_self_correlation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_state.json",
                {
                    "jobs": [
                        {
                            "alpha_id": "A1",
                            "status": "post_accepted",
                            "recommended_action": "submit",
                            "attempts": 1,
                        }
                    ]
                },
            )
            client = FakeClient()
            client.details.append({"status_code": 200, "status": "UNSUBMITTED", "dateSubmitted": None})
            client.checks.append(complete_check_response(self_corr_result="FAIL", self_corr_value=0.82))

            SubmissionWorker(
                run_dir,
                client=client,
                confirm_polls=1,
                sleep=lambda _seconds: None,
            ).run_once()

            state = self._read_json(run_dir / "submission_state.json")
            self.assertEqual(state["jobs"][0]["status"], "rejected")
            self.assertEqual(state["jobs"][0]["diagnosis_type"], "post_accept_check_failed")
            self.assertEqual(state["jobs"][0]["last_failed_checks"], ["SELF_CORRELATION"])
            self.assertEqual(client.submits, [])

    def test_worker_escalates_unconfirmed_acceptance_without_reposting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_state.json",
                {
                    "jobs": [
                        {
                            "alpha_id": "A1",
                            "status": "accepted_but_unconfirmed",
                            "recommended_action": "submit",
                            "attempts": 1,
                            "confirmation_attempts": 2,
                        }
                    ]
                },
            )
            client = FakeClient()
            client.details.append({"status_code": 200, "status": "UNSUBMITTED", "dateSubmitted": None})
            client.checks.append({"status_code": 200, "checks": [], "failed_checks": [], "pending_checks": []})

            worker = SubmissionWorker(
                run_dir,
                client=client,
                confirm_polls=1,
                max_confirmation_attempts=3,
                sleep=lambda _seconds: None,
            )
            result = worker.run_once()

            self.assertEqual(result["manual_review_or_platform_lag_count"], 1)
            state = self._read_json(run_dir / "submission_state.json")
            self.assertEqual(state["jobs"][0]["status"], "manual_review_or_platform_lag")
            self.assertEqual(state["jobs"][0]["confirmation_attempts"], 3)
            self.assertEqual(client.submits, [])

    def test_worker_confirms_delayed_active_and_records_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_state.json",
                {
                    "jobs": [
                        {
                            "alpha_id": "A1",
                            "status": "pending_confirmation",
                            "recommended_action": "submit",
                            "attempts": 1,
                        }
                    ]
                },
            )
            client = FakeClient()
            client.details.append({"status_code": 200, "status": "ACTIVE", "dateSubmitted": "2026-07-05T00:00:00-04:00"})

            worker = SubmissionWorker(run_dir, client=client, confirm_polls=1, sleep=lambda _seconds: None)
            result = worker.run_once()

            self.assertEqual(result["submitted_confirmed_count"], 1)
            state = self._read_json(run_dir / "submission_state.json")
            self.assertEqual(state["jobs"][0]["status"], "submitted_confirmed")
            self.assertEqual(client.submitted, ["A1"])

    def test_worker_resumes_throttled_confirmation_without_reposting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_state.json",
                {
                    "jobs": [
                        {
                            "alpha_id": "A1",
                            "status": "throttled_retry_later",
                            "retry_status": "accepted_but_unconfirmed",
                            "recommended_action": "submit",
                            "attempts": 1,
                        }
                    ]
                },
            )
            client = FakeClient()
            client.details.append(
                {"status_code": 200, "status": "ACTIVE", "dateSubmitted": "2026-07-12T00:00:00-04:00"}
            )

            SubmissionWorker(run_dir, client=client, confirm_polls=1, sleep=lambda _seconds: None).run_once()

            state = self._read_json(run_dir / "submission_state.json")
            self.assertEqual(state["jobs"][0]["status"], "submitted_confirmed")
            self.assertEqual(client.submits, [])
            self.assertEqual(client.submitted, ["A1"])

    def test_worker_defers_throttled_live_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_backlog.json",
                [{"alpha_id": "A1", "recommended_action": "live_recheck_then_submit"}],
            )
            client = FakeClient()
            client.checks.append({"status_code": 429, "retry_after_seconds": 90, "failed_checks": [], "pending_checks": []})

            worker = SubmissionWorker(run_dir, client=client, sleep=lambda _seconds: None)
            result = worker.run_once()

            self.assertEqual(result["throttled_count"], 1)
            state = self._read_json(run_dir / "submission_state.json")
            self.assertEqual(state["jobs"][0]["status"], "throttled_retry_later")
            self.assertEqual(state["jobs"][0]["retry_after_seconds"], 90)

    def test_worker_treats_already_submitted_live_check_as_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_backlog.json",
                [{"alpha_id": "A1", "recommended_action": "live_recheck_then_submit"}],
            )
            client = FakeClient()
            client.checks.append({"status_code": 200, "failed_checks": ["ALREADY_SUBMITTED"], "pending_checks": []})

            worker = SubmissionWorker(run_dir, client=client, sleep=lambda _seconds: None)
            result = worker.run_once()

            self.assertEqual(result["submitted_confirmed_count"], 1)
            state = self._read_json(run_dir / "submission_state.json")
            self.assertEqual(state["jobs"][0]["status"], "submitted_confirmed")
            self.assertEqual(client.submits, [])
            self.assertEqual(client.submitted, ["A1"])

    def test_worker_treats_submit_already_submitted_response_as_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self._write_json(
                run_dir / "submission_backlog.json",
                [{"alpha_id": "A1", "recommended_action": "submit"}],
            )
            client = FakeClient()
            client.checks.extend([complete_check_response(), complete_check_response()])
            client.submits.append({"status_code": 403, "ok": False, "text": '{"is":{"checks":[{"name":"ALREADY_SUBMITTED"}]}}'})

            worker = SubmissionWorker(run_dir, client=client, sleep=lambda _seconds: None)
            result = worker.run_once()

            self.assertEqual(result["submitted_confirmed_count"], 1)
            state = self._read_json(run_dir / "submission_state.json")
            self.assertEqual(state["jobs"][0]["status"], "submitted_confirmed")
            self.assertEqual(client.submitted, ["A1"])

    def test_brain_submission_client_maps_normalized_submit_result(self) -> None:
        client = BrainSubmissionClient(wqb_client=FakeWQBClient())

        result = client.submit("A1")

        self.assertEqual(result["status_code"], 201)
        self.assertEqual(result["post_status"], "accepted")
        self.assertEqual(result["confirmation_status"], "still_unsubmitted")
        self.assertEqual(result["text"], "post_accepted_but_still_unsubmitted")

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
