from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.llm_provider import (
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    ResolvedLLMProvider,
    create_llm_provider,
    invalid_llm_config_diagnostic,
    invalid_llm_config_identity,
    llm_config_identity,
    resolve_llm_provider_config,
)


_SAFE_ERROR_MESSAGES = {
    "authentication_error": "Provider authentication failed.",
    "rate_limited": "Provider rate limit exceeded.",
    "timeout": "Provider request timed out.",
    "connection_error": "Provider connection failed.",
    "provider_error": "Provider request failed.",
    "invalid_response": "Provider returned an invalid response.",
    "invalid_structured_output": "Provider returned invalid structured output.",
    "context_length_exceeded": "Provider context length was exceeded.",
    "unsupported_capability": "Provider capability is unsupported.",
    "invalid_configuration": "LLM provider configuration is invalid.",
    "process_error": "Provider process failed.",
}
_NETWORK_PROVIDER_IDS = {"openai_compatible", "anthropic", "gemini"}


@dataclass
class LLMPlanAdapter:
    """Artifact-compatible planning facade over the unified provider contract."""

    provider: str = "none"
    display_name: str = "LLM"
    stage: str = "daily_direction_plan"
    prompt_file_pattern: str = ""
    output_file_pattern: str = ""
    executable: str = ""
    model: str = ""
    api_key_env: str = ""
    base_url_env: str = ""
    base_url: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout_seconds: int = 180
    thinking: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    response_format: Any = None
    llm_provider: LLMProvider | None = field(default=None, repr=False)
    resolved: ResolvedLLMProvider | None = field(default=None, repr=False)
    configuration_error: LLMProviderError | None = field(default=None, repr=False)
    raw_config: dict[str, Any] = field(default_factory=dict, repr=False)
    provider_injected: bool = field(default=False, repr=False)
    _credential_value: str | None = field(default=None, repr=False)

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        *,
        workspace_root: Path | None = None,
        llm_provider: LLMProvider | None = None,
    ) -> LLMPlanAdapter:
        try:
            identity_resolved = resolve_llm_provider_config(
                config,
                require_credentials=False,
            )
        except LLMProviderError as exc:
            return cls.from_resolved_config(
                config,
                None,
                llm_provider,
                configuration_error=exc,
                provider_injected=llm_provider is not None,
            )
        effective_provider = llm_provider
        configuration_error: LLMProviderError | None = None
        credential_value: str | None = None
        if effective_provider is None and workspace_root is not None:
            try:
                runtime_resolved = resolve_llm_provider_config(
                    config,
                    require_credentials=True,
                )
                credential_value = runtime_resolved.api_key
                effective_provider = create_llm_provider(
                    runtime_resolved,
                    workspace_root=workspace_root,
                )
            except LLMProviderError as exc:
                configuration_error = exc
        return cls.from_resolved_config(
            config,
            identity_resolved,
            effective_provider,
            configuration_error=configuration_error,
            provider_injected=llm_provider is not None,
            credential_value=credential_value,
        )

    @classmethod
    def from_resolved_config(
        cls,
        config: dict[str, Any],
        resolved: ResolvedLLMProvider | None,
        llm_provider: LLMProvider | None,
        *,
        configuration_error: LLMProviderError | None = None,
        provider_injected: bool = False,
        credential_value: str | None = None,
    ) -> LLMPlanAdapter:
        compatibility = cls._compatibility_settings(config)
        if resolved is not None:
            settings = resolved.config
            configured_provider = str(compatibility.get("provider") or settings.provider)
            artifact_provider = (
                llm_provider.provider_id
                if llm_provider is not None and configured_provider == "disabled"
                else configured_provider
            )
            stage = str(
                compatibility.get("stage")
                or f"{artifact_provider}_daily_direction_plan"
            )
            return cls(
                provider=artifact_provider,
                display_name=str(
                    compatibility.get("display_name")
                    or settings.display_name
                    or artifact_provider
                ),
                stage=stage,
                prompt_file_pattern=str(
                    compatibility.get("prompt_file_pattern") or ""
                ),
                output_file_pattern=str(
                    compatibility.get("output_file_pattern") or ""
                ),
                executable=str(compatibility.get("executable") or ""),
                model=settings.model,
                api_key_env=settings.api_key_env,
                base_url_env=settings.base_url_env,
                base_url=settings.base_url,
                temperature=settings.temperature,
                max_tokens=settings.max_tokens,
                timeout_seconds=settings.timeout_seconds,
                thinking=compatibility.get("thinking"),
                reasoning_effort=compatibility.get("reasoning_effort"),
                response_format=(
                    compatibility.get("response_format")
                    or settings.response_format
                ),
                llm_provider=llm_provider,
                resolved=resolved,
                configuration_error=configuration_error,
                raw_config=dict(config),
                provider_injected=provider_injected,
                _credential_value=None if provider_injected else credential_value,
            )
        return cls(
            provider=str(compatibility.get("provider") or "none"),
            display_name=str(compatibility.get("display_name") or "LLM"),
            stage=str(compatibility.get("stage") or "daily_direction_plan"),
            prompt_file_pattern=str(compatibility.get("prompt_file_pattern") or ""),
            output_file_pattern=str(compatibility.get("output_file_pattern") or ""),
            executable=str(compatibility.get("executable") or ""),
            model=(
                llm_provider.model
                if llm_provider is not None
                else str(compatibility.get("model") or "")
            ),
            api_key_env=str(compatibility.get("api_key_env") or ""),
            base_url_env=str(compatibility.get("base_url_env") or ""),
            base_url=str(compatibility.get("base_url") or ""),
            thinking=compatibility.get("thinking"),
            reasoning_effort=compatibility.get("reasoning_effort"),
            response_format=compatibility.get("response_format"),
            llm_provider=llm_provider,
            configuration_error=configuration_error,
            raw_config=dict(config),
            provider_injected=provider_injected,
            _credential_value=(
                None
                if provider_injected
                else os.getenv(str(compatibility.get("api_key_env") or ""))
            ),
        )

    @staticmethod
    def _compatibility_settings(config: dict[str, Any]) -> dict[str, Any]:
        if config.get("llm_provider") is not None:
            settings = config.get("llm_provider") or {}
            provider = str(settings.get("provider") or "none")
            command = settings.get("command")
            executable = (
                command[0]
                if isinstance(command, list)
                and command
                and isinstance(command[0], str)
                else ""
            )
            return {
                "provider": provider,
                "display_name": str(
                    settings.get("display_name") or provider or "LLM"
                ),
                "stage": str(
                    settings.get("stage")
                    or f"{provider}_daily_direction_plan"
                ),
                "prompt_file_pattern": str(
                    settings.get("prompt_file_pattern") or ""
                ),
                "output_file_pattern": str(
                    settings.get("output_file_pattern") or ""
                ),
                "executable": executable,
                "model": str(settings.get("model") or ""),
                "api_key_env": str(settings.get("api_key_env") or ""),
                "base_url_env": str(settings.get("base_url_env") or ""),
                "base_url": str(settings.get("base_url") or ""),
                "thinking": settings.get("thinking"),
                "reasoning_effort": settings.get("reasoning_effort"),
                "response_format": settings.get("response_format"),
            }
        llm_config = config.get("llm_adapter") or {}
        if llm_config:
            provider = str(llm_config.get("provider") or "none")
            return {
                "provider": provider,
                "display_name": str(
                    llm_config.get("display_name") or provider or "LLM"
                ),
                "stage": str(
                    llm_config.get("stage")
                    or f"{provider}_daily_direction_plan"
                ),
                "prompt_file_pattern": str(
                    llm_config.get("prompt_file_pattern") or ""
                ),
                "output_file_pattern": str(
                    llm_config.get("output_file_pattern") or ""
                ),
                "executable": str(llm_config.get("executable") or ""),
                "model": str(llm_config.get("model") or ""),
                "api_key_env": str(llm_config.get("api_key_env") or ""),
                "base_url_env": str(llm_config.get("base_url_env") or ""),
                "base_url": str(llm_config.get("base_url") or ""),
                "thinking": llm_config.get("thinking"),
                "reasoning_effort": llm_config.get("reasoning_effort"),
                "response_format": llm_config.get("response_format"),
            }
        if config.get("deepseek_v4_pro"):
            settings = config.get("deepseek_v4_pro") or {}
            return {
                "provider": "deepseek",
                "display_name": str(
                    settings.get("display_name") or "DeepSeek v4 Pro"
                ),
                "stage": "deepseek_v4_pro_daily_direction_plan",
                "prompt_file_pattern": str(
                    settings.get("prompt_file_pattern") or ""
                ),
                "output_file_pattern": str(
                    settings.get("output_file_pattern") or ""
                ),
                "model": str(
                    os.getenv("DEEPSEEK_MODEL")
                    or settings.get("model")
                    or "deepseek-v4-pro"
                ),
                "api_key_env": str(
                    settings.get("api_key_env") or "DEEPSEEK_API_KEY"
                ),
                "base_url_env": str(
                    settings.get("base_url_env") or "DEEPSEEK_BASE_URL"
                ),
                "base_url": str(
                    settings.get("base_url") or "https://api.deepseek.com"
                ),
                "thinking": settings.get("thinking") or {"type": "enabled"},
                "reasoning_effort": str(
                    settings.get("reasoning_effort") or "high"
                ),
                "response_format": settings.get("response_format")
                or {"type": "text"},
            }
        if config.get("kimi_cli"):
            settings = config.get("kimi_cli") or {}
            return {
                "provider": "kimi_cli",
                "display_name": str(settings.get("display_name") or "Kimi"),
                "stage": "kimi_daily_direction_plan",
                "prompt_file_pattern": str(
                    settings.get("long_prompt_file_pattern") or ""
                ),
                "output_file_pattern": str(
                    settings.get("output_file_pattern") or ""
                ),
                "executable": str(settings.get("executable") or "kimi-cli"),
                "model": str(settings.get("model") or "kimi-cli"),
            }
        return {}

    def is_configured(self) -> bool:
        return self.llm_provider is not None or self.provider not in {
            "",
            "none",
            "disabled",
        }

    def prompt_path(self, root: Path, run_dir: Path, run_tag: str) -> Path:
        if self.prompt_file_pattern:
            return self._pattern_path(root, self.prompt_file_pattern, run_tag)
        folder = (
            "kimi_prompts"
            if self.provider == "kimi_cli"
            else f"{self.provider}_prompts"
        )
        return run_dir / folder / f"{self.stage}.md"

    def output_path(self, root: Path, run_dir: Path, run_tag: str) -> Path:
        if self.output_file_pattern:
            return self._pattern_path(root, self.output_file_pattern, run_tag)
        folder = (
            "kimi_outputs"
            if self.provider == "kimi_cli"
            else f"{self.provider}_outputs"
        )
        return run_dir / folder / f"{self.stage}.json"

    def _pattern_path(self, root: Path, pattern: str, run_tag: str) -> Path:
        formatted = pattern.format(
            daily_run_tag=run_tag,
            run_tag=run_tag,
            stage=self.stage,
        )
        path = Path(formatted)
        return path if path.is_absolute() else root / path

    def call(self, root: Path, prompt: str) -> dict[str, Any]:
        if not self.is_configured():
            return {"disabled": True, "reason": "No LLM adapter is configured."}
        if os.environ.get("WQB_DISABLE_LLM_TEMPLATE_BACKEND") == "1":
            return {
                "disabled": True,
                "reason": "WQB_DISABLE_LLM_TEMPLATE_BACKEND=1",
                "provider": self.provider,
            }
        self.prepare_for_attempt(root)
        if self.configuration_error is not None:
            return self._error_payload(self.configuration_error, disabled=True)
        if self.llm_provider is None:
            return {
                "disabled": True,
                "reason": "The configured LLM provider is disabled.",
                "provider": self.provider,
            }
        request = LLMRequest(
            system_prompt=(
                "You are a WQB alpha-mining planner. "
                "Output only valid JSON with no markdown."
            ),
            user_prompt=prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_format="json",
            timeout_seconds=self.timeout_seconds,
            metadata={"workflow_stage": self.stage},
        )
        try:
            response = self.llm_provider.complete(request)
            payload = json.loads(response.content)
        except LLMProviderError as exc:
            return self._error_payload(exc)
        except (json.JSONDecodeError, TypeError):
            return self._error_payload(
                LLMProviderError(
                    code="invalid_structured_output",
                    message="Provider returned invalid JSON planning content.",
                    provider=self.llm_provider.provider_id,
                    model=self.llm_provider.model,
                )
            )
        except Exception:
            return self._error_payload(
                LLMProviderError(
                    code="provider_error",
                    message="Provider raised an unexpected exception.",
                    provider=self.llm_provider.provider_id,
                    model=self.llm_provider.model,
                )
            )
        if isinstance(payload, dict):
            payload["provider"] = response.provider
            payload["model"] = response.model
            return payload
        return {
            "provider": response.provider,
            "model": response.model,
            "result": payload,
        }

    def prepare_for_attempt(self, root: Path) -> bool:
        """Refresh a changed network credential and bind deferred providers to root."""
        credential_changed = self._network_credential_changed()
        if credential_changed:
            credential_env = self._credential_env_name()
            self._credential_value = (
                os.getenv(credential_env) if credential_env else None
            )
            self.llm_provider = None
            self.configuration_error = None
            try:
                self.resolved = resolve_llm_provider_config(
                    self.raw_config,
                    require_credentials=False,
                )
                runtime_resolved = resolve_llm_provider_config(
                    self.raw_config,
                    require_credentials=True,
                )
                self._credential_value = runtime_resolved.api_key
                self._apply_resolved_generation_settings()
                self.llm_provider = create_llm_provider(
                    runtime_resolved,
                    workspace_root=root,
                )
            except LLMProviderError as exc:
                self.configuration_error = exc
                return True
        if self.llm_provider is not None:
            return credential_changed
        if self.configuration_error is not None:
            return credential_changed
        if self.resolved is None or self.resolved.config.provider == "disabled":
            return credential_changed
        try:
            runtime_resolved = resolve_llm_provider_config(
                self.raw_config,
                require_credentials=True,
            )
            self.llm_provider = create_llm_provider(
                runtime_resolved,
                workspace_root=root,
            )
            self._credential_value = runtime_resolved.api_key
        except LLMProviderError as exc:
            self.configuration_error = exc
        return credential_changed

    def _network_credential_changed(self) -> bool:
        if self.provider_injected:
            return False
        provider_id = (
            self.resolved.config.provider
            if self.resolved is not None
            else self.runtime_provider_id
        )
        if provider_id == "deepseek":
            provider_id = "openai_compatible"
        if provider_id not in _NETWORK_PROVIDER_IDS:
            return False
        credential_env = self._credential_env_name()
        if not credential_env:
            return False
        return os.getenv(credential_env) != self._credential_value

    def _credential_env_name(self) -> str:
        return (
            self.resolved.config.api_key_env
            if self.resolved is not None
            else self.api_key_env
        )

    def _apply_resolved_generation_settings(self) -> None:
        if self.resolved is None:
            return
        self.model = self.resolved.config.model
        self.api_key_env = self.resolved.config.api_key_env
        self.base_url_env = self.resolved.config.base_url_env
        self.base_url = self.resolved.config.base_url
        self.temperature = self.resolved.config.temperature
        self.max_tokens = self.resolved.config.max_tokens
        self.timeout_seconds = self.resolved.config.timeout_seconds
        self.response_format = self.resolved.config.response_format

    def _error_payload(
        self,
        error: LLMProviderError,
        *,
        disabled: bool = False,
    ) -> dict[str, Any]:
        safe_error: dict[str, Any] = {
            "code": error.code,
            "message": _SAFE_ERROR_MESSAGES[error.code],
            "retryable": error.retryable,
        }
        if error.provider is not None:
            safe_error["provider"] = error.provider
        if error.model is not None:
            safe_error["model"] = error.model
        if error.status_code is not None:
            safe_error["status_code"] = error.status_code
        payload: dict[str, Any] = {
            "provider": error.provider or self.runtime_provider_id,
            "model": error.model or self.runtime_model,
            "error": safe_error,
        }
        if disabled:
            payload["disabled"] = True
            payload["reason"] = safe_error["message"]
        return payload

    @property
    def runtime_provider_id(self) -> str:
        if self.llm_provider is not None:
            return self.llm_provider.provider_id
        if self.resolved is not None:
            return self.resolved.config.provider
        return self.provider

    @property
    def runtime_model(self) -> str:
        if self.llm_provider is not None:
            return self.llm_provider.model
        if self.resolved is not None:
            return self.resolved.config.model
        return self.model

    def metadata(self) -> dict[str, Any]:
        if self.resolved is not None:
            metadata = llm_config_identity(self.resolved)
        else:
            error = self.configuration_error or LLMProviderError(
                code="invalid_configuration",
                message="LLM provider configuration could not be resolved.",
            )
            metadata = invalid_llm_config_identity(self.raw_config, error)
        if self.configuration_error is not None:
            metadata["configuration_error"] = invalid_llm_config_diagnostic(
                self.configuration_error
            )
        return metadata
