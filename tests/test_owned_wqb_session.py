from __future__ import annotations

import asyncio
import threading
import unittest
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import requests

from wqb_agent_lab.runtime import OperationJournal, SideEffectUncertainError

from wqb_agent_lab.platform.session import (
    URL_AUTHENTICATION,
    WQBAuthenticationError,
    WQBSession,
)


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


class OwnedWQBSessionTests(unittest.TestCase):
    def test_authentication_uses_basic_auth_and_keeps_credentials_out_of_url(self) -> None:
        response = FakeResponse(201, {})
        with patch.object(requests.Session, "request", return_value=response) as request:
            session = WQBSession(("researcher@example.com", "secret"), sleep=lambda _: None)

        self.assertTrue(session._authenticated)
        method, url = request.call_args.args[:2]
        self.assertEqual("POST", method)
        self.assertEqual(URL_AUTHENTICATION, url)
        self.assertEqual(("researcher@example.com", "secret"), request.call_args.kwargs["auth"])
        self.assertNotIn("secret", url)

    def test_authentication_fails_closed_after_bounded_retries(self) -> None:
        with patch.object(requests.Session, "request", return_value=FakeResponse(401, text="denied")) as request:
            with self.assertRaises(WQBAuthenticationError):
                WQBSession(
                    ("researcher@example.com", "secret"),
                    auth_max_tries=2,
                    auth_delay_unexpected=0,
                )

        self.assertEqual(2, request.call_count)

    def test_dataset_pagination_uses_structured_query_parameters(self) -> None:
        responses = [
            FakeResponse(200, {"count": 2, "results": []}),
            FakeResponse(200, {"results": [{"id": "D1"}, {"id": "D2"}]}),
        ]
        with patch.object(requests.Session, "request", side_effect=responses) as request:
            session = WQBSession(("researcher@example.com", "secret"), auto_authenticate=False)
            session._authenticated = True
            pages = list(session.search_datasets("USA", 1, "TOP3000", limit=2, theme=True))

        self.assertEqual(1, len(pages))
        count_url = request.call_args_list[0].args[1]
        page_url = request.call_args_list[1].args[1]
        for url in (count_url, page_url):
            self.assertIn("region=USA", url)
            self.assertIn("delay=1", url)
            self.assertIn("universe=TOP3000", url)
            self.assertIn("theme=true", url)
        self.assertIn("limit=1", count_url)
        self.assertIn("limit=2", page_url)

    def test_dataset_transport_options_are_not_encoded_as_filters(self) -> None:
        response = FakeResponse(200, {"results": []})
        with patch.object(requests.Session, "request", return_value=response) as request:
            session = WQBSession(("researcher@example.com", "secret"), auto_authenticate=False)
            session._authenticated = True
            session.search_datasets_limited(
                "USA",
                1,
                "TOP3000",
                theme=True,
                timeout=7,
                headers={"X-Research-Trace": "trace-1"},
            )

        url = request.call_args.args[1]
        self.assertIn("theme=true", url)
        self.assertNotIn("timeout", url)
        self.assertNotIn("headers", url)
        self.assertEqual(7, request.call_args.kwargs["timeout"])
        self.assertEqual({"X-Research-Trace": "trace-1"}, request.call_args.kwargs["headers"])

    def test_explicit_request_kwargs_take_precedence(self) -> None:
        response = FakeResponse(200, {"results": []})
        with patch.object(requests.Session, "request", return_value=response) as request:
            session = WQBSession(("researcher@example.com", "secret"), auto_authenticate=False)
            session._authenticated = True
            session.filter_alphas_limited(
                status="UNSUBMITTED",
                timeout=3,
                request_kwargs={"timeout": 9, "verify": False},
            )

        url = request.call_args.args[1]
        self.assertIn("status=UNSUBMITTED", url)
        self.assertNotIn("timeout", url)
        self.assertEqual(9, request.call_args.kwargs["timeout"])
        self.assertFalse(request.call_args.kwargs["verify"])

    def test_check_and_submit_use_https_platform_endpoints(self) -> None:
        responses = [FakeResponse(200, {}), FakeResponse(201, {})]
        with patch.object(requests.Session, "request", side_effect=responses) as request:
            session = WQBSession(("researcher@example.com", "secret"), auto_authenticate=False)
            session._authenticated = True
            asyncio.run(session.check("A1", max_tries=1))
            asyncio.run(session.submit("A1", max_tries=1))

        calls = [(call.args[0], call.args[1]) for call in request.call_args_list]
        self.assertEqual(
            [
                ("GET", "https://api.worldquantbrain.com/alphas/A1/check"),
                ("POST", "https://api.worldquantbrain.com/alphas/A1/submit"),
            ],
            calls,
        )

    def test_simulation_does_not_repeat_success_without_location(self) -> None:
        response = FakeResponse(201, {})
        with patch.object(requests.Session, "request", return_value=response) as request:
            session = WQBSession(("researcher@example.com", "secret"), auto_authenticate=False)
            session._authenticated = True
            result = session.create_simulation({"type": "REGULAR"}, max_tries=5)

        self.assertIs(response, result)
        self.assertEqual(1, request.call_count)

    def test_simulation_does_not_repeat_ambiguous_server_error(self) -> None:
        response = FakeResponse(503, {"detail": "upstream failed"})
        with patch.object(requests.Session, "request", return_value=response) as request:
            session = WQBSession(("researcher@example.com", "secret"), auto_authenticate=False)
            session._authenticated = True
            result = session.create_simulation({"type": "REGULAR"}, max_tries=5)

        self.assertIs(response, result)
        self.assertEqual(1, request.call_count)

    def test_simulation_retries_throttle_then_returns_location(self) -> None:
        responses = [
            FakeResponse(429, {"detail": "throttled"}, headers={"Retry-After": "0"}),
            FakeResponse(201, {}, headers={"Location": "/simulations/S1"}),
        ]
        with patch.object(requests.Session, "request", side_effect=responses) as request:
            session = WQBSession(("researcher@example.com", "secret"), auto_authenticate=False, sleep=lambda _: None)
            session._authenticated = True
            result = session.create_simulation({"type": "REGULAR"}, max_tries=5)

        self.assertEqual("/simulations/S1", result.headers["Location"])
        self.assertEqual(2, request.call_count)

    def test_simulation_journal_distinguishes_connect_from_read_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = OperationJournal(Path(tmp) / "operations.db")
            responses = [requests.ConnectTimeout("connect"), FakeResponse(201, {}, headers={"Location": "/simulations/S1"})]
            with patch.object(requests.Session, "request", side_effect=responses) as request:
                session = WQBSession(
                    ("researcher@example.com", "secret"),
                    auto_authenticate=False,
                    sleep=lambda _: None,
                    operation_journal=journal,
                    run_id="run-1",
                )
                session._authenticated = True
                result = session.create_simulation({"type": "REGULAR"}, max_tries=2)
            self.assertEqual("/simulations/S1", result.headers["Location"])
            self.assertEqual(2, request.call_count)

            with patch.object(requests.Session, "request", side_effect=requests.ReadTimeout("read")):
                with self.assertRaises(SideEffectUncertainError) as raised:
                    session.create_simulation({"type": "REGULAR"}, max_tries=2)
            self.assertEqual("read_timeout_after_send", raised.exception.record.reason)
            self.assertEqual(1, len(journal.unresolved("simulation.create")))

    def test_submit_does_not_repeat_accepted_post_with_retry_after(self) -> None:
        response = FakeResponse(201, {}, headers={"Retry-After": "5"})
        with patch.object(requests.Session, "request", return_value=response) as request:
            session = WQBSession(("researcher@example.com", "secret"), auto_authenticate=False)
            session._authenticated = True
            result = asyncio.run(session.submit("A1", max_tries=5))

        self.assertIs(response, result)
        self.assertEqual(1, request.call_count)

    def test_concurrent_simulate_enters_transport_in_parallel(self) -> None:
        barrier = threading.Barrier(3, timeout=2.0)

        class ProbeSession(WQBSession):
            def create_simulation(self, target, *args, **kwargs):
                barrier.wait()
                return FakeResponse(201, {}, headers={"Location": f"/simulations/{target['id']}"})

            def get(self, url, *args, **kwargs):
                return FakeResponse(200, {"alpha": str(url).rsplit("/", 1)[-1]})

        session = ProbeSession(("researcher@example.com", "secret"), auto_authenticate=False)
        session._authenticated = True
        results = asyncio.run(
            session.concurrent_simulate(
                [{"id": "S1"}, {"id": "S2"}, {"id": "S3"}],
                concurrency=3,
            )
        )

        self.assertEqual(3, len(results))
        self.assertTrue(all(result is not None and result.ok for result in results))


if __name__ == "__main__":
    unittest.main()
