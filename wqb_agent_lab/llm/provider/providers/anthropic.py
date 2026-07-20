from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import requests

from ..client import validate_structured_content
from ..errors import LLMProviderError, redact_secrets
from ..models import LLMRequest, LLMResponse, LLMUsage
from .common import require_nonempty_string, require_positive_timeout
from .http import post_json_once


class AnthropicProvider:
    provider_id = "anthropic"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 180,
        session: requests.Session | None = None,
    ) -> None:
        self._model = require_nonempty_string(model, "model")
        self._api_key = require_nonempty_string(api_key, "api_key")
        self._base_url = require_nonempty_string(base_url, "base_url").rstrip("/")
        self._timeout_seconds = require_positive_timeout(timeout_seconds)
        self._session = session

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: LLMRequest) -> LLMResponse:
        system_prompt = request.system_prompt
        if request.response_format == "json":
            system_prompt = (
                f"{system_prompt}\n\n"
                "Return only a valid JSON object or array. "
                "Use no Markdown fences or explanatory text."
            )
        response = post_json_once(
            url=f"{self._base_url}/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload={
                "model": self.model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": request.user_prompt}],
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
            },
            timeout_seconds=request.timeout_seconds or self._timeout_seconds,
            provider=self.provider_id,
            model=self.model,
            secrets=(self._api_key,),
            session=self._session,
        )
        blocks = response.get("content")
        if not isinstance(blocks, list):
            raise self._invalid("Provider content must be a list.")
        text = "".join(
            block["text"]
            for block in blocks
            if isinstance(block, Mapping)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        if not text.strip():
            raise self._invalid("Provider response has no text content.")
        content = self._sanitize_content(text, request.response_format)
        redacted = redact_secrets(response, (self._api_key,))
        if not isinstance(redacted, dict):
            raise self._invalid("Provider response must be a JSON object.")
        if request.response_format == "json":
            self._replace_text_blocks(redacted, content)
        usage = self._usage(response.get("usage"))
        stop_reason = response.get("stop_reason")
        if stop_reason is not None and not isinstance(stop_reason, str):
            raise self._invalid("Provider stop_reason must be a string or null.")
        return LLMResponse(
            content=content,
            provider=self.provider_id,
            model=self.model,
            usage=usage,
            finish_reason=stop_reason,
            raw_response=redacted,
        )

    def _sanitize_content(self, content: str, response_format: str) -> str:
        if response_format != "json":
            return str(redact_secrets(content, (self._api_key,)))
        try:
            parsed = validate_structured_content(content)
        except LLMProviderError as exc:
            raise self._structured_error(exc) from exc
        return json.dumps(
            redact_secrets(parsed, (self._api_key,)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _replace_text_blocks(self, response: dict[str, Any], content: str) -> None:
        blocks = response.get("content")
        if not isinstance(blocks, list):
            return
        replaced = False
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                block["text"] = content if not replaced else ""
                replaced = True

    def _usage(self, value: Any) -> LLMUsage:
        if value is None:
            value = {}
        if not isinstance(value, Mapping):
            raise self._invalid("Provider usage must be a JSON object.")
        input_tokens = value.get("input_tokens")
        output_tokens = value.get("output_tokens")
        total = (
            input_tokens + output_tokens
            if isinstance(input_tokens, int)
            and not isinstance(input_tokens, bool)
            and isinstance(output_tokens, int)
            and not isinstance(output_tokens, bool)
            else None
        )
        try:
            return LLMUsage(input_tokens, output_tokens, total)
        except LLMProviderError as exc:
            raise self._invalid(exc.message) from exc

    def _structured_error(self, error: LLMProviderError) -> LLMProviderError:
        return LLMProviderError(
            code=error.code,
            message=error.message,
            provider=self.provider_id,
            model=self.model,
            details=error.details,
            secrets=(self._api_key,),
        )

    def _invalid(self, message: str) -> LLMProviderError:
        return LLMProviderError(
            code="invalid_response",
            message=message,
            provider=self.provider_id,
            model=self.model,
            secrets=(self._api_key,),
        )
