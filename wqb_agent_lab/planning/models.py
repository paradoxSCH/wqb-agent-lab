from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from src.contracts import ValidationError, validate_contract


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    frozen = _freeze_json(value or {})
    if not isinstance(frozen, Mapping):
        raise TypeError("expected a JSON object")
    return frozen


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _objects(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


@dataclass(frozen=True, slots=True)
class HypothesisProposal:
    thesis: str
    hypothesis_id: str = ""
    mechanism: str = ""
    expressions: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    uncertainty: float | None = None
    kill_conditions: tuple[str, ...] = ()
    requested_budget: int = 0
    extensions: Mapping[str, Any] = field(default_factory=_freeze_mapping)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> HypothesisProposal:
        uncertainty = payload.get("uncertainty")
        return cls(
            hypothesis_id=str(payload.get("hypothesis_id") or ""),
            thesis=str(payload["thesis"]),
            mechanism=str(payload.get("mechanism") or ""),
            expressions=_strings(payload.get("expressions")),
            evidence_refs=_strings(payload.get("evidence_refs")),
            assumptions=_strings(payload.get("assumptions")),
            uncertainty=float(uncertainty) if uncertainty is not None else None,
            kill_conditions=_strings(payload.get("kill_conditions")),
            requested_budget=int(payload.get("requested_budget") or 0),
            extensions=_freeze_mapping(_optional_mapping(payload.get("extensions"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "thesis": self.thesis,
            "mechanism": self.mechanism,
            "expressions": list(self.expressions),
            "evidence_refs": list(self.evidence_refs),
            "assumptions": list(self.assumptions),
            "uncertainty": self.uncertainty,
            "kill_conditions": list(self.kill_conditions),
            "requested_budget": self.requested_budget,
            "extensions": _thaw_json(self.extensions),
        }


@dataclass(frozen=True, slots=True)
class RequestedAction:
    action_id: str
    kind: str
    candidate_ref: str = ""
    rationale: str = ""
    priority: int = 0
    parameters: Mapping[str, Any] = field(default_factory=_freeze_mapping)
    extensions: Mapping[str, Any] = field(default_factory=_freeze_mapping)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RequestedAction:
        return cls(
            action_id=str(payload["action_id"]),
            kind=str(payload["kind"]),
            candidate_ref=str(payload.get("candidate_ref") or ""),
            rationale=str(payload.get("rationale") or ""),
            priority=int(payload.get("priority") or 0),
            parameters=_freeze_mapping(_optional_mapping(payload.get("parameters"))),
            extensions=_freeze_mapping(_optional_mapping(payload.get("extensions"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind,
            "candidate_ref": self.candidate_ref,
            "rationale": self.rationale,
            "priority": self.priority,
            "parameters": _thaw_json(self.parameters),
            "extensions": _thaw_json(self.extensions),
        }


@dataclass(frozen=True, slots=True)
class PolicyExceptionRequest:
    policy_id: str
    rationale: str
    evidence_refs: tuple[str, ...] = ()
    extensions: Mapping[str, Any] = field(default_factory=_freeze_mapping)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PolicyExceptionRequest:
        return cls(
            policy_id=str(payload["policy_id"]),
            rationale=str(payload["rationale"]),
            evidence_refs=_strings(payload.get("evidence_refs")),
            extensions=_freeze_mapping(_optional_mapping(payload.get("extensions"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
            "extensions": _thaw_json(self.extensions),
        }


@dataclass(frozen=True, slots=True)
class PlanProposal:
    schema_version: int
    plan_id: str
    objective: str
    hypotheses: tuple[HypothesisProposal, ...]
    requested_actions: tuple[RequestedAction, ...]
    alternatives: tuple[Mapping[str, Any], ...] = ()
    policy_exception_requests: tuple[PolicyExceptionRequest, ...] = ()
    freeform_notes: str = ""
    extensions: Mapping[str, Any] = field(default_factory=_freeze_mapping)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PlanProposal:
        return cls(
            schema_version=int(payload["schema_version"]),
            plan_id=str(payload["plan_id"]),
            objective=str(payload["objective"]),
            hypotheses=tuple(HypothesisProposal.from_dict(item) for item in _objects(payload["hypotheses"])),
            requested_actions=tuple(
                RequestedAction.from_dict(item) for item in _objects(payload["requested_actions"])
            ),
            alternatives=tuple(_freeze_mapping(item) for item in _objects(payload.get("alternatives"))),
            policy_exception_requests=tuple(
                PolicyExceptionRequest.from_dict(item)
                for item in _objects(payload.get("policy_exception_requests"))
            ),
            freeform_notes=str(payload.get("freeform_notes") or ""),
            extensions=_freeze_mapping(_optional_mapping(payload.get("extensions"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "objective": self.objective,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "requested_actions": [item.to_dict() for item in self.requested_actions],
            "alternatives": [_thaw_json(item) for item in self.alternatives],
            "policy_exception_requests": [item.to_dict() for item in self.policy_exception_requests],
            "freeform_notes": self.freeform_notes,
            "extensions": _thaw_json(self.extensions),
        }


class PlanProposalValidationError(ValueError):
    def __init__(self, errors: list[ValidationError]) -> None:
        self.errors = tuple(errors)
        details = "; ".join(str(error) for error in errors)
        super().__init__(f"plan proposal validation failed: {details}")

    def repair_feedback(self) -> str:
        lines = [
            "Return a corrected plan proposal without narrowing or discarding the research ideas.",
            "Fix only the structural contract errors below:",
        ]
        lines.extend(f"- {error}" for error in self.errors)
        return "\n".join(lines)


def parse_plan_proposal(payload: Any) -> PlanProposal:
    if not isinstance(payload, Mapping):
        raise PlanProposalValidationError(
            [ValidationError("$", f"expected object, got {type(payload).__name__}")]
        )
    normalized = _thaw_json(_freeze_json(payload))
    if not isinstance(normalized, dict):
        raise TypeError("plan proposal must be a JSON object")
    errors = validate_contract("plan_proposal", normalized)
    if errors:
        raise PlanProposalValidationError(errors)
    return PlanProposal.from_dict(normalized)


def _optional_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
