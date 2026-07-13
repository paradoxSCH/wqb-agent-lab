from __future__ import annotations

from ..errors import LLMProviderError


def require_nonempty_string(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LLMProviderError(
            code="invalid_configuration", message=f"{name} must be non-empty."
        )
    return value


def require_positive_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise LLMProviderError(
            code="invalid_configuration", message="timeout_seconds must be positive."
        )
    return float(value)
