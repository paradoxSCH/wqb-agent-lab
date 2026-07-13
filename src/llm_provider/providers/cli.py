from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from ..cli_placeholders import validate_cli_command_placeholders
from ..cli_process import (
    DEFAULT_STDERR_LIMIT_BYTES,
    DEFAULT_STDOUT_LIMIT_BYTES,
    ProcessOutputLimitExceeded,
    ProcessTimedOut,
    build_cli_environment,
    execute_bounded_process,
    resolve_cli_executable,
)
from ..client import validate_structured_content
from ..errors import LLMProviderError, normalize_secrets, redact_secrets
from ..models import LLMRequest, LLMResponse, LLMUsage


_STDERR_LIMIT = 2048


class CLIProvider:
    provider_id = "cli"

    def __init__(
        self,
        *,
        model: str,
        command: Sequence[str],
        prompt_transport: Literal["argument", "stdin"],
        workspace_root: str | Path,
        working_directory: str | Path = ".",
        timeout_seconds: int = 180,
        secrets: Sequence[str] = (),
        credential_env_name: str = "",
        credential_value: str | None = None,
        stdout_limit_bytes: int = DEFAULT_STDOUT_LIMIT_BYTES,
        stderr_limit_bytes: int = DEFAULT_STDERR_LIMIT_BYTES,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise _invalid("CLI model must be a non-empty string.")
        if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
            raise _invalid("CLI command must be a non-empty string sequence.")
        if not command or any(
            not isinstance(part, str) or not part.strip() for part in command
        ):
            raise _invalid("CLI command must contain only non-empty strings.")
        if prompt_transport not in {"argument", "stdin"}:
            raise _invalid("CLI prompt transport must be 'argument' or 'stdin'.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 600
        ):
            raise _invalid("CLI timeout must be an integer between 1 and 600.")
        for name, value in (
            ("stdout_limit_bytes", stdout_limit_bytes),
            ("stderr_limit_bytes", stderr_limit_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise _invalid(f"{name} must be a positive integer.")
        if not isinstance(credential_env_name, str):
            raise _invalid("CLI credential environment name is invalid.")
        if credential_env_name and (
            not credential_env_name.strip()
            or credential_env_name != credential_env_name.strip()
            or "=" in credential_env_name
            or "\0" in credential_env_name
        ):
            raise _invalid("CLI credential environment name is invalid.")
        if credential_value is not None and (
            not isinstance(credential_value, str) or not credential_value.strip()
        ):
            raise _invalid("CLI credential value must be a non-empty string.")
        if credential_value is not None and not credential_env_name:
            raise _invalid("CLI credential value requires an environment name.")

        placeholders = validate_cli_command_placeholders(command)
        if prompt_transport == "argument" and not placeholders.intersection(
            {"prompt", "system_prompt"}
        ):
            raise _invalid(
                "Argument prompt transport requires {prompt} or {system_prompt}."
            )

        root = Path(workspace_root).resolve()
        if not root.is_dir():
            raise _invalid("CLI workspace root must be an existing directory.")
        candidate = Path(working_directory)
        if not candidate.is_absolute():
            candidate = root / candidate
        cwd = candidate.resolve()
        if cwd != root and root not in cwd.parents:
            raise _invalid("CLI working directory must remain inside the workspace.")
        if not cwd.is_dir():
            raise _invalid("CLI working directory must be an existing directory.")

        process_environment = build_cli_environment(
            os.environ,
            credential_env_name=credential_env_name,
            credential_value=credential_value,
        )
        try:
            executable = resolve_cli_executable(
                command[0], cwd=cwd, environment=process_environment
            )
        except FileNotFoundError as exc:
            raise _invalid("CLI executable could not be resolved.") from exc
        except ValueError as exc:
            raise _invalid(str(exc)) from exc

        self.model = model
        self._command = (executable, *command[1:])
        self._prompt_transport = prompt_transport
        self._workspace_root = root
        self._working_directory = cwd
        self._timeout_seconds = timeout_seconds
        self._secrets = normalize_secrets((*secrets, credential_value or ""))
        self._process_environment = process_environment
        self._stdout_limit_bytes = stdout_limit_bytes
        self._stderr_limit_bytes = stderr_limit_bytes

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not isinstance(request, LLMRequest):
            raise _invalid("request must be an LLMRequest value.")
        replacements = {
            "prompt": request.user_prompt,
            "system_prompt": request.system_prompt,
            "model": self.model,
            "workspace_root": str(self._workspace_root),
        }
        command = tuple(part.format_map(replacements) for part in self._command)
        stdin: str | None = None
        if self._prompt_transport == "stdin":
            stdin = json.dumps(
                {
                    "system_prompt": request.system_prompt,
                    "user_prompt": request.user_prompt,
                    "model": self.model,
                    "response_format": request.response_format,
                },
                ensure_ascii=False,
            )
        timeout = request.timeout_seconds or self._timeout_seconds

        try:
            completed = execute_bounded_process(
                command,
                cwd=self._working_directory,
                environment=self._process_environment,
                stdin_bytes=stdin.encode("utf-8") if stdin is not None else None,
                timeout_seconds=timeout,
                stdout_limit_bytes=self._stdout_limit_bytes,
                stderr_limit_bytes=self._stderr_limit_bytes,
            )
        except ProcessTimedOut as exc:
            raise LLMProviderError(
                code="timeout",
                message=f"CLI provider timed out after {timeout} seconds.",
                provider=self.provider_id,
                model=self.model,
                retryable=True,
                details={
                    "timeout_seconds": timeout,
                    "stdout_excerpt": _excerpt(exc.stdout, self._secrets),
                    "stderr_excerpt": _excerpt(exc.stderr, self._secrets),
                },
                secrets=self._secrets,
            ) from exc
        except ProcessOutputLimitExceeded as exc:
            raise LLMProviderError(
                code="process_error",
                message=f"CLI provider {exc.stream} exceeded its byte limit.",
                provider=self.provider_id,
                model=self.model,
                details={
                    "stream": exc.stream,
                    "observed_bytes": exc.observed_bytes,
                    "limit_bytes": exc.limit_bytes,
                    "stdout_excerpt": _excerpt(exc.stdout, self._secrets),
                    "stderr_excerpt": _excerpt(exc.stderr, self._secrets),
                },
                secrets=self._secrets,
            ) from exc
        except FileNotFoundError as exc:
            raise LLMProviderError(
                code="process_error",
                message="CLI provider executable was not found.",
                provider=self.provider_id,
                model=self.model,
                details={"executable": Path(command[0]).name},
                secrets=self._secrets,
            ) from exc
        except ValueError as exc:
            raise LLMProviderError(
                code="process_error",
                message="CLI provider process arguments are invalid.",
                provider=self.provider_id,
                model=self.model,
                details={"reason": str(exc)},
                secrets=self._secrets,
            ) from exc
        except OSError as exc:
            raise LLMProviderError(
                code="process_error",
                message="CLI provider process could not be started.",
                provider=self.provider_id,
                model=self.model,
                details={"reason": str(exc)},
                secrets=self._secrets,
            ) from exc

        if completed.returncode != 0:
            stderr = _excerpt(completed.stderr, self._secrets)
            raise LLMProviderError(
                code="process_error",
                message="CLI provider process exited with a non-zero status.",
                provider=self.provider_id,
                model=self.model,
                details={
                    "exit_code": completed.returncode,
                    "stderr": stderr[:_STDERR_LIMIT],
                },
                secrets=self._secrets,
            )

        return self._parse_stdout(completed.stdout.decode("utf-8", errors="replace"), request)

    def _parse_stdout(self, stdout: str, request: LLMRequest) -> LLMResponse:
        output = stdout.strip()
        if not output:
            raise LLMProviderError(
                code="invalid_response",
                message="CLI provider returned empty stdout.",
                provider=self.provider_id,
                model=self.model,
            )

        raw_response: Mapping[str, object] | None = None
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            content_value = redact_secrets(output, self._secrets)
            content = content_value if isinstance(content_value, str) else str(content_value)
        else:
            sanitized = redact_secrets(parsed, self._secrets)
            if isinstance(sanitized, Mapping) and "content" in sanitized:
                content_value = sanitized["content"]
                if not isinstance(content_value, str) or not content_value.strip():
                    raise LLMProviderError(
                        code="invalid_response",
                        message="CLI JSON stdout content must be a non-empty string.",
                        provider=self.provider_id,
                        model=self.model,
                    )
                content = content_value.strip()
                raw_response = dict(sanitized)
            elif request.response_format == "json":
                content = json.dumps(
                    sanitized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                raw_response = (
                    dict(sanitized)
                    if isinstance(sanitized, Mapping)
                    else {"output": sanitized}
                )
            else:
                content_value = redact_secrets(output, self._secrets)
                content = (
                    content_value
                    if isinstance(content_value, str)
                    else str(content_value)
                )
                raw_response = (
                    dict(sanitized)
                    if isinstance(sanitized, Mapping)
                    else {"output": sanitized}
                )

        if request.response_format == "json":
            validate_structured_content(content)

        finish_reason = None
        if raw_response is not None and isinstance(raw_response.get("finish_reason"), str):
            finish_reason = str(raw_response["finish_reason"])
        return LLMResponse(
            content=content,
            provider=self.provider_id,
            model=self.model,
            usage=LLMUsage(),
            finish_reason=finish_reason,
            raw_response=raw_response,
        )
def _invalid(message: str) -> LLMProviderError:
    return LLMProviderError(code="invalid_configuration", message=message)


def _excerpt(value: bytes, secrets: Sequence[str]) -> str:
    decoded = value.decode("utf-8", errors="replace")
    return str(redact_secrets(decoded, secrets))
