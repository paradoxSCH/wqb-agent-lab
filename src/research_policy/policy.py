from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from wqb_agent_lab.contracts import validate_contract


class ResearchPolicyError(ValueError):
    """A fail-closed policy configuration error with a stable machine code."""

    def __init__(self, code: str, message: str, *, path: str = "$.research_policy") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path

    def __str__(self) -> str:
        return f"{self.code} at {self.path}: {self.message}"


@dataclass(frozen=True, slots=True)
class ResearchBudget:
    daily_simulation_limit: int
    exploration_share_limit: float
    exploration_stages: tuple[str, ...]
    stage_allocations: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "daily_simulation_limit": self.daily_simulation_limit,
            "exploration_share_limit": self.exploration_share_limit,
            "exploration_stages": list(self.exploration_stages),
            "stage_allocations": dict(self.stage_allocations),
        }


@dataclass(frozen=True, slots=True)
class BehavioralMechanism:
    mechanism_id: str
    enabled: bool
    allowed_proxy_fields: tuple[str, ...]
    kill_conditions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism_id": self.mechanism_id,
            "enabled": self.enabled,
            "allowed_proxy_fields": list(self.allowed_proxy_fields),
            "kill_conditions": list(self.kill_conditions),
        }


@dataclass(frozen=True, slots=True)
class BehavioralBoundaries:
    block_unclassified_candidates: bool
    require_kill_conditions: bool
    forbid_pure_price_volume: bool
    mechanisms: tuple[BehavioralMechanism, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_unclassified_candidates": self.block_unclassified_candidates,
            "require_kill_conditions": self.require_kill_conditions,
            "forbid_pure_price_volume": self.forbid_pure_price_volume,
            "mechanisms": [mechanism.to_dict() for mechanism in self.mechanisms],
        }


@dataclass(frozen=True, slots=True)
class ResearchPolicy:
    version: int
    budget: ResearchBudget
    behavioral_boundaries: BehavioralBoundaries

    @property
    def enabled_mechanism_ids(self) -> tuple[str, ...]:
        return tuple(
            mechanism.mechanism_id
            for mechanism in self.behavioral_boundaries.mechanisms
            if mechanism.enabled
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "budget": self.budget.to_dict(),
            "behavioral_boundaries": self.behavioral_boundaries.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class BoundaryIssue:
    code: str
    message: str
    path: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass(frozen=True, slots=True)
class BoundaryEvaluation:
    candidate_id: str
    behavioral_mechanism: str
    errors: tuple[BoundaryIssue, ...]

    @property
    def allowed(self) -> bool:
        return not self.errors

    @property
    def error_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "behavioral_mechanism": self.behavioral_mechanism,
            "allowed": self.allowed,
            "errors": [issue.to_dict() for issue in self.errors],
        }


