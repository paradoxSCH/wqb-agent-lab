from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import requests

from ..client import validate_structured_content
from ..errors import LLMProviderError, redact_secrets
from ..models import LLMRequest, LLMResponse, LLMUsage
from .http import post_json_once


class OpenAICompatibleProvider:
    provider_id = "openai_compatible"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 180,
        session: requests.Session | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise LLMProviderError(
                code="invalid_configuration", message="model must be non-empty."
            )
        if not isinstance(api_key, str) or not api_key.strip():
            raise LLMProviderError(
                code="invalid_configuration", message="api_key must be configured."
            )
        if not isinstance(base_url, str) or not base_url.strip():
            raise LLMProviderError(
                code="invalid_configuration", message="base_url must be non-empty."
            )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise LLMProviderError(
                code="invalid_configuration",
                message="timeout_seconds must be positive.",
            )
        self._model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = float(timeout_seconds)
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
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        if request.response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        response_payload = post_json_once(
            url=f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=request.timeout_seconds or self._timeout_seconds,
            provider=self.provider_id,
            model=self.model,
            secrets=(self._api_key,),
            session=self._session,
        )
        redacted_payload = redact_secrets(response_payload, (self._api_key,))
        if not isinstance(redacted_payload, dict):
            raise self._invalid("Provider response must be a JSON object.")
        response_payload = redacted_payload
        if request.response_format == "json":
            self._redact_all_structured_choice_content(response_payload)
        content, finish_reason = self._parse_choice(response_payload)
        usage = self._parse_usage(response_payload.get("usage"))
        return LLMResponse(
            content=content,
            provider=self.provider_id,
            model=self.model,
            usage=usage,
            finish_reason=finish_reason,
            raw_response=response_payload,
        )

    def _redact_structured_content(self, content: str) -> str:
        try:
            parsed = validate_structured_content(content)
            redacted = redact_secrets(parsed, (self._api_key,))
            serialized = json.dumps(
                redacted,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            validate_structured_content(serialized)
            return serialized
        except LLMProviderError as exc:
            raise LLMProviderError(
                code=exc.code,
                message=exc.message,
                provider=self.provider_id,
                model=self.model,
                retryable=exc.retryable,
                details=exc.details,
                secrets=(self._api_key,),
            ) from exc

    def _redact_all_structured_choice_content(
        self, payload: dict[str, Any]
    ) -> None:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise self._invalid("Provider response has no choices.")
        for choice in choices:
            if not isinstance(choice, dict):
                raise self._invalid("Provider choice must be a JSON object.")
            message = choice.get("message")
            if not isinstance(message, dict):
                raise self._invalid("Provider choice has no message object.")
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise self._invalid("Provider returned empty response content.")
            message["content"] = self._redact_structured_content(content)

    def _parse_choice(self, payload: Mapping[str, Any]) -> tuple[str, str | None]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise self._invalid("Provider response has no choices.")
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise self._invalid("Provider choice must be a JSON object.")
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise self._invalid("Provider choice has no message object.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise self._invalid("Provider returned empty response content.")
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise self._invalid("Provider finish_reason must be a string or null.")
        return content, finish_reason

    def _parse_usage(self, value: Any) -> LLMUsage:
        if value is None:
            value = {}
        if not isinstance(value, Mapping):
            raise self._invalid("Provider usage must be a JSON object.")
        try:
            return LLMUsage(
                input_tokens=value.get("prompt_tokens"),
                output_tokens=value.get("completion_tokens"),
                total_tokens=value.get("total_tokens"),
            )
        except LLMProviderError as exc:
            raise self._invalid(exc.message) from exc

    def _invalid(self, message: str) -> LLMProviderError:
        return LLMProviderError(
            code="invalid_response",
            message=message,
            provider=self.provider_id,
            model=self.model,
            secrets=(self._api_key,),
        )
