"""Phase 2 模拟接口测试。"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from wqb_agent_lab.runtime.config import Config, SimulationDefaults
from wqb_agent_lab.platform.simulation import simulate_batch, simulate_single


class FakeResponse:
    """用于单元测试的轻量响应对象。"""

    def __init__(
        self,
        *,
        ok: bool = True,
        status_code: int = 200,
        json_data=None,
        text: str = "",
        headers=None,
    ) -> None:
        self.ok = ok
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._json_data


class FakeSingleSession:
    """模拟单次异步接口的测试对象。"""

    def __init__(self, response, alpha_response=None) -> None:
        self._response = response
        self._alpha_response = alpha_response or FakeResponse(
            ok=True, json_data={"id": "a1", "is": {"sharpe": 1.5}}
        )
        self.expected_location = "Location"
        self.post_calls = 0

    def post(self, *_args, **_kwargs):
        self.post_calls += 1
        if self._response is None:
            return None
        return FakeResponse(ok=True, headers={"Location": "simulation/1"})

    def create_simulation(self, target, *_args, **_kwargs):
        return self.post("simulations", json=target)

    def get(self, url):
        if str(url).endswith("alpha-1"):
            return self._alpha_response
        return self._response


class FakeBatchSession:
    """模拟批量异步接口的测试对象。"""

    def __init__(self, responses, alpha_response=None) -> None:
        self._responses = responses
        self._alpha_response = alpha_response or FakeResponse(
            ok=True, json_data={"id": "a1", "is": {"sharpe": 1.5}}
        )

    async def concurrent_simulate(self, _targets, _concurrency, return_exceptions=False):
        return self._responses

    def get(self, _url):
        return self._alpha_response


class SimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capability = patch.dict(os.environ, {"WQB_LIVE_SIMULATION_CAPABILITY": "1"})
        self.capability.start()

    def tearDown(self) -> None:
        self.capability.stop()

    def test_simulate_single_refuses_before_post_when_capability_disabled(self) -> None:
        from wqb_agent_lab.governance.side_effects import SideEffectCapabilityDisabled

        session = FakeSingleSession(None)

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SideEffectCapabilityDisabled):
                asyncio.run(simulate_single(session, "rank(close)", {"region": "USA"}))

        self.assertEqual(session.post_calls, 0)

    def test_simulate_single_handles_none_response(self) -> None:
        session = FakeSingleSession(None)

        result = asyncio.run(
            simulate_single(session, "rank(close)", {"region": "USA"})
        )

        self.assertFalse(result["success"])
        self.assertIn("未返回结果", result["error"])

    def test_simulate_single_fetches_alpha_details(self) -> None:
        sim_resp = FakeResponse(
            ok=True,
            json_data={"id": "sim-1", "alpha": "alpha-1", "status": "COMPLETE"},
        )
        alpha_resp = FakeResponse(
            ok=True,
            json_data={
                "id": "alpha-1",
                "is": {"sharpe": 1.5, "fitness": 1.0, "turnover": 0.4},
            },
        )
        session = FakeSingleSession(sim_resp, alpha_response=alpha_resp)

        result = asyncio.run(
            simulate_single(session, "rank(close)", {"region": "USA"})
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["id"], "alpha-1")
        self.assertEqual(result["data"]["is"]["sharpe"], 1.5)

    def test_simulate_batch_collects_success_and_exception(self) -> None:
        config = Config(
            simulation=SimulationDefaults(),
            max_concurrency=2,
        )
        session = FakeBatchSession(
            [
                FakeResponse(ok=True, status_code=200, json_data={"id": "sim-1", "alpha": "alpha-1", "status": "COMPLETE"}),
                RuntimeError("boom"),
            ]
        )

        results = simulate_batch(session, ["alpha_a", "alpha_b"], config)

        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["success"])
        self.assertEqual(results[0]["expression"], "alpha_a")
        self.assertFalse(results[1]["success"])
        self.assertEqual("boom", results[1]["error"])
        self.assertEqual(results[1]["expression"], "alpha_b")


if __name__ == "__main__":
    unittest.main()
