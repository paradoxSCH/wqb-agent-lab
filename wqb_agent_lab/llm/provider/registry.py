from __future__ import annotations

from pathlib import Path

from .client import LLMProvider
from .errors import LLMProviderError
from .models import ResolvedLLMProvider
from .providers.anthropic import AnthropicProvider
from .providers.cli import CLIProvider
from .providers.gemini import GeminiProvider
from .providers.ollama import OllamaProvider
from .providers.openai_compatible import OpenAICompatibleProvider


def create_llm_provider(
    resolved: ResolvedLLMProvider,
    workspace_root: str | Path | None = None,
) -> LLMProvider | None:
    if not isinstance(resolved, ResolvedLLMProvider):
        raise LLMProviderError(
            code="invalid_configuration",
            message="resolved must be a ResolvedLLMProvider value.",
        )
    provider_id = resolved.config.provider
    if provider_id == "disabled":
        return None
    if provider_id == "openai_compatible":
        if resolved.api_key is None:
            raise LLMProviderError(
                code="invalid_configuration",
                message="OpenAI-compatible provider credential is missing.",
                provider=provider_id,
                model=resolved.config.model,
            )
        return OpenAICompatibleProvider(
            model=resolved.config.model,
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            timeout_seconds=resolved.config.timeout_seconds,
        )
    if provider_id == "anthropic":
        if resolved.api_key is None:
            raise _missing_credential(resolved)
        return AnthropicProvider(
            model=resolved.config.model,
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            timeout_seconds=resolved.config.timeout_seconds,
        )
    if provider_id == "gemini":
        if resolved.api_key is None:
            raise _missing_credential(resolved)
        return GeminiProvider(
            model=resolved.config.model,
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            timeout_seconds=resolved.config.timeout_seconds,
        )
    if provider_id == "ollama":
        return OllamaProvider(
            model=resolved.config.model,
            base_url=resolved.base_url,
            timeout_seconds=resolved.config.timeout_seconds,
        )
    if provider_id == "cli":
        return CLIProvider(
            model=resolved.config.model,
            command=resolved.config.command,
            prompt_transport=resolved.config.prompt_transport,
            workspace_root=Path.cwd() if workspace_root is None else workspace_root,
            working_directory=resolved.config.working_directory,
            timeout_seconds=resolved.config.timeout_seconds,
            secrets=(resolved.api_key or "",),
            credential_env_name=resolved.config.api_key_env,
            credential_value=resolved.api_key,
        )
    raise LLMProviderError(
        code="invalid_configuration",
        message=f"Provider '{provider_id}' is not registered.",
        provider=provider_id,
        model=resolved.config.model or None,
    )


def _missing_credential(resolved: ResolvedLLMProvider) -> LLMProviderError:
    return LLMProviderError(
        code="invalid_configuration",
        message=f"{resolved.config.provider} provider credential is missing.",
        provider=resolved.config.provider,
        model=resolved.config.model,
    )
