from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class SubmitDecision:
    decision_id: str
    alpha_id: str
    requested_mode: str
    agent_id: str
    rationale: str
    policy_evaluation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SubmitDecision":
        return cls(
            decision_id=str(payload.get("decision_id") or ""),
            alpha_id=str(payload.get("alpha_id") or ""),
            requested_mode=str(payload.get("requested_mode") or "queue_only"),
            agent_id=str(payload.get("agent_id") or ""),
            rationale=str(payload.get("rationale") or ""),
            policy_evaluation_id=payload.get("policy_evaluation_id"),
            metadata=dict(payload.get("metadata") or {}),
            created_at=str(payload.get("created_at") or datetime.now().isoformat(timespec="seconds")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PolicyEvaluation:
    evaluation_id: str
    decision_id: str
    allowed: bool
    policy_action: str
    reasons: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PolicyEvaluation":
        return cls(
            evaluation_id=str(payload.get("evaluation_id") or ""),
            decision_id=str(payload.get("decision_id") or ""),
            allowed=bool(payload.get("allowed")),
            policy_action=str(payload.get("policy_action") or "block"),
            reasons=[str(item) for item in payload.get("reasons") or []],
            created_at=str(payload.get("created_at") or datetime.now().isoformat(timespec="seconds")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SubmissionAuditEvent:
    event_type: str
    decision_id: str
    alpha_id: str | None = None
    status: str | None = None
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
