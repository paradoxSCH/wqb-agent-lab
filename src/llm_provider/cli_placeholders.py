from __future__ import annotations

import string
from collections.abc import Sequence

from .errors import LLMProviderError


ALLOWED_CLI_PLACEHOLDERS = frozenset(
    {"prompt", "system_prompt", "model", "workspace_root"}
)


def validate_cli_command_placeholders(command: Sequence[str]) -> frozenset[str]:
    placeholders: set[str] = set()
    formatter = string.Formatter()
    for index, part in enumerate(command):
        try:
            parsed = formatter.parse(part)
            for _, field_name, format_spec, conversion in parsed:
                if field_name is None:
                    continue
                if (
                    field_name not in ALLOWED_CLI_PLACEHOLDERS
                    or format_spec
                    or conversion
                ):
                    raise _invalid(
                        f"Unsupported CLI command placeholder: {field_name or part}"
                    )
                if index == 0:
                    raise _invalid("CLI executable must be static, not a placeholder.")
                placeholders.add(field_name)
        except ValueError as exc:
            raise _invalid("CLI command contains malformed placeholders.") from exc
    if command and command[0].lower().endswith((".cmd", ".bat")):
        raise _invalid("Windows batch executables are not supported.")
    return frozenset(placeholders)


def _invalid(message: str) -> LLMProviderError:
    return LLMProviderError(code="invalid_configuration", message=message)
