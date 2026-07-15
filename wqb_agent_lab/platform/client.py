from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import requests

from src.config import Config, load_config

from .models import (
    WQBAlphaDetail,
    WQBCheck,
    WQBSubmitResult,
    WQBSimulationCreated,
    WQBSimulationRequest,
    extract_checks,
)
from .contracts import validate_read_contract, validate_simulation_create_contract
from .session import WQBSession


class WQBClient:
    def __init__(
        self,
        session: Any | None = None,
        *,
        base_url: str = "https://api.worldquantbrain.com",
        sleep: Callable[[float], None] = time.sleep,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.sleep = sleep
        self.request_timeout_seconds = request_timeout_seconds

    @classmethod
    def from_config(cls, config: Config | None = None) -> "WQBClient":
        cfg = config or load_config()
        if not cfg.email or not cfg.password:
            raise ValueError("WQB_EMAIL and WQB_PASSWORD must be configured in .env")
        session = WQBSession(
            (cfg.email, cfg.password),
            auth_max_tries=cfg.request_max_attempts,
            auth_delay_unexpected=cfg.request_backoff_seconds,
            request_timeout_seconds=30.0,
        )
        return cls(session=session, request_timeout_seconds=30.0)

    def auth_status(self) -> dict[str, Any]:
        response = self._request("GET", "/authentication")
        return {
            "authenticated": response.status_code == 200,
            "status_code": response.status_code,
        }

    def contract_probe(self) -> dict[str, Any]:
        """Run credentialed read-only API shape checks without simulation or submission."""
        probes = (
            ("/authentication", {}),
            ("/operators", {}),
            ("/users/self/alphas", {"params": {"limit": 1, "offset": 0}}),
        )
        issues: list[dict[str, str]] = []
        statuses: dict[str, int] = {}
        for endpoint, kwargs in probes:
            response = self._request("GET", endpoint, **kwargs)
            statuses[endpoint] = int(response.status_code)
            payload = _json_or_empty(response)
            issues.extend(issue.to_dict() for issue in validate_read_contract(endpoint, response.status_code, payload))
        return {"status": "ok" if not issues else "contract_drift", "http_statuses": statuses, "issues": issues}

    def get_alpha(self, alpha_id: str) -> WQBAlphaDetail:
        response = self._request("GET", f"/alphas/{alpha_id}")
        payload = _json_or_empty(response)
        return WQBAlphaDetail.from_payload(payload if isinstance(payload, dict) else {}, http_status=response.status_code)

    def get_alpha_checks(self, alpha_id: str) -> list[WQBCheck]:
        response = self._request("GET", f"/alphas/{alpha_id}/check")
        payload = _json_or_empty(response)
        return [WQBCheck.from_payload(item) for item in extract_checks(payload)]

    def submit_alpha(
        self,
        alpha_id: str,
        *,
        confirm_polls: int = 3,
        confirm_wait_seconds: float = 5.0,
    ) -> WQBSubmitResult:
        response = self._request("POST", f"/alphas/{alpha_id}/submit")
        retry_after = _retry_after_seconds(response)
        text = str(getattr(response, "text", "") or "")[:500]

        if response.status_code == 429:
            return WQBSubmitResult(
                alpha_id=alpha_id,
                post_status="throttled",
                confirmation_status="pending",
                retry_after_seconds=retry_after,
                diagnosis="submit_throttled",
                post_status_code=response.status_code,
                response_text=text,
            )
        if response.status_code == 401:
            return WQBSubmitResult(
                alpha_id=alpha_id,
                post_status="auth_failed",
                confirmation_status="failed",
                diagnosis="submit_auth_failed",
                post_status_code=response.status_code,
                response_text=text,
            )
        if response.status_code >= 400:
            return WQBSubmitResult(
                alpha_id=alpha_id,
                post_status="rejected",
                confirmation_status="failed",
                diagnosis="submit_http_rejected",
                post_status_code=response.status_code,
                response_text=text,
            )

        latest: WQBAlphaDetail | None = None
        polls = max(1, int(confirm_polls))
        for poll_index in range(polls):
            if poll_index > 0 and confirm_wait_seconds > 0:
                self.sleep(confirm_wait_seconds)
            latest = self.get_alpha(alpha_id)
            if latest.is_submitted:
                return WQBSubmitResult(
                    alpha_id=alpha_id,
                    post_status="accepted",
                    confirmation_status="confirmed",
                    platform_status=latest.status,
                    date_submitted=latest.date_submitted,
                    diagnosis="platform_submission_confirmed",
                    post_status_code=response.status_code,
                    detail_status_code=latest.http_status,
                    response_text=text,
                    checks=latest.checks,
                )

        return WQBSubmitResult(
            alpha_id=alpha_id,
            post_status="accepted",
            confirmation_status="still_unsubmitted" if latest and latest.status == "UNSUBMITTED" else "pending",
            platform_status=latest.status if latest else None,
            date_submitted=latest.date_submitted if latest else None,
            diagnosis="post_accepted_but_still_unsubmitted" if latest and latest.status == "UNSUBMITTED" else "post_accepted_pending_confirmation",
            post_status_code=response.status_code,
            detail_status_code=latest.http_status if latest else None,
            response_text=text,
            checks=latest.checks if latest else [],
        )

    def create_simulation(self, request: WQBSimulationRequest | dict[str, Any]) -> WQBSimulationCreated:
        payload = request.to_payload() if isinstance(request, WQBSimulationRequest) else dict(request)
        response = self._request("POST", "/simulations", json=payload)
        location = str(response.headers.get("Location") or "")
        location_url = self._absolute_url(location) if location else ""
        simulation_id = location_url.rstrip("/").split("/")[-1] if location_url else None
        return WQBSimulationCreated(
            success=200 <= response.status_code < 400 and not validate_simulation_create_contract(response.status_code, response.headers),
            location=location,
            simulation_id=simulation_id,
            status_code=response.status_code,
        )

    def poll_simulation(self, location: str) -> dict[str, Any]:
        response = self._request("GET", location)
        payload = _json_or_empty(response)
        return payload if isinstance(payload, dict) else {"results": payload}

    def run_simulation(
        self,
        request: WQBSimulationRequest | dict[str, Any],
        *,
        max_create_attempts: int = 12,
        default_create_retry_seconds: float = 5.0,
        max_polls: int = 600,
        default_poll_seconds: float = 2.0,
    ) -> dict[str, Any]:
        payload = request.to_payload() if isinstance(request, WQBSimulationRequest) else dict(request)
        attempts = max(1, int(max_create_attempts))
        response = None
        attempt = 0
        for attempt in range(1, attempts + 1):
            response = self._request("POST", "/simulations", json=payload)
            if response.status_code != 429 or attempt >= attempts:
                break
            retry_after = float(_retry_after_seconds(response) or 0)
            delay = max(retry_after, min(60.0, default_create_retry_seconds * attempt))
            self.sleep(delay)
        assert response is not None
        if not 200 <= response.status_code < 400:
            detail = _json_or_empty(response)
            return {
                "diagnosis": "simulation_create_failed",
                "status_code": response.status_code,
                "detail": detail,
                "create_attempts": attempt,
            }

        location = str(response.headers.get("Location") or "")
        if not location:
            return {
                "diagnosis": "simulation_location_missing",
                "status_code": response.status_code,
            }

        terminal_statuses = {"ERROR", "FAILED", "CANCELLED", "COMPLETE"}
        latest: dict[str, Any] = {}
        for _poll_index in range(max(1, int(max_polls))):
            poll_response = self._request("GET", location)
            poll_payload = _json_or_empty(poll_response)
            latest = poll_payload if isinstance(poll_payload, dict) else {"results": poll_payload}
            if latest.get("alpha"):
                return latest
            if poll_response.status_code >= 400:
                return {
                    **latest,
                    "diagnosis": "simulation_poll_failed",
                    "status_code": poll_response.status_code,
                }
            status = str(latest.get("status") or "").upper()
            if status in terminal_statuses or any(latest.get(key) for key in ("error", "message", "detail")):
                return latest
            delay = _retry_after_seconds(poll_response)
            self.sleep(float(delay) if delay is not None else default_poll_seconds)

        return {**latest, "diagnosis": "simulation_poll_budget_exhausted"}

    def get_user_alphas(self, **params: Any) -> dict[str, Any]:
        response = self._request("GET", "/users/self/alphas", params={k: v for k, v in params.items() if v is not None})
        payload = _json_or_empty(response)
        return payload if isinstance(payload, dict) else {"results": payload}

    def list_operators(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/operators")
        payload = _json_or_empty(response)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            return [item for item in payload["results"] if isinstance(item, dict)]
        return []

    def _request(self, method: str, path_or_url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self.request_timeout_seconds)
        return self.session.request(method, self._absolute_url(path_or_url), **kwargs)

    def _absolute_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return urljoin(f"{self.base_url}/", path_or_url.lstrip("/"))


def _json_or_empty(response: Any) -> Any:
    try:
        return response.json()
    except (AttributeError, ValueError):
        return {}


def _retry_after_seconds(response: Any) -> int | None:
    try:
        value = response.headers.get("Retry-After") or response.headers.get("retry-after")
    except AttributeError:
        value = None
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
