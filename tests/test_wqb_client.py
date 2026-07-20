from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import requests

from wqb_agent_lab.platform import WQBAlphaDetail, WQBClient, is_submitted_status
from wqb_agent_lab.platform.session import WQBSession
from wqb_agent_lab.runtime import OperationJournal, SideEffectUncertainError


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: Any | None = None,
        *,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self.ok = 200 <= status_code < 400
        self.reason = "OK" if self.ok else "ERROR"

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.headers: dict[str, str] = {}
        self.cookies: dict[str, str] = {}

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected request {method} {url}")
        return self.responses.pop(0)


def alpha_payload(
    *,
    status: str = "UNSUBMITTED",
    date_submitted: str | None = None,
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": "A1",
        "status": status,
        "dateSubmitted": date_submitted,
        "regular": {"code": "rank(close)"},
        "is": {
            "sharpe": 1.5,
            "fitness": 1.2,
            "turnover": 0.1,
            "returns": 0.08,
            "drawdown": 0.04,
            "margin": 0.001,
            "checks": checks
            or [
                {"name": "LOW_SHARPE", "result": "PASS", "limit": 1.25, "value": 1.5},
                {"name": "SELF_CORRELATION", "result": "PASS", "limit": 0.7, "value": 0.2},
            ],
        },
    }


class WQBModelTests(unittest.TestCase):
    def test_alpha_detail_parses_metrics_expression_and_checks(self) -> None:
        detail = WQBAlphaDetail.from_payload(alpha_payload(status="ACTIVE", date_submitted="2026-07-04"), http_status=200)

        self.assertEqual(detail.alpha_id, "A1")
        self.assertEqual(detail.status, "ACTIVE")
        self.assertEqual(detail.date_submitted, "2026-07-04")
        self.assertEqual(detail.expression, "rank(close)")
        self.assertEqual(detail.metrics["sharpe"], 1.5)
        self.assertEqual(detail.checks[1].name, "SELF_CORRELATION")
        self.assertTrue(detail.is_submitted)

    def test_is_submitted_status_accepts_status_or_date(self) -> None:
        self.assertTrue(is_submitted_status("ACTIVE", None))
        self.assertTrue(is_submitted_status("UNSUBMITTED", "2026-07-04"))
        self.assertFalse(is_submitted_status("UNSUBMITTED", None))


class WQBClientSimulationTests(unittest.TestCase):
    def test_run_simulation_journals_read_timeout_without_replaying_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = OperationJournal(Path(tmp) / "operations.db")
            session = WQBSession(
                ("researcher@example.com", "secret"),
                auto_authenticate=False,
                operation_journal=journal,
                run_id="run-uncertain",
            )
            session._authenticated = True
            client = WQBClient(session=session, sleep=lambda _seconds: None)

            with patch.object(requests.Session, "request", side_effect=requests.ReadTimeout("read")) as request:
                with self.assertRaises(SideEffectUncertainError):
                    client.run_simulation(
                        {"type": "REGULAR", "settings": {}, "regular": "rank(close)"},
                        max_create_attempts=4,
                    )

            self.assertEqual(1, request.call_count)
            self.assertEqual(1, len(journal.unresolved("simulation.create")))

    def test_run_simulation_treats_success_without_location_as_unknown_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = OperationJournal(Path(tmp) / "operations.db")
            session = WQBSession(
                ("researcher@example.com", "secret"),
                auto_authenticate=False,
                operation_journal=journal,
                run_id="run-missing-location",
            )
            session._authenticated = True
            client = WQBClient(session=session, sleep=lambda _seconds: None)

            with patch.object(
                requests.Session,
                "request",
                return_value=FakeResponse(201, {}),
            ) as request:
                with self.assertRaises(SideEffectUncertainError) as raised:
                    client.run_simulation(
                        {"type": "REGULAR", "settings": {}, "regular": "rank(close)"},
                        max_create_attempts=4,
                    )

            self.assertEqual(1, request.call_count)
            self.assertEqual("success_without_location", raised.exception.record.reason)

    def test_run_simulation_retries_throttled_creation(self) -> None:
        session = FakeSession(
            [
                FakeResponse(429, {"detail": "too many simulations"}, headers={"Retry-After": "0"}),
                FakeResponse(201, headers={"Location": "/simulations/S1"}),
                FakeResponse(200, {"alpha": "A1", "status": "COMPLETE"}),
            ]
        )
        client = WQBClient(session=session, sleep=lambda _seconds: None)

        result = client.run_simulation(
            {"type": "REGULAR", "settings": {}, "regular": "rank(close)"},
            max_create_attempts=2,
        )

        self.assertEqual("A1", result["alpha"])
        self.assertEqual(3, len(session.requests))

    def test_run_simulation_polls_until_alpha_is_available(self) -> None:
        session = FakeSession(
            [
                FakeResponse(201, headers={"Location": "/simulations/S1"}),
                FakeResponse(200, {"progress": 0.5}, headers={"Retry-After": "0"}),
                FakeResponse(200, {"alpha": "A1", "status": "COMPLETE"}),
            ]
        )
        client = WQBClient(session=session, sleep=lambda _seconds: None)

        result = client.run_simulation({"type": "REGULAR", "settings": {}, "regular": "rank(close)"})

        self.assertEqual("A1", result["alpha"])
        self.assertEqual(3, len(session.requests))

    def test_run_simulation_returns_creation_failure_diagnosis(self) -> None:
        session = FakeSession([FakeResponse(400, {"error": "invalid expression"})])
        client = WQBClient(session=session, sleep=lambda _seconds: None)

        result = client.run_simulation({"type": "REGULAR", "settings": {}, "regular": "bad(close)"})

        self.assertEqual("simulation_create_failed", result["diagnosis"])
        self.assertEqual(400, result["status_code"])


