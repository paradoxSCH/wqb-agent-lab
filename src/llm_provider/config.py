from __future__ import annotations

import os
from collections.abc import Mapping
from math import isfinite
from typing import Any

from .cli_placeholders import validate_cli_command_placeholders
from .errors import LLMProviderError
from .models import LLMProviderConfig, ResolvedLLMProvider


PROVIDER_IDS = frozenset(
    {"openai_compatible", "anthropic", "gemini", "ollama", "cli", "disabled"}
)
NETWORK_PROVIDERS = frozenset({"openai_compatible", "anthropic", "gemini"})
MODEL_REQUIRED_PROVIDERS = NETWORK_PROVIDERS | {"ollama"}
DEFAULT_API_KEY_ENV = {
    "openai_compatible": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}
DEFAULT_BASE_URL = {
    "openai_compatible": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "ollama": "http://127.0.0.1:11434",
}


def _invalid(message: str, *, details: Mapping[str, Any] | None = None) -> LLMProviderError:
    return LLMProviderError(
        code="invalid_configuration",
        message=message,
        details=details,
    )


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(f"{name} must be a JSON object.")
    return dict(value)


def _string(
    settings: Mapping[str, Any], name: str, default: str = ""
) -> str:
    if name not in settings:
        return default
    value = settings[name]
    if not isinstance(value, str):
        raise _invalid(f"{name} must be a string.")
    if value and not value.strip():
        raise _invalid(f"{name} must not contain only whitespace.")
    return value or default


def _environment_string(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not isinstance(value, str):
        raise _invalid(f"Environment variable {name} must be a string.")
    return value


def _number(
    settings: Mapping[str, Any],
    name: str,
    default: int | float,
    minimum: int | float,
    maximum: int | float,
) -> int | float:
    value = settings.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"{name} must be numeric.")
    if not isfinite(value):
        raise _invalid(f"{name} must be finite.")
    if value < minimum or value > maximum:
        raise _invalid(f"{name} must be between {minimum} and {maximum}.")
    return value


