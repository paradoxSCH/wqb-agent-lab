from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from wqb_agent_lab.planning import PlanProposal, RequestedAction


PolicyStrength = Literal["hard", "soft"]
PolicyDisposition = Literal["allow", "explore", "defer", "deny"]

_SIDE_EFFECT_ACTIONS = {
    "simulate": "simulation",
    "simulation": "simulation",
    "submit": "submission",
    "submission": "submission",
}
_READ_ONLY_ACTIONS = frozenset(
    {
        "inspect_artifact",
        "preflight",
        "query_operator_catalog",
        "query_registry",
        "retrieve_memory",
    }
)


@dataclass(frozen=True, slots=True)
class PolicyFinding:
    policy_id: str
    strength: PolicyStrength
    message: str
    subject_ref: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "policy_id": self.policy_id,
            "strength": self.strength,
            "message": self.message,
            "subject_ref": self.subject_ref,
        }


@dataclass(frozen=True, slots=True)
class ActionPolicyDecision:
    action_id: str
    kind: str
    disposition: PolicyDisposition
    findings: tuple[PolicyFinding, ...] = ()

    @property
    def executable(self) -> bool:
        return self.disposition == "allow"

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "kind": self.kind,
            "disposition": self.disposition,
            "executable": self.executable,
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True, slots=True)
class PlanningPolicyContext:
    simulation_budget_remaining: int
    enabled_capabilities: frozenset[str] = frozenset()
    unresolved_side_effects: frozenset[str] = frozenset()
    known_mechanisms: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if (
            isinstance(self.simulation_budget_remaining, bool)
            or not isinstance(self.simulation_budget_remaining, int)
            or self.simulation_budget_remaining < 0
        ):
            raise ValueError("simulation_budget_remaining must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class PlanningPolicyDecision:
    proposal: PlanProposal
    research_findings: tuple[PolicyFinding, ...]
    actions: tuple[ActionPolicyDecision, ...]

    @property
    def denied_action_count(self) -> int:
        return sum(action.disposition == "deny" for action in self.actions)

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.proposal.plan_id,
            "research_findings": [finding.to_dict() for finding in self.research_findings],
            "actions": [action.to_dict() for action in self.actions],
        }


def evaluate_plan_proposal(
    proposal: PlanProposal,
    context: PlanningPolicyContext,
) -> PlanningPolicyDecision:
    """Classify research guidance separately from execution-safety controls.

    The original proposal is returned unchanged. Novel content creates soft findings;
    only deterministic budget, capability, and unresolved-side-effect conditions can
    deny or defer a known side-effect action.
    """

    research_findings: list[PolicyFinding] = []
    for hypothesis in proposal.hypotheses:
        subject_ref = hypothesis.hypothesis_id
        if hypothesis.mechanism and hypothesis.mechanism not in context.known_mechanisms:
            research_findings.append(
                PolicyFinding(
                    "research.novel_mechanism",
                    "soft",
                    "The mechanism is novel and should use the exploration lane until evidence accumulates.",
                    subject_ref,
                )
            )
        if not hypothesis.kill_conditions:
            research_findings.append(
                PolicyFinding(
                    "research.missing_kill_conditions",
                    "soft",
                    "Ask the planner to add kill conditions before consuming simulation budget.",
                    subject_ref,
                )
            )
        if hypothesis.extensions.get("proposed_proxy_fields"):
            research_findings.append(
                PolicyFinding(
                    "research.proposed_proxy_fields",
                    "soft",
                    "Proposed proxy fields require preflight evidence but remain part of the proposal.",
                    subject_ref,
                )
            )

    actions = tuple(_evaluate_action(action, proposal, context) for action in proposal.requested_actions)
    return PlanningPolicyDecision(proposal, tuple(research_findings), actions)


def _evaluate_action(
    action: RequestedAction,
    proposal: PlanProposal,
    context: PlanningPolicyContext,
) -> ActionPolicyDecision:
    normalized_kind = action.kind.strip().lower()
    operation = _SIDE_EFFECT_ACTIONS.get(normalized_kind)
    if operation is None:
        if normalized_kind in _READ_ONLY_ACTIONS or normalized_kind.startswith(("inspect_", "query_", "retrieve_")):
            return ActionPolicyDecision(action.action_id, action.kind, "allow")
        finding = PolicyFinding(
            "research.unknown_action_kind",
            "soft",
            "No executor handles this action yet; retain it for exploration or review.",
            action.action_id,
        )
        return ActionPolicyDecision(action.action_id, action.kind, "explore", (finding,))

    if operation in context.unresolved_side_effects:
        finding = PolicyFinding(
            "execution.unresolved_side_effect",
            "hard",
            f"A previous {operation} outcome must be reconciled before another action executes.",
            action.action_id,
        )
        return ActionPolicyDecision(action.action_id, action.kind, "defer", (finding,))

    if operation not in context.enabled_capabilities:
        finding = PolicyFinding(
            "execution.capability_disabled",
            "hard",
            f"The runtime has not explicitly enabled the {operation} capability.",
            action.action_id,
        )
        return ActionPolicyDecision(action.action_id, action.kind, "deny", (finding,))

    if operation == "simulation":
        requested_budget = _requested_budget(action, proposal)
        if requested_budget > context.simulation_budget_remaining:
            finding = PolicyFinding(
                "execution.simulation_budget_exceeded",
                "hard",
                (
                    f"The action requests {requested_budget} simulations but only "
                    f"{context.simulation_budget_remaining} remain."
                ),
                action.action_id,
            )
            return ActionPolicyDecision(action.action_id, action.kind, "deny", (finding,))

    return ActionPolicyDecision(action.action_id, action.kind, "allow")


def _requested_budget(action: RequestedAction, proposal: PlanProposal) -> int:
    for hypothesis in proposal.hypotheses:
        if hypothesis.hypothesis_id == action.candidate_ref:
            return hypothesis.requested_budget
    value = action.parameters.get("requested_budget", 0)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
