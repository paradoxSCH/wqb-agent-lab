from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import patch

import requests

from src.wqb_agent_lab.platform.session import (
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


if __name__ == "__main__":
    unittest.main()
