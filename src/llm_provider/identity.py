from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import LLMProviderError, sanitize_url
from .models import ResolvedLLMProvider


def llm_config_identity(resolved: ResolvedLLMProvider) -> dict[str, Any]:
    """Return the credential-independent identity of an effective LLM config."""
    provider = resolved.config.provider
    model = resolved.config.model
    digest_input = {
        "effective_config": {
            "config": resolved.config.to_redacted_dict(),
            "effective_base_url": sanitize_url(resolved.base_url),
            "warnings": list(resolved.warnings),
        },
        "provider": provider,
        "model": model,
    }
    digest = hashlib.sha256(
        json.dumps(
            digest_input,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "provider": provider,
        "model": model,
        "config_digest": digest,
        "migration_warnings": list(resolved.warnings),
    }


_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)
_LLM_CONFIG_KEYS = ("llm_provider", "llm_adapter", "deepseek_v4_pro", "kimi_cli")
_KNOWN_PROVIDERS = {
    "anthropic",
    "cli",
    "disabled",
    "gemini",
    "ollama",
    "openai_compatible",
}


def _redact_sensitive_config(value: Any, *, parent_key: str = "") -> Any:
    normalized_key = parent_key.lower().replace("-", "_")
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return {"kind": "redacted"}
    if isinstance(value, Mapping):
        return {
            str(key): _redact_sensitive_config(item, parent_key=str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_sensitive_config(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, str):
            return {
                "kind": "string_fingerprint",
                "length": len(value),
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            }
        return value
    return f"<{type(value).__name__}>"


def invalid_llm_config_identity(
    workflow_config: Mapping[str, Any],
    error: LLMProviderError,
) -> dict[str, Any]:
    relevant = {
        key: workflow_config[key]
        for key in _LLM_CONFIG_KEYS
        if key in workflow_config
    }
    redacted = _redact_sensitive_config(relevant)
    settings = next(
        (
            value
            for value in relevant.values()
            if isinstance(value, Mapping)
        ),
        {},
    )
    provider_value = settings.get("provider")
    provider = (
        provider_value
        if isinstance(provider_value, str) and provider_value in _KNOWN_PROVIDERS
        else "invalid"
    )
    model = ""
    path_value = error.details.get("path")
    error_path = (
        path_value
        if isinstance(path_value, str)
        and re.fullmatch(r"\$[A-Za-z0-9_.\[\]-]*", path_value)
        else "$.llm_provider"
    )
    digest_input = {
        "redacted_config": redacted,
        "error_code": error.code,
        "error_path": error_path,
    }
    digest = hashlib.sha256(
        json.dumps(
            digest_input,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "provider": provider,
        "model": model,
        "config_digest": digest,
        "migration_warnings": [],
        "error_code": error.code,
        "error_path": error_path,
    }


def invalid_llm_config_diagnostic(error: LLMProviderError) -> dict[str, Any]:
    path_value = error.details.get("path")
    error_path = (
        path_value
        if isinstance(path_value, str)
        and re.fullmatch(r"\$[A-Za-z0-9_.\[\]-]*", path_value)
        else "$.llm_provider"
    )
    return {
        "code": error.code,
        "message": "LLM provider configuration is invalid.",
        "retryable": False,
        "path": error_path,
    }
