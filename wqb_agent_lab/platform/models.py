from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SUBMITTED_STATUSES = {"ACTIVE", "SUBMITTED"}


def is_submitted_status(status: str | None, date_submitted: str | None) -> bool:
    return str(status or "").upper() in SUBMITTED_STATUSES or bool(date_submitted)


@dataclass(slots=True)
class WQBCheck:
    name: str
    result: str | None = None
    limit: Any = None
    value: Any = None
    message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "WQBCheck":
        return cls(
            name=str(payload.get("name") or "UNKNOWN"),
            result=payload.get("result"),
            limit=payload.get("limit"),
            value=payload.get("value"),
            message=payload.get("message"),
            raw=dict(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WQBAlphaDetail:
    alpha_id: str
    http_status: int | None = None
    status: str | None = None
    date_submitted: str | None = None
    expression: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    checks: list[WQBCheck] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, http_status: int | None = None) -> "WQBAlphaDetail":
        raw_is_data = payload.get("is")
        is_data: dict[str, Any] = raw_is_data if isinstance(raw_is_data, dict) else {}
        raw_regular = payload.get("regular")
        regular: dict[str, Any] = raw_regular if isinstance(raw_regular, dict) else {}
        checks_payload = extract_checks(payload)
        return cls(
            alpha_id=str(payload.get("id") or payload.get("alpha_id") or ""),
            http_status=http_status,
            status=payload.get("status"),
            date_submitted=payload.get("dateSubmitted"),
            expression=str(regular.get("code") or payload.get("expression") or ""),
            metrics={
                "sharpe": is_data.get("sharpe"),
                "fitness": is_data.get("fitness"),
                "turnover": is_data.get("turnover"),
                "returns": is_data.get("returns"),
                "drawdown": is_data.get("drawdown"),
                "margin": is_data.get("margin"),
            },
            checks=[WQBCheck.from_payload(item) for item in checks_payload],
            raw=dict(payload),
        )

    @property
    def is_submitted(self) -> bool:
        return is_submitted_status(self.status, self.date_submitted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha_id": self.alpha_id,
            "http_status": self.http_status,
            "status": self.status,
            "dateSubmitted": self.date_submitted,
            "expression": self.expression,
            "metrics": dict(self.metrics),
            "checks": [check.to_dict() for check in self.checks],
            "is_submitted": self.is_submitted,
        }


@dataclass(slots=True)
class WQBSubmitResult:
    alpha_id: str
    post_status: str
    confirmation_status: str
    platform_status: str | None = None
    date_submitted: str | None = None
    retry_after_seconds: int | None = None
    diagnosis: str = ""
    post_status_code: int | None = None
    detail_status_code: int | None = None
    response_text: str = ""
    checks: list[WQBCheck] = field(default_factory=list)

    @property
    def submitted(self) -> bool:
        return self.confirmation_status == "confirmed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha_id": self.alpha_id,
            "post_status": self.post_status,
            "confirmation_status": self.confirmation_status,
            "platform_status": self.platform_status,
            "dateSubmitted": self.date_submitted,
            "retry_after_seconds": self.retry_after_seconds,
            "diagnosis": self.diagnosis,
            "post_status_code": self.post_status_code,
            "detail_status_code": self.detail_status_code,
            "response_text": self.response_text,
            "checks": [check.to_dict() for check in self.checks],
            "submitted": self.submitted,
        }


@dataclass(slots=True)
class WQBSimulationRequest:
    expression: str
    settings: dict[str, Any]
    alpha_type: str = "REGULAR"

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": self.alpha_type,
            "settings": dict(self.settings),
            "regular": self.expression,
        }


@dataclass(slots=True)
class WQBSimulationCreated:
    success: bool
    location: str
    simulation_id: str | None = None
    status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_checks(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    direct = payload.get("checks")
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]
    nested = payload.get("is")
    if isinstance(nested, dict) and isinstance(nested.get("checks"), list):
        return [item for item in nested["checks"] if isinstance(item, dict)]
    return []
