from __future__ import annotations

from typing import Any


SELF_CORR_NEAR_REPAIR_MAX = 0.72
EXTREME_SELF_CORR_REPLACE_MIN = 0.90


def self_corr_bucket(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return "unknown"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if number >= EXTREME_SELF_CORR_REPLACE_MIN:
        return "extreme"
    if number > SELF_CORR_NEAR_REPAIR_MAX:
        return "moderate"
    return "mild"
