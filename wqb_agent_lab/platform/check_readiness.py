from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


REQUIRED_SUBMISSION_CHECK_NAMES = frozenset(
    {
        "LOW_SHARPE",
        "LOW_FITNESS",
        "LOW_TURNOVER",
        "HIGH_TURNOVER",
        "CONCENTRATED_WEIGHT",
        "LOW_SUB_UNIVERSE_SHARPE",
        "SELF_CORRELATION",
        "MATCHES_COMPETITION",
    }
)

FAILED_RESULTS = frozenset({"FAIL", "ERROR"})
WAITING_RESULTS = frozenset({"PENDING", "UNKNOWN", ""})


@dataclass(frozen=True, slots=True)
class CheckReadiness:
    status: str
    checks: tuple[dict[str, Any], ...]
    failed_checks: tuple[str, ...]
    pending_checks: tuple[str, ...]
    missing_checks: tuple[str, ...]
    unknown_checks: tuple[str, ...]
    fingerprint: str

    @property
    def ready(self) -> bool:
        return self.status == "ready"


def normalize_checks(checks: Iterable[Mapping[str, Any]] | None) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    for check in checks or ():
        if not isinstance(check, Mapping):
            continue
        normalized.append(
            {
                "name": str(check.get("name") or "").strip().upper(),
                "result": str(check.get("result") or "UNKNOWN").strip().upper(),
                "value": check.get("value"),
                "limit": check.get("limit"),
            }
        )
    return tuple(sorted(normalized, key=lambda item: item["name"]))


def evaluate_check_snapshot(
    checks: Iterable[Mapping[str, Any]] | None,
    *,
    required_names: Iterable[str] = REQUIRED_SUBMISSION_CHECK_NAMES,
) -> CheckReadiness:
    normalized = normalize_checks(checks)
    names = {check["name"] for check in normalized if check["name"]}
    required = {str(name).strip().upper() for name in required_names}
    missing = sorted(required - names)
    failed = sorted({check["name"] for check in normalized if check["result"] in FAILED_RESULTS})
    pending = sorted({check["name"] for check in normalized if check["result"] == "PENDING"})
    unknown = sorted({check["name"] for check in normalized if check["result"] in WAITING_RESULTS})

    self_corr = next((check for check in normalized if check["name"] == "SELF_CORRELATION"), None)
    if self_corr is not None:
        value = _number(self_corr.get("value"))
        limit = _number(self_corr.get("limit"))
        if value is None or limit is None:
            unknown = sorted(set(unknown) | {"SELF_CORRELATION"})
        elif value > limit:
            failed = sorted(set(failed) | {"SELF_CORRELATION"})

    if any(check["name"] == "ALREADY_SUBMITTED" for check in normalized):
        status = "already_submitted"
    elif failed:
        status = "failed"
    elif missing or pending or unknown:
        status = "waiting"
    else:
        status = "ready"

    canonical = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"), sort_keys=True, default=str)
    return CheckReadiness(
        status=status,
        checks=normalized,
        failed_checks=tuple(failed),
        pending_checks=tuple(pending),
        missing_checks=tuple(missing),
        unknown_checks=tuple(unknown),
        fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
