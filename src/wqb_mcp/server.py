from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.wqb_agent_lab.platform import WQBClient


P0_TOOL_NAMES = (
    "wqb_auth_status",
    "wqb_get_alpha",
    "wqb_get_alpha_checks",
    "wqb_create_simulation",
    "wqb_poll_simulation",
    "wqb_get_user_alphas",
    "wqb_list_operators",
)


def tool_names() -> tuple[str, ...]:
    return P0_TOOL_NAMES


def build_tool_handlers(client: Any) -> dict[str, Callable[..., Any]]:
    def wqb_auth_status() -> dict[str, Any]:
        return _as_dict(client.auth_status())

    def wqb_get_alpha(alpha_id: str) -> dict[str, Any]:
        return _as_dict(client.get_alpha(alpha_id))

    def wqb_get_alpha_checks(alpha_id: str) -> list[dict[str, Any]]:
        return [_as_dict(item) for item in client.get_alpha_checks(alpha_id)]

    def wqb_create_simulation(payload: dict[str, Any]) -> dict[str, Any]:
        return _as_dict(client.create_simulation(payload))

    def wqb_poll_simulation(location: str) -> dict[str, Any]:
        return _as_dict(client.poll_simulation(location))

    def wqb_get_user_alphas(**kwargs: Any) -> dict[str, Any]:
        return _as_dict(client.get_user_alphas(**kwargs))

    def wqb_list_operators() -> list[dict[str, Any]]:
        return [_as_dict(item) for item in client.list_operators()]

    return {
        "wqb_auth_status": wqb_auth_status,
        "wqb_get_alpha": wqb_get_alpha,
        "wqb_get_alpha_checks": wqb_get_alpha_checks,
        "wqb_create_simulation": wqb_create_simulation,
        "wqb_poll_simulation": wqb_poll_simulation,
        "wqb_get_user_alphas": wqb_get_user_alphas,
        "wqb_list_operators": wqb_list_operators,
    }


def create_mcp_server(client: WQBClient | None = None) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install the optional 'mcp' package to run the WQB MCP server.") from exc

    mcp = FastMCP("wqb-research")
    handlers = build_tool_handlers(client or WQBClient.from_config())
    for name in tool_names():
        mcp.tool(name=name)(handlers[name])
    return mcp


def _as_dict(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    return value