def load_research_policy(config: Mapping[str, Any]) -> ResearchPolicy:
    if not isinstance(config, Mapping):
        raise ResearchPolicyError(
            "invalid_research_policy",
            "Workflow configuration must be an object.",
            path="$",
        )
    raw = config.get("research_policy")
    if raw is None:
        raise ResearchPolicyError(
            "missing_research_policy",
            "Workflow configuration must define research_policy.",
        )
    if not isinstance(raw, dict):
        raise ResearchPolicyError(
            "invalid_research_policy",
            "research_policy must be an object.",
        )

    contract_errors = validate_contract("research_policy", raw)
    if contract_errors:
        first = contract_errors[0]
        suffix = first.path[1:] if first.path.startswith("$") else f".{first.path}"
        raise ResearchPolicyError(
            "invalid_research_policy",
            first.message,
            path=f"$.research_policy{suffix}",
        )

    budget_data = raw["budget"]
    allocations = dict(budget_data["stage_allocations"])
    if not allocations:
        raise ResearchPolicyError(
            "missing_stage_allocations",
            "At least one stage allocation is required.",
            path="$.research_policy.budget.stage_allocations",
        )
    if sum(allocations.values()) != budget_data["daily_simulation_limit"]:
        raise ResearchPolicyError(
            "budget_allocation_mismatch",
            "Stage allocations must sum exactly to daily_simulation_limit.",
            path="$.research_policy.budget.stage_allocations",
        )
    exploration_stages = _non_empty_strings(
        budget_data["exploration_stages"],
        "exploration_stages",
        "$.research_policy.budget",
    )
    if len(set(exploration_stages)) != len(exploration_stages):
        raise ResearchPolicyError(
            "duplicate_exploration_stage",
            "exploration_stages must contain unique stage names.",
            path="$.research_policy.budget.exploration_stages",
        )
    unknown_exploration_stages = sorted(set(exploration_stages) - set(allocations))
    if unknown_exploration_stages:
        raise ResearchPolicyError(
            "unknown_exploration_stage",
            f"Exploration stages are not allocated: {', '.join(unknown_exploration_stages)}",
            path="$.research_policy.budget.exploration_stages",
        )
    exploration_budget = sum(int(allocations[stage]) for stage in exploration_stages)
    exploration_limit = float(budget_data["daily_simulation_limit"]) * float(
        budget_data["exploration_share_limit"]
    )
    if exploration_budget > exploration_limit:
        raise ResearchPolicyError(
            "exploration_budget_exceeded",
            "Exploration stage allocations exceed exploration_share_limit.",
            path="$.research_policy.budget.stage_allocations",
        )
    boundaries_data = raw["behavioral_boundaries"]
    mechanism_data = boundaries_data["mechanisms"]
    seen: set[str] = set()
    mechanisms: list[BehavioralMechanism] = []
    for index, item in enumerate(mechanism_data):
        mechanism_id = item["mechanism_id"].strip()
        path = f"$.research_policy.behavioral_boundaries.mechanisms[{index}]"
        if not mechanism_id:
            raise ResearchPolicyError(
                "invalid_behavioral_mechanism",
                "mechanism_id must not be empty.",
                path=f"{path}.mechanism_id",
            )
        if mechanism_id in seen:
            raise ResearchPolicyError(
                "duplicate_behavioral_mechanism",
                f"Duplicate mechanism_id: {mechanism_id}",
                path=f"{path}.mechanism_id",
            )
        seen.add(mechanism_id)

        allowed_fields = _non_empty_strings(item["allowed_proxy_fields"], "allowed_proxy_fields", path)
        kill_conditions = _non_empty_strings(item["kill_conditions"], "kill_conditions", path)
        if item["enabled"] and not allowed_fields:
            raise ResearchPolicyError(
                "missing_allowed_proxy_fields",
                "An enabled mechanism must declare allowed proxy fields.",
                path=f"{path}.allowed_proxy_fields",
            )
        if item["enabled"] and not kill_conditions:
            raise ResearchPolicyError(
                "missing_mechanism_kill_conditions",
                "An enabled mechanism must declare kill conditions.",
                path=f"{path}.kill_conditions",
            )
        mechanisms.append(
            BehavioralMechanism(
                mechanism_id=mechanism_id,
                enabled=item["enabled"],
                allowed_proxy_fields=allowed_fields,
                kill_conditions=kill_conditions,
            )
        )

    if not any(mechanism.enabled for mechanism in mechanisms):
        raise ResearchPolicyError(
            "no_enabled_behavioral_mechanism",
            "At least one behavioral mechanism must be enabled.",
            path="$.research_policy.behavioral_boundaries.mechanisms",
        )

    return ResearchPolicy(
        version=raw["version"],
        budget=ResearchBudget(
            daily_simulation_limit=budget_data["daily_simulation_limit"],
            exploration_share_limit=float(budget_data["exploration_share_limit"]),
            exploration_stages=exploration_stages,
            stage_allocations=allocations,
        ),
        behavioral_boundaries=BehavioralBoundaries(
            block_unclassified_candidates=boundaries_data["block_unclassified_candidates"],
            require_kill_conditions=boundaries_data["require_kill_conditions"],
            forbid_pure_price_volume=boundaries_data["forbid_pure_price_volume"],
            mechanisms=tuple(mechanisms),
        ),
    )


