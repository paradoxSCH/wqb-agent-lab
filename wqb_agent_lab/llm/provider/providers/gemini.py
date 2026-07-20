from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import requests

from ..client import validate_structured_content
from ..errors import LLMProviderError, redact_secrets
from ..models import LLMRequest, LLMResponse, LLMUsage
from .common import require_nonempty_string, require_positive_timeout
from .http import post_json_once


class GeminiProvider:
    provider_id = "gemini"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 180,
        session: requests.Session | None = None,
    ) -> None:
        self._api_key = require_nonempty_string(api_key, "api_key")
        self._model = require_nonempty_string(model, "model")
        self._resource_model = self._normalize_model_identifier(self._model)
        self._base_url = require_nonempty_string(base_url, "base_url").rstrip("/")
        self._timeout_seconds = require_positive_timeout(timeout_seconds)
        self._session = session

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: LLMRequest) -> LLMResponse:
        generation_config: dict[str, Any] = {
            "temperature": request.temperature,
            "maxOutputTokens": request.max_tokens,
        }
        if request.response_format == "json":
            generation_config["responseMimeType"] = "application/json"
        response = post_json_once(
            url=(
                f"{self._base_url}/v1beta/models/"
                f"{quote(self._resource_model, safe='')}:generateContent"
            ),
            headers={
                "x-goog-api-key": self._api_key,
                "Content-Type": "application/json",
            },
            payload={
                "system_instruction": {"parts": [{"text": request.system_prompt}]},
                "contents": [
                    {"role": "user", "parts": [{"text": request.user_prompt}]}
                ],
                "generationConfig": generation_config,
            },
            timeout_seconds=request.timeout_seconds or self._timeout_seconds,
            provider=self.provider_id,
            model=self.model,
            secrets=(self._api_key,),
            session=self._session,
        )
        self._raise_if_safety_blocked(response)
        candidate, parts = self._first_parts(response)
        text = "".join(
            part["text"]
            for part in parts
            if isinstance(part, Mapping) and isinstance(part.get("text"), str)
        )
        if not text.strip():
            raise self._invalid("Provider response has no text content.")
        content = self._sanitize_content(text, request.response_format)
        redacted = redact_secrets(response, (self._api_key,))
        if not isinstance(redacted, dict):
            raise self._invalid("Provider response must be a JSON object.")
        finish_reason = candidate.get("finishReason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise self._invalid("Provider finishReason must be a string or null.")
        return LLMResponse(
            content=content,
            provider=self.provider_id,
            model=self.model,
            usage=self._usage(response.get("usageMetadata")),
            finish_reason=finish_reason,
            raw_response=redacted,
        )

    def _normalize_model_identifier(self, model: str) -> str:
        normalized = model.strip()
        if normalized.startswith("models/"):
            normalized = normalized[len("models/") :].strip()
        if not normalized or "/" in normalized:
            raise LLMProviderError(
                code="invalid_configuration",
                message=(
                    "Gemini model must be a non-empty model name or "
                    "'models/<model-name>' resource."
                ),
                provider=self.provider_id,
                model=model,
                secrets=(self._api_key,),
            )
        return normalized

    def _raise_if_safety_blocked(self, response: Mapping[str, Any]) -> None:
        evidence: dict[str, Any] = {}
        prompt_feedback = response.get("promptFeedback")
        if isinstance(prompt_feedback, Mapping):
            block_reason = prompt_feedback.get("blockReason")
            if isinstance(block_reason, str) and block_reason not in {
                "",
                "BLOCK_REASON_UNSPECIFIED",
            }:
                evidence["promptFeedback"] = dict(prompt_feedback)
            elif self._has_blocked_rating(prompt_feedback.get("safetyRatings")):
                evidence["promptFeedback"] = dict(prompt_feedback)

        candidates = response.get("candidates")
        if isinstance(candidates, list):
            blocked_candidates = []
            for index, candidate in enumerate(candidates):
                if not isinstance(candidate, Mapping):
                    continue
                finish_reason = candidate.get("finishReason")
                ratings = candidate.get("safetyRatings")
                if finish_reason == "SAFETY" or self._has_blocked_rating(ratings):
                    blocked_candidates.append(
                        {
                            "index": index,
                            "finishReason": finish_reason,
                            "safetyRatings": ratings,
                        }
                    )
            if blocked_candidates:
                evidence["candidates"] = blocked_candidates

        if evidence:
            raise LLMProviderError(
                code="provider_error",
                message="Gemini blocked the request or response for safety reasons.",
                provider=self.provider_id,
                model=self.model,
                retryable=False,
                details={"safety": evidence},
                secrets=(self._api_key,),
            )

    @staticmethod
    def _has_blocked_rating(value: Any) -> bool:
        return isinstance(value, list) and any(
            isinstance(rating, Mapping) and rating.get("blocked") is True
            for rating in value
        )

    def _first_parts(
        self, response: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], list[Any]]:
        candidates = response.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise self._invalid("Provider response has no candidates.")
        candidate = candidates[0]
        if not isinstance(candidate, Mapping):
            raise self._invalid("Provider candidate must be a JSON object.")
        candidate_content = candidate.get("content")
        if not isinstance(candidate_content, Mapping):
            raise self._invalid("Provider candidate has no content object.")
        parts = candidate_content.get("parts")
        if not isinstance(parts, list) or not parts:
            raise self._invalid("Provider candidate has no content parts.")
        return candidate, parts

    def _sanitize_content(self, content: str, response_format: str) -> str:
        if response_format != "json":
            return str(redact_secrets(content, (self._api_key,)))
        try:
            parsed = validate_structured_content(content)
        except LLMProviderError as exc:
            raise LLMProviderError(
                code=exc.code,
                message=exc.message,
                provider=self.provider_id,
                model=self.model,
                details=exc.details,
                secrets=(self._api_key,),
            ) from exc
        return json.dumps(
            redact_secrets(parsed, (self._api_key,)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _usage(self, value: Any) -> LLMUsage:
        if value is None:
            value = {}
        if not isinstance(value, Mapping):
            raise self._invalid("Provider usageMetadata must be a JSON object.")
        try:
            return LLMUsage(
                value.get("promptTokenCount"),
                value.get("candidatesTokenCount"),
                value.get("totalTokenCount"),
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
