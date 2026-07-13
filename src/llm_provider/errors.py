from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ERROR_CODES = frozenset(
    {
        "authentication_error",
        "rate_limited",
        "timeout",
        "connection_error",
        "provider_error",
        "invalid_response",
        "invalid_structured_output",
        "context_length_exceeded",
        "unsupported_capability",
        "invalid_configuration",
        "process_error",
    }
)

_SENSITIVE_QUERY_NAMES = frozenset(
    {
        "apikey",
        "accesstoken",
        "authtoken",
        "authorization",
        "authentication",
        "clientsecret",
        "credential",
        "credentials",
        "key",
        "password",
        "secret",
        "signature",
        "token",
    }
)
_SENSITIVE_QUERY_PARTS = frozenset(
    {"auth", "credential", "key", "password", "secret", "signature", "token"}
)
INVALID_URL_MARKER = "<redacted-invalid-url>"


def _sensitive_query_name(name: str) -> bool:
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    parts = tuple(part for part in re.split(r"[^A-Za-z0-9]+", snake_case.lower()) if part)
    compact = "".join(parts)
    return compact in _SENSITIVE_QUERY_NAMES or any(
        part in _SENSITIVE_QUERY_PARTS
        or any(
            part.startswith(prefix)
            for prefix in ("authenticat", "authoriz", "credential", "signature")
        )
        for part in parts
    )


def sanitize_url(url: str) -> str:
    """Redact URL credentials while preserving endpoint and benign query data."""
    if not isinstance(url, str):
        return INVALID_URL_MARKER
    if not url:
        return url
    try:
        if any(character.isspace() or ord(character) < 32 for character in url):
            return INVALID_URL_MARKER
        if re.search(r"%(?![0-9A-Fa-f]{2})", url):
            return INVALID_URL_MARKER
        parsed = urlsplit(url)
        if not parsed.scheme or not parsed.netloc:
            return INVALID_URL_MARKER
        hostname = parsed.hostname
        if not hostname or any(character.isspace() for character in hostname):
            return INVALID_URL_MARKER
        parsed.port

        netloc = parsed.netloc
        if "@" in netloc:
            userinfo, host = netloc.rsplit("@", 1)
            if not userinfo or not host:
                return INVALID_URL_MARKER
            replacement = (
                "<redacted>:<redacted>" if ":" in userinfo else "<redacted>"
            )
            netloc = f"{replacement}@{host}"

        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        sanitized_query = urlencode(
            [
                (name, "<redacted>" if _sensitive_query_name(name) else value)
                for name, value in query_items
            ],
            doseq=True,
            safe="<>",
        )
        return urlunsplit((parsed.scheme, netloc, parsed.path, sanitized_query, ""))
    except Exception:
        return INVALID_URL_MARKER


def normalize_secrets(secrets: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({secret for secret in secrets if secret}, key=len, reverse=True))


def _redact_secrets(value: Any, active_secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        redacted = value
        patterns = {
            pattern
            for secret in active_secrets
            for pattern in (
                secret,
                json.dumps(secret)[1:-1],
                json.dumps(secret, ensure_ascii=False)[1:-1],
            )
            if pattern
        }
        for pattern in sorted(patterns, key=len, reverse=True):
            redacted = redacted.replace(pattern, "<redacted>")
        return redacted
    if isinstance(value, Mapping):
        return {
            str(_redact_secrets(str(key), active_secrets)): _redact_secrets(
                item, active_secrets
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_secrets(item, active_secrets) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_secrets(str(value), active_secrets)


def redact_secrets(value: Any, secrets: Sequence[str] = ()) -> Any:
    """Return a JSON-safe copy with every known non-empty secret removed."""
    return _redact_secrets(value, normalize_secrets(secrets))


class LLMProviderError(Exception):
    """A provider-independent error suitable for workflow policy decisions."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        provider: str | None = None,
        model: str | None = None,
        retryable: bool = False,
        status_code: int | None = None,
        details: Mapping[str, Any] | None = None,
        secrets: Sequence[str] = (),
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"Unknown LLM provider error code: {code}")
        self.code = code
        self.provider = provider
        self.model = model
        self.retryable = retryable
        self.status_code = status_code
        self._secrets = normalize_secrets(secrets)
        self.message = str(redact_secrets(message, self._secrets))
        redacted_details = redact_secrets(details or {}, self._secrets)
        self.details = redacted_details if isinstance(redacted_details, dict) else {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.provider is not None:
            payload["provider"] = self.provider
        if self.model is not None:
            payload["model"] = self.model
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.details:
            payload["details"] = redact_secrets(self.details, self._secrets)
        redacted = redact_secrets(payload, self._secrets)
        return redacted if isinstance(redacted, dict) else {}
