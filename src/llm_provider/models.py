from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType
from typing import Any, Literal

from .errors import LLMProviderError, redact_secrets, sanitize_url


def _readonly_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise LLMProviderError(
                    code="invalid_response",
                    message="Raw response mapping keys must be strings.",
                )
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise LLMProviderError(
        code="invalid_response",
        message="Raw response contains a non-JSON value.",
        details={"value_type": type(value).__name__},
    )


@dataclass(frozen=True)
class LLMRequest:
    system_prompt: str
    user_prompt: str
    temperature: float = 0.2
    max_tokens: int = 4096
    response_format: Literal["text", "json"] = "text"
    timeout_seconds: int | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, prompt in (
            ("system_prompt", self.system_prompt),
            ("user_prompt", self.user_prompt),
        ):
            if not isinstance(prompt, str) or not prompt.strip():
                raise LLMProviderError(
                    code="invalid_configuration",
                    message=f"{name} must be a non-empty string.",
                )
        if (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not isfinite(self.temperature)
            or self.temperature < 0
            or self.temperature > 2
        ):
            raise LLMProviderError(
                code="invalid_configuration",
                message="temperature must be finite and between 0 and 2.",
            )
        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens < 1
            or self.max_tokens > 131072
        ):
            raise LLMProviderError(
                code="invalid_configuration",
                message="max_tokens must be an integer between 1 and 131072.",
            )
        if self.timeout_seconds is not None and (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds < 1
            or self.timeout_seconds > 600
        ):
            raise LLMProviderError(
                code="invalid_configuration",
                message="timeout_seconds must be None or an integer between 1 and 600.",
            )
        if not isinstance(self.response_format, str) or self.response_format not in {
            "text",
            "json",
        }:
            raise LLMProviderError(
                code="invalid_configuration",
                message="response_format must be 'text' or 'json'.",
            )
        if not isinstance(self.metadata, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.metadata.items()
        ):
            raise LLMProviderError(
                code="invalid_configuration",
                message="metadata keys and values must be strings.",
            )
        object.__setattr__(self, "temperature", float(self.temperature))
        object.__setattr__(self, "metadata", _readonly_mapping(self.metadata))


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("total_tokens", self.total_tokens),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise LLMProviderError(
                    code="invalid_response",
                    message=f"{name} must be None or a nonnegative integer.",
                )


@dataclass(frozen=True)
class LLMResponse:
    content: str
    provider: str
    model: str
    usage: LLMUsage
    finish_reason: str | None = None
    raw_response: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise LLMProviderError(
                code="invalid_response",
                message="Response provider must be a non-empty string.",
            )
        if not isinstance(self.model, str) or not self.model.strip():
            raise LLMProviderError(
                code="invalid_response",
                message="Response model must be a non-empty string.",
                provider=self.provider,
            )
        if not isinstance(self.content, str) or not self.content.strip():
            raise LLMProviderError(
                code="invalid_response",
                message="Provider returned empty response content.",
                provider=self.provider,
                model=self.model,
            )
        if not isinstance(self.usage, LLMUsage):
            raise LLMProviderError(
                code="invalid_response",
                message="Response usage must be an LLMUsage value.",
                provider=self.provider,
                model=self.model,
            )
        if self.finish_reason is not None and not isinstance(self.finish_reason, str):
            raise LLMProviderError(
                code="invalid_response",
                message="Response finish_reason must be a string or None.",
                provider=self.provider,
                model=self.model,
            )
        if self.raw_response is not None:
            if not isinstance(self.raw_response, Mapping):
                raise LLMProviderError(
                    code="invalid_response",
                    message="Raw response must be a mapping.",
                    provider=self.provider,
                    model=self.model,
                )
            object.__setattr__(self, "raw_response", _freeze_json(self.raw_response))


@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    display_name: str
    model: str
    api_key_env: str
    base_url: str
    base_url_env: str
    timeout_seconds: int
    temperature: float
    max_tokens: int
    response_format: Literal["text", "json"]
    command: tuple[str, ...] = ()
    prompt_transport: Literal["argument", "stdin"] = "argument"
    working_directory: str = "."

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "base_url": sanitize_url(self.base_url),
            "base_url_env": self.base_url_env,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": self.response_format,
            "command": list(self.command),
            "prompt_transport": self.prompt_transport,
            "working_directory": self.working_directory,
        }


@dataclass(frozen=True)
class ResolvedLLMProvider:
    config: LLMProviderConfig
    api_key: str | None = field(repr=False)
    base_url: str
    warnings: tuple[str, ...] = ()

    def to_redacted_dict(self) -> dict[str, Any]:
        payload = {
            "config": self.config.to_redacted_dict(),
            "effective_base_url": sanitize_url(self.base_url),
            "credential_configured": self.api_key is not None,
            "warnings": list(self.warnings),
        }
        redacted = redact_secrets(payload, (self.api_key or "",))
        return redacted if isinstance(redacted, dict) else {}
