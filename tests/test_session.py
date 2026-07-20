"""Phase 2 API 会话封装测试。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from wqb_agent_lab.platform.research_session import (
    BrainAPIError,
    BrainSession,
    RetryPolicy,
)


class FakeResponse:
    """用于单元测试的轻量响应对象。"""

    def __init__(
        self,
        *,
        ok: bool = True,
        status_code: int = 200,
        json_data=None,
        text: str = "",
        reason: str = "OK",
    ) -> None:
        self.ok = ok
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text
        self.reason = reason

    def json(self):
        return self._json_data


class BrainSessionTests(unittest.TestCase):
    def test_search_operators_retries_after_transient_failure(self) -> None:
        raw_session = MagicMock()
        raw_session.search_operators.side_effect = [
            RuntimeError("temporary"),
            FakeResponse(json_data=[{"name": "rank"}]),
        ]
        session = BrainSession(raw_session, RetryPolicy(max_attempts=2, backoff_seconds=0))

        operators = session.search_operators()

        self.assertEqual([{"name": "rank"}], operators)
        self.assertEqual(2, raw_session.search_operators.call_count)

    def test_search_datasets_aggregates_results_from_all_pages(self) -> None:
        raw_session = MagicMock()
        raw_session.search_datasets.return_value = iter(
            [
                FakeResponse(json_data={"results": [{"id": "pv1"}]}),
                FakeResponse(json_data={"results": [{"id": "model1"}]}),
            ]
        )
        session = BrainSession(raw_session, RetryPolicy(max_attempts=1, backoff_seconds=0))

        datasets = session.search_datasets("USA", 1, "TOP3000")

        self.assertEqual([{"id": "pv1"}, {"id": "model1"}], datasets)

    def test_filter_alphas_aggregates_results(self) -> None:
        raw_session = MagicMock()
        raw_session.filter_alphas.return_value = iter(
            [
                FakeResponse(json_data={"results": [{"id": "alpha-1"}]}),
                FakeResponse(json_data={"results": [{"id": "alpha-2"}]}),
            ]
        )
        session = BrainSession(raw_session, RetryPolicy(max_attempts=1, backoff_seconds=0))

        alphas = session.filter_alphas(status="UNSUBMITTED")

        self.assertEqual([{"id": "alpha-1"}, {"id": "alpha-2"}], alphas)

    def test_validate_session_returns_false_when_response_not_ok(self) -> None:
        raw_session = MagicMock()
        raw_session.head_authentication.return_value = FakeResponse(
            ok=False,
            status_code=401,
            text="expired",
            reason="Unauthorized",
        )
        session = BrainSession(raw_session, RetryPolicy(max_attempts=1, backoff_seconds=0))

        self.assertFalse(session.validate_session())

    def test_locate_dataset_raises_brain_api_error_on_http_failure(self) -> None:
        raw_session = MagicMock()
        raw_session.locate_dataset.return_value = FakeResponse(
            ok=False,
            status_code=500,
            text="server error",
            reason="Server Error",
        )
        session = BrainSession(raw_session, RetryPolicy(max_attempts=1, backoff_seconds=0))

        with self.assertRaises(BrainAPIError):
            session.locate_dataset("pv1")


if __name__ == "__main__":
    unittest.main()
