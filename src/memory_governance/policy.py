from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.alpha_memory.schema import MemoryNode


EVIDENCE_LEVELS = ("L0", "L1", "L2", "L3", "L4")


@dataclass(frozen=True)
class EvidenceAssessment:
    evidence_level: str
    reasons: tuple[str, ...]
    tested_count: int = 0
    near_pass_count: int = 0
    all_pass_count: int = 0


@dataclass(frozen=True)
class ActionPermission:
    can_use_in_prompt: bool
    can_increase_budget: bool
    can_promote: bool
    can_block_generation: bool
    can_be_default_prior: bool
    max_budget_policy: str


@dataclass(frozen=True)
class ForgettingUpdate:
    status: str
    forgetting_state: str
    confidence_delta: float
    decay_delta: float
    reason: str


def assess_evidence(metrics: Mapping[str, Any]) -> EvidenceAssessment:
    tested_count = _int(metrics.get("tested_count"))
    near_pass_count = _int(metrics.get("near_pass_count"))
    all_pass_count = _int(metrics.get("all_pass_count"))
    skeleton_diversity = _int(metrics.get("skeleton_diversity"))
    field_diversity = _int(metrics.get("field_diversity"))
    repeated_run_count = _int(metrics.get("repeated_run_count"))
    low_value_rate = _float(metrics.get("low_value_rate"))
    decision_outcome_lift = _float(metrics.get("decision_outcome_lift"))
    has_field_proxy = metrics.get("has_field_proxy", True)

    reasons: list[str] = []
    if not has_field_proxy:
        return EvidenceAssessment("L0", ("missing WQB field proxy",), tested_count, near_pass_count, all_pass_count)

    if (
        tested_count >= 40
        and repeated_run_count >= 3
        and all_pass_count >= 2
        and skeleton_diversity >= 4
        and field_diversity >= 3
        and low_value_rate <= 0.5
    ):
        reasons.append("stable repeated multi-run evidence")
        return EvidenceAssessment("L4", tuple(reasons), tested_count, near_pass_count, all_pass_count)

    if (
        tested_count >= 20
        and all_pass_count >= 1
        and skeleton_diversity >= 3
        and field_diversity >= 2
        and low_value_rate <= 0.75
        and decision_outcome_lift > 0.0
    ):
        reasons.append("actionable outcome-backed evidence")
        return EvidenceAssessment("L3", tuple(reasons), tested_count, near_pass_count, all_pass_count)

    if (
        tested_count >= 20
        and (near_pass_count >= 2 or all_pass_count >= 1)
        and skeleton_diversity >= 3
        and field_diversity >= 2
        and low_value_rate <= 0.75
    ):
        reasons.append("repeated local pattern")
        return EvidenceAssessment("L2", tuple(reasons), tested_count, near_pass_count, all_pass_count)

    if tested_count >= 3 or near_pass_count >= 1:
        reasons.append("local weak pattern")
        return EvidenceAssessment("L1", tuple(reasons), tested_count, near_pass_count, all_pass_count)

    reasons.append("raw observation")
    return EvidenceAssessment("L0", tuple(reasons), tested_count, near_pass_count, all_pass_count)


def resolve_action_permission(assessment: EvidenceAssessment) -> ActionPermission:
    if assessment.evidence_level in {"L0", "L1"}:
        return ActionPermission(
            can_use_in_prompt=True,
            can_increase_budget=False,
            can_promote=False,
            can_block_generation=False,
            can_be_default_prior=False,
            max_budget_policy="none",
        )
    if assessment.evidence_level == "L2":
        return ActionPermission(
            can_use_in_prompt=True,
            can_increase_budget=False,
            can_promote=False,
            can_block_generation=False,
            can_be_default_prior=False,
            max_budget_policy="policy_evaluator_required",
        )
    if assessment.evidence_level == "L3":
        return ActionPermission(
            can_use_in_prompt=True,
            can_increase_budget=False,
            can_promote=True,
            can_block_generation=False,
            can_be_default_prior=False,
            max_budget_policy="policy_evaluator_required",
        )
    return ActionPermission(
        can_use_in_prompt=True,
        can_increase_budget=False,
        can_promote=True,
        can_block_generation=False,
        can_be_default_prior=True,
        max_budget_policy="policy_evaluator_required",
    )


def evaluate_forgetting(metrics: Mapping[str, Any]) -> ForgettingUpdate:
    if bool(metrics.get("duplicate")) or _int(metrics.get("blocked_skeleton_count")) >= 2:
        return ForgettingUpdate(
            status="blocked",
            forgetting_state="forgotten",
            confidence_delta=-1.0,
            decay_delta=1.0,
            reason="duplicate or repeatedly blocked skeleton",
        )

    if bool(metrics.get("live_recheck_failed")) or bool(metrics.get("submit_failed")):
        return ForgettingUpdate(
            status="deprecated",
            forgetting_state="quarantined",
            confidence_delta=-0.5,
            decay_delta=0.7,
            reason="live recheck or submit contradicted memory",
        )

    low_fitness_count = _int(metrics.get("low_fitness_count"))
    low_sharpe_count = _int(metrics.get("low_sharpe_count"))
    tested_count = max(_int(metrics.get("tested_count")), 1)
    all_pass_count = _int(metrics.get("all_pass_count"))
    near_pass_count = _int(metrics.get("near_pass_count"))
    if (
        tested_count >= 20
        and all_pass_count == 0
        and near_pass_count == 0
        and (low_fitness_count / tested_count >= 0.6 or low_sharpe_count / tested_count >= 0.6)
    ):
        return ForgettingUpdate(
            status="deprecated",
            forgetting_state="quarantined",
            confidence_delta=-0.4,
            decay_delta=0.6,
            reason="persistent weak simulation outcomes",
        )

    if _int(metrics.get("consecutive_proxy_weak_count")) >= 2:
        return ForgettingUpdate(
            status="probation",
            forgetting_state="quarantined",
            confidence_delta=-0.25,
            decay_delta=0.35,
            reason="proxy map repeatedly downgraded this memory",
        )

    return ForgettingUpdate(
        status="active",
        forgetting_state="active",
        confidence_delta=0.0,
        decay_delta=0.0,
        reason="no forgetting trigger",
    )


def is_retrievable_for_mode(node: MemoryNode, mode: str = "planner") -> bool:
    status = str(node.status or "active")
    forgetting_state = str(node.forgetting_state or "active")
    if mode == "audit":
        return True
    if mode == "risk_review":
        return status in {"active", "probation", "deprecated"} and forgetting_state != "forgotten"
    return status == "active" and forgetting_state == "active"


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
