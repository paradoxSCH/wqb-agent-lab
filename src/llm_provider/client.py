from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from .errors import LLMProviderError
from .models import LLMRequest, LLMResponse


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def model(self) -> str: ...

    def complete(self, request: LLMRequest) -> LLMResponse: ...


def validate_structured_content(content: str) -> dict[str, Any] | list[Any]:
    if not isinstance(content, str) or not content.strip():
        raise LLMProviderError(
            code="invalid_structured_output",
            message="Structured response content is empty.",
        )
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise LLMProviderError(
            code="invalid_structured_output",
            message="Structured response is not valid JSON.",
            details={"reason": str(exc)},
        ) from exc
    if not isinstance(parsed, (dict, list)):
        raise LLMProviderError(
            code="invalid_structured_output",
            message="Structured response must be a JSON object or array.",
            details={"json_type": type(parsed).__name__},
        )
    return parsed
