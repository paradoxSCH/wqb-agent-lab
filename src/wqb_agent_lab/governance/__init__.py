"""Compatibility import for the canonical installed namespace."""

from wqb_agent_lab.governance import (
    ActionPolicyDecision,
    PlanningPolicyContext,
    PlanningPolicyDecision,
    PolicyFinding,
    SubmissionPolicyEvaluator,
    evaluate_plan_proposal,
    require_side_effect_capability,
)

__all__ = [
    "ActionPolicyDecision",
    "PlanningPolicyContext",
    "PlanningPolicyDecision",
    "PolicyFinding",
    "SubmissionPolicyEvaluator",
    "evaluate_plan_proposal",
    "require_side_effect_capability",
]