class WQBClientSubmissionTests(unittest.TestCase):
    def test_submit_read_timeout_surfaces_durable_unknown_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = OperationJournal(Path(tmp) / "operations.db")
            session = WQBSession(
                ("researcher@example.com", "secret"),
                auto_authenticate=False,
                operation_journal=journal,
                run_id="submission-run",
            )
            session._authenticated = True
            client = WQBClient(session=session, sleep=lambda _: None)

            with patch.object(requests.Session, "request", side_effect=requests.ReadTimeout("read")) as request:
                with self.assertRaises(SideEffectUncertainError):
                    client.submit_alpha("A1")

            self.assertEqual(1, request.call_count)
            self.assertEqual("unknown_commit", journal.unresolved("submission.create")[0].outcome)

    def test_submit_201_still_unsubmitted_is_not_confirmed(self) -> None:
        session = FakeSession([
            FakeResponse(201, None),
            FakeResponse(200, alpha_payload(status="UNSUBMITTED")),
        ])
        result = WQBClient(session=session, sleep=lambda _: None).submit_alpha("A1", confirm_polls=1)

        self.assertEqual(result.post_status, "accepted")
        self.assertEqual(result.confirmation_status, "still_unsubmitted")
        self.assertEqual(result.platform_status, "UNSUBMITTED")
        self.assertFalse(result.submitted)

    def test_submit_201_active_detail_is_confirmed(self) -> None:
        session = FakeSession([
            FakeResponse(201, None),
            FakeResponse(200, alpha_payload(status="ACTIVE", date_submitted="2026-07-04")),
        ])
        result = WQBClient(session=session, sleep=lambda _: None).submit_alpha("A1", confirm_polls=1)

        self.assertEqual(result.post_status, "accepted")
        self.assertEqual(result.confirmation_status, "confirmed")
        self.assertEqual(result.platform_status, "ACTIVE")
        self.assertEqual(result.date_submitted, "2026-07-04")
        self.assertTrue(result.submitted)

    def test_submit_429_returns_throttled_with_retry_after(self) -> None:
        session = FakeSession([
            FakeResponse(429, {"detail": "THROTTLED"}, headers={"Retry-After": "300"}),
        ])
        result = WQBClient(session=session, sleep=lambda _: None).submit_alpha("A1", confirm_polls=1)

        self.assertEqual(result.post_status, "throttled")
        self.assertEqual(result.confirmation_status, "pending")
        self.assertEqual(result.retry_after_seconds, 300)
        self.assertEqual(result.diagnosis, "submit_throttled")

    def test_get_alpha_checks_reads_nested_is_checks(self) -> None:
        session = FakeSession([FakeResponse(200, alpha_payload())])
        checks = WQBClient(session=session).get_alpha_checks("A1")

        self.assertEqual([check.name for check in checks], ["LOW_SHARPE", "SELF_CORRELATION"])


if __name__ == "__main__":
    unittest.main()
