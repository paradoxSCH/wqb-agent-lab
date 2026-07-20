"""Portable JSON-output helpers for machine-readable commands."""

from __future__ import annotations

import json
from typing import Any, TextIO


def write_json_line(payload: Any, stdout: TextIO, *, sort_keys: bool = True) -> None:
    """Write JSON that is safe for UTF-8 and legacy Windows console encodings."""

    stdout.write(json.dumps(payload, ensure_ascii=True, sort_keys=sort_keys) + "\n")
