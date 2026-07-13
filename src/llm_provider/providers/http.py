from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import requests

from ..errors import LLMProviderError


def _error_payload(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"body": response.text[:1000]}
    if isinstance(payload, Mapping):
        return dict(payload)
    return {"body": payload}


def _provider_message(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message
    return "Provider returned an HTTP error."


def _is_context_length_error(payload: Mapping[str, Any]) -> bool:
    error = payload.get("error")
    candidates: list[str] = []
    if isinstance(error, Mapping):
        candidates.extend(
            str(error.get(key, "")) for key in ("code", "type", "message")
        )
    candidates.extend(str(payload.get(key, "")) for key in ("code", "message"))
    normalized = " ".join(candidates).lower().replace("-", "_")
    return (
        "context_length" in normalized
        or "maximum context" in normalized
        or "prompt is too long" in normalized
        or ("tokens >" in normalized and "maximum" in normalized)
    )


def _post_json_with_session(
    *,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout_seconds: float,
    provider: str,
    model: str,
    secrets: tuple[str, ...] = (),
    session: requests.Session,
) -> dict[str, Any]:
    try:
        response = session.post(
            url,
            headers=dict(headers),
            json=dict(payload),
            timeout=timeout_seconds,
        )
    except requests.Timeout as exc:
        raise LLMProviderError(
            code="timeout",
            message="Provider request timed out.",
            provider=provider,
            model=model,
            retryable=True,
            secrets=secrets,
        ) from exc
    except requests.ConnectionError as exc:
        raise LLMProviderError(
            code="connection_error",
            message="Could not connect to the provider.",
            provider=provider,
            model=model,
            retryable=True,
            secrets=secrets,
        ) from exc
    except requests.RequestException as exc:
        raise LLMProviderError(
            code="provider_error",
            message="Provider request failed.",
            provider=provider,
            model=model,
            retryable=True,
            details={"exception_type": type(exc).__name__},
            secrets=secrets,
        ) from exc

    if response.status_code >= 400:
        error_payload = _error_payload(response)
        if response.status_code in {401, 403}:
            code = "authentication_error"
            retryable = False
        elif response.status_code == 429:
            code = "rate_limited"
            retryable = True
        elif _is_context_length_error(error_payload):
            code = "context_length_exceeded"
            retryable = False
        else:
            code = "provider_error"
            retryable = response.status_code >= 500
        raise LLMProviderError(
            code=code,
            message=_provider_message(error_payload),
            provider=provider,
            model=model,
            retryable=retryable,
            status_code=response.status_code,
            details={"response": error_payload},
            secrets=secrets,
        )

    try:
        response_payload = response.json()
    except ValueError as exc:
        raise LLMProviderError(
            code="invalid_response",
            message="Provider response is not valid JSON.",
            provider=provider,
            model=model,
            status_code=response.status_code,
            secrets=secrets,
        ) from exc
    if not isinstance(response_payload, Mapping):
        raise LLMProviderError(
            code="invalid_response",
            message="Provider response must be a JSON object.",
            provider=provider,
            model=model,
            status_code=response.status_code,
            details={"response_type": type(response_payload).__name__},
            secrets=secrets,
        )
    return dict(response_payload)


def post_json_once(
    *,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout_seconds: float,
    provider: str,
    model: str,
    secrets: tuple[str, ...] = (),
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """POST one JSON request and normalize transport and HTTP failures."""
    owns_session = session is None
    active_session = session or requests.Session()
    try:
        return _post_json_with_session(
            url=url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
            provider=provider,
            model=model,
            secrets=secrets,
            session=active_session,
        )
    finally:
        if owns_session:
            active_session.close()
