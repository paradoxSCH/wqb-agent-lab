from __future__ import annotations

import unittest
from typing import Any

from wqb_agent_lab.mcp.server import build_tool_handlers, tool_names


class FakeClient:
    def __init__(self) -> None:
        self.submitted: list[str] = []

    def auth_status(self) -> dict[str, Any]:
        return {"authenticated": True}

    def get_alpha(self, alpha_id: str) -> Any:
        return {"alpha_id": alpha_id, "status": "UNSUBMITTED"}

    def get_alpha_checks(self, alpha_id: str) -> list[Any]:
        return []

    def create_simulation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"location": "/simulations/1", "simulation_id": "1", "success": True}

    def poll_simulation(self, location: str) -> dict[str, Any]:
        return {"location": location, "status": "COMPLETE"}

    def get_user_alphas(self, **kwargs: Any) -> dict[str, Any]:
        return {"results": [], "count": 0, "params": kwargs}

    def list_operators(self) -> list[dict[str, Any]]:
        return [{"name": "rank"}]


class WQBMCPServerTests(unittest.TestCase):
    def test_tool_names_are_stable_p0_surface(self) -> None:
        self.assertEqual(
            tool_names(),
            (
                "wqb_auth_status",
                "wqb_get_alpha",
                "wqb_get_alpha_checks",
                "wqb_create_simulation",
                "wqb_poll_simulation",
                "wqb_get_user_alphas",
                "wqb_list_operators",
            ),
        )

    def test_raw_submit_tool_is_not_exposed(self) -> None:
        client = FakeClient()
        handlers = build_tool_handlers(client)

        self.assertNotIn("wqb_submit_alpha", handlers)


if __name__ == "__main__":
    unittest.main()
