from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import requests

from ..client import validate_structured_content
from ..errors import LLMProviderError
from ..models import LLMRequest, LLMResponse, LLMUsage
from .common import require_nonempty_string, require_positive_timeout
from .http import post_json_once


class OllamaProvider:
    provider_id = "ollama"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 180,
        session: requests.Session | None = None,
    ) -> None:
        self._model = require_nonempty_string(model, "model")
        self._base_url = require_nonempty_string(base_url, "base_url").rstrip("/")
        self._timeout_seconds = require_positive_timeout(timeout_seconds)
        self._session = session

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        if request.response_format == "json":
            payload["format"] = "json"
        response = post_json_once(
            url=f"{self._base_url}/api/chat",
            headers={"Content-Type": "application/json"},
            payload=payload,
            timeout_seconds=request.timeout_seconds or self._timeout_seconds,
            provider=self.provider_id,
            model=self.model,
            session=self._session,
        )
        message = response.get("message")
        if not isinstance(message, Mapping):
            raise self._invalid("Provider response has no message object.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise self._invalid("Provider returned empty response content.")
        if request.response_format == "json":
            try:
                validate_structured_content(content)
            except LLMProviderError as exc:
                raise LLMProviderError(
                    code=exc.code,
                    message=exc.message,
                    provider=self.provider_id,
                    model=self.model,
                    details=exc.details,
                ) from exc
        finish_reason = response.get("done_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise self._invalid("Provider done_reason must be a string or null.")
        input_tokens = response.get("prompt_eval_count")
        output_tokens = response.get("eval_count")
        total = (
            input_tokens + output_tokens
            if isinstance(input_tokens, int)
            and not isinstance(input_tokens, bool)
            and isinstance(output_tokens, int)
            and not isinstance(output_tokens, bool)
            else None
        )
        try:
            usage = LLMUsage(input_tokens, output_tokens, total)
        except LLMProviderError as exc:
            raise self._invalid(exc.message) from exc
        return LLMResponse(
            content=content,
            provider=self.provider_id,
            model=self.model,
            usage=usage,
            finish_reason=finish_reason,
            raw_response=response,
        )

    def _invalid(self, message: str) -> LLMProviderError:
        return LLMProviderError(
            code="invalid_response",
            message=message,
            provider=self.provider_id,
            model=self.model,
        )