def _integer(
    settings: Mapping[str, Any],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = settings.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(f"{name} must be an integer.")
    if value < minimum or value > maximum:
        raise _invalid(f"{name} must be between {minimum} and {maximum}.")
    return value


def _canonical_response_format(value: Any) -> str:
    if value is None or value == "text":
        return "text"
    if value == "json":
        return "json"
    raise _invalid("response_format must be 'text' or 'json'.")


def _legacy_response_format(value: Any) -> str:
    if isinstance(value, Mapping):
        legacy_type = value.get("type")
        if legacy_type == "json_object":
            return "json"
        if legacy_type == "text":
            return "text"
    return _canonical_response_format(value)


def _normalize_legacy_adapter(
    settings: Mapping[str, Any], env: Mapping[str, str]
) -> dict[str, Any]:
    normalized = dict(settings)
    provider = _string(normalized, "provider", "disabled")
    if provider in {"deepseek", "openai_compatible"}:
        return _legacy_openai_adapter_config(settings, env)
    if provider == "kimi_cli":
        return _kimi_cli_config(settings)
    provider_aliases = {
        "none": "disabled",
        "": "disabled",
    }
    normalized["provider"] = provider_aliases.get(provider, provider)
    normalized["response_format"] = _legacy_response_format(
        settings.get("response_format")
    )
    return normalized


def _legacy_openai_adapter_config(
    settings: Mapping[str, Any], env: Mapping[str, str]
) -> dict[str, Any]:
    normalized = dict(settings)
    normalized["provider"] = "openai_compatible"
    normalized["model"] = (
        _string(settings, "model")
        or _environment_string(env, "DEEPSEEK_MODEL")
        or "deepseek-v4-pro"
    )
    normalized["api_key_env"] = _string(
        settings, "api_key_env", "DEEPSEEK_API_KEY"
    )
    normalized["base_url_env"] = _string(
        settings, "base_url_env"
    )
    normalized["base_url"] = _string(
        settings, "base_url", "https://api.deepseek.com"
    )
    normalized["response_format"] = _legacy_response_format(
        settings.get("response_format")
    )
    return normalized


def _deepseek_config(settings: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    return {
        "provider": "openai_compatible",
        "display_name": _string(settings, "display_name", "DeepSeek v4 Pro"),
        "model": _string(settings, "model")
        or _environment_string(env, "DEEPSEEK_MODEL")
        or "deepseek-v4-pro",
        "api_key_env": _string(settings, "api_key_env", "DEEPSEEK_API_KEY"),
        "base_url_env": _string(settings, "base_url_env", "DEEPSEEK_BASE_URL"),
        "base_url": _string(settings, "base_url", "https://api.deepseek.com"),
        "temperature": settings.get("temperature", 1.0),
        "max_tokens": settings.get("max_tokens", 4096),
        "timeout_seconds": settings.get("timeout_seconds", 180),
        "response_format": _legacy_response_format(settings.get("response_format")),
    }


def _kimi_cli_config(settings: Mapping[str, Any]) -> dict[str, Any]:
    executable = _string(settings, "executable", "kimi-cli")
    return {
        "provider": "cli",
        "display_name": _string(settings, "display_name", "Kimi"),
        "model": _string(settings, "model", "kimi-cli"),
        "command": [
            executable,
            "--work-dir",
            "{workspace_root}",
            "--print",
            "--final-message-only",
            "--prompt",
            "{prompt}",
        ],
        "prompt_transport": "argument",
        "working_directory": _string(settings, "working_directory", "."),
        "timeout_seconds": settings.get("timeout_seconds", 180),
        "temperature": settings.get("temperature", 0.2),
        "max_tokens": settings.get("max_tokens", 4096),
        "response_format": _legacy_response_format(
            settings.get("response_format", "json")
        ),
    }


def _kimi_environment_config(env: Mapping[str, str]) -> tuple[dict[str, Any], str] | None:
    if _environment_string(env, "KIMI_API_KEY"):
        api_key_env = "KIMI_API_KEY"
    elif _environment_string(env, "MOONSHOT_API_KEY"):
        api_key_env = "MOONSHOT_API_KEY"
    else:
        return None
    return (
        {
            "provider": "openai_compatible",
            "display_name": "Kimi",
            "model": _environment_string(env, "KIMI_MODEL") or "kimi-k2-6",
            "api_key_env": api_key_env,
            "base_url": _environment_string(env, "KIMI_BASE_URL")
            or "https://api.moonshot.cn/v1",
            "temperature": 0.75,
            "max_tokens": 4000,
            "response_format": "json",
        },
        f"{api_key_env} and legacy KIMI_* settings are deprecated; use llm_provider.",
    )


def _select_settings(
    workflow_config: Mapping[str, Any], env: Mapping[str, str]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    if "llm_provider" in workflow_config:
        settings = _mapping(workflow_config["llm_provider"], "llm_provider")
        if "provider" not in settings:
            raise _invalid("llm_provider.provider is required.")
        if settings.get("provider") == "":
            raise _invalid("provider must not be empty.")
        return settings, ()
    if workflow_config.get("llm_adapter") is not None:
        settings = _mapping(workflow_config["llm_adapter"], "llm_adapter")
        return _normalize_legacy_adapter(settings, env), (
            "llm_adapter is deprecated; use llm_provider.",
        )
    if workflow_config.get("deepseek_v4_pro") is not None:
        settings = _mapping(workflow_config["deepseek_v4_pro"], "deepseek_v4_pro")
        return _deepseek_config(settings, env), (
            "deepseek_v4_pro is deprecated; use llm_provider.",
        )
    if workflow_config.get("kimi_cli") is not None:
        settings = _mapping(workflow_config["kimi_cli"], "kimi_cli")
        return _kimi_cli_config(settings), (
            "kimi_cli is deprecated; use llm_provider with provider 'cli'.",
        )
    environment_config = _kimi_environment_config(env)
    if environment_config is not None:
        settings, warning = environment_config
        return settings, (warning,)
    return {"provider": "disabled"}, ()


def _validate_cli(settings: Mapping[str, Any]) -> tuple[tuple[str, ...], str, str]:
    command_value = settings.get("command")
    if not isinstance(command_value, list) or not command_value:
        raise _invalid("CLI command must be a non-empty JSON string array.")
    if any(not isinstance(part, str) or not part for part in command_value):
        raise _invalid("Every CLI command item must be a non-empty string.")
    if any(not part.strip() for part in command_value):
        raise _invalid("CLI command items must not contain only whitespace.")
    command = tuple(command_value)
    placeholders = validate_cli_command_placeholders(command)
    prompt_transport = _string(settings, "prompt_transport", "argument")
    if prompt_transport not in {"argument", "stdin"}:
        raise _invalid("prompt_transport must be 'argument' or 'stdin'.")
    if prompt_transport == "argument" and not placeholders.intersection(
        {"prompt", "system_prompt"}
    ):
        raise _invalid("Argument prompt transport requires a prompt placeholder.")
    working_directory = _string(settings, "working_directory", ".")
    return command, prompt_transport, working_directory


def resolve_llm_provider_config(
    workflow_config: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
    *,
    require_credentials: bool = True,
) -> ResolvedLLMProvider:
    if not isinstance(workflow_config, Mapping):
        raise _invalid("Workflow configuration must be a JSON object.")
    effective_env = os.environ if env is None else env
    settings, warnings = _select_settings(workflow_config, effective_env)
    if "api_key" in settings:
        raise _invalid("Literal api_key values are forbidden; use api_key_env.")

    provider = _string(settings, "provider", "disabled")
    if provider not in PROVIDER_IDS:
        raise _invalid(f"Unsupported LLM provider: {provider}")

    model = _string(settings, "model")
    if provider in MODEL_REQUIRED_PROVIDERS and not model:
        raise _invalid(f"model is required for provider '{provider}'.")
    if provider in MODEL_REQUIRED_PROVIDERS and not model.strip():
        raise _invalid(f"model is required for provider '{provider}'.")

    command: tuple[str, ...] = ()
    prompt_transport = "argument"
    working_directory = "."
    if provider == "cli":
        command, prompt_transport, working_directory = _validate_cli(settings)
        model = model or "cli"

    api_key_env = _string(
        settings, "api_key_env", DEFAULT_API_KEY_ENV.get(provider, "")
    )
    if not isinstance(require_credentials, bool):
        raise _invalid("require_credentials must be a boolean.")
    api_key = effective_env.get(api_key_env) if api_key_env else None
    if api_key is not None and not isinstance(api_key, str):
        raise _invalid(f"Environment variable {api_key_env} must be a string.")
    if (
        require_credentials
        and provider in NETWORK_PROVIDERS
        and (not api_key or not api_key.strip())
    ):
        raise _invalid(
            f"Credential environment variable '{api_key_env}' is not configured.",
            details={"api_key_env": api_key_env},
        )

    base_url = _string(settings, "base_url", DEFAULT_BASE_URL.get(provider, ""))
    base_url_env = _string(settings, "base_url_env")
    environment_base_url = (
        _environment_string(effective_env, base_url_env) if base_url_env else ""
    )
    resolved_base_url = environment_base_url or base_url
    if provider in MODEL_REQUIRED_PROVIDERS and not resolved_base_url.strip():
        raise _invalid(f"base_url is required for provider '{provider}'.")
    timeout_seconds = _integer(settings, "timeout_seconds", 180, 1, 600)
    temperature = float(_number(settings, "temperature", 0.2, 0, 2))
    max_tokens = _integer(settings, "max_tokens", 4096, 1, 131072)
    response_format = _canonical_response_format(settings.get("response_format"))

    config = LLMProviderConfig(
        provider=provider,
        display_name=_string(settings, "display_name", provider),
        model=model,
        api_key_env=api_key_env,
        base_url=base_url,
        base_url_env=base_url_env,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,  # type: ignore[arg-type]
        command=command,
        prompt_transport=prompt_transport,  # type: ignore[arg-type]
        working_directory=working_directory,
    )
    return ResolvedLLMProvider(
        config=config,
        api_key=api_key,
        base_url=resolved_base_url,
        warnings=warnings,
    )