def evaluate_candidate_boundaries(
    candidate: Mapping[str, Any], policy: ResearchPolicy
) -> BoundaryEvaluation:
    candidate_id = str(candidate.get("candidate_id") or "")
    mechanism_id = str(candidate.get("behavioral_mechanism") or "").strip()
    fields = _candidate_strings(candidate.get("fields"))
    kill_conditions = set(_candidate_strings(candidate.get("kill_conditions")))
    issues: list[BoundaryIssue] = []
    boundaries = policy.behavioral_boundaries
    mechanisms = {mechanism.mechanism_id: mechanism for mechanism in boundaries.mechanisms}
    mechanism = mechanisms.get(mechanism_id)

    if not mechanism_id:
        if boundaries.block_unclassified_candidates:
            issues.append(
                BoundaryIssue(
                    "unclassified_behavioral_candidate",
                    "Candidate does not declare a behavioral mechanism.",
                    "$.behavioral_mechanism",
                )
            )
    elif mechanism is None:
        issues.append(
            BoundaryIssue(
                "unknown_behavioral_mechanism",
                f"Unknown behavioral mechanism: {mechanism_id}",
                "$.behavioral_mechanism",
            )
        )
    elif not mechanism.enabled:
        issues.append(
            BoundaryIssue(
                "disabled_behavioral_mechanism",
                f"Behavioral mechanism is disabled: {mechanism_id}",
                "$.behavioral_mechanism",
            )
        )

    if not fields:
        issues.append(BoundaryIssue("missing_proxy_fields", "Candidate must declare proxy fields.", "$.fields"))
    elif mechanism is not None and mechanism.enabled:
        outside = [
            field
            for field in fields
            if not any(fnmatch.fnmatchcase(field, pattern) for pattern in mechanism.allowed_proxy_fields)
        ]
        if outside:
            issues.append(
                BoundaryIssue(
                    "proxy_field_outside_boundary",
                    f"Proxy fields outside mechanism boundary: {', '.join(outside)}",
                    "$.fields",
                )
            )

    if boundaries.require_kill_conditions and mechanism is not None and mechanism.enabled:
        missing = [condition for condition in mechanism.kill_conditions if condition not in kill_conditions]
        if missing:
            issues.append(
                BoundaryIssue(
                    "missing_required_kill_condition",
                    f"Missing required kill conditions: {', '.join(missing)}",
                    "$.kill_conditions",
                )
            )

    if boundaries.forbid_pure_price_volume and fields and all(_is_price_volume_field(field) for field in fields):
        issues.append(
            BoundaryIssue(
                "pure_price_volume_candidate",
                "Pure price-volume candidates are forbidden by policy.",
                "$.fields",
            )
        )

    return BoundaryEvaluation(candidate_id, mechanism_id, tuple(issues))


def policy_digest(policy: ResearchPolicy) -> str:
    canonical = json.dumps(policy.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _non_empty_strings(values: list[Any], name: str, parent_path: str) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in values)
    if any(not value for value in normalized):
        raise ResearchPolicyError(
            "invalid_research_policy",
            f"{name} must contain non-empty strings.",
            path=f"{parent_path}.{name}",
        )
    return normalized


def _candidate_strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    if any(not isinstance(item, str) for item in value):
        return ()
    return tuple(item.strip() for item in value if item.strip())


def _is_price_volume_field(field: str) -> bool:
    normalized = field.lower()
    exact = {
        "open",
        "high",
        "low",
        "close",
        "vwap",
        "volume",
        "returns",
        "return",
        "adv20",
        "adv60",
        "adv120",
    }
    return normalized in exact or normalized.startswith(("price_", "volume_", "returns_", "adv"))
