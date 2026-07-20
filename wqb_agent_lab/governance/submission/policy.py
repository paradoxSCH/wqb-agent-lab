from __future__ import annotations

from datetime import datetime

from .models import PolicyEvaluation, SubmitDecision


class SubmissionPolicyEvaluator:
    def evaluate(self, decision: SubmitDecision) -> PolicyEvaluation:
        reasons: list[str] = []
        if not decision.decision_id.strip():
            reasons.append("decision_id_required")
        if not decision.alpha_id.strip():
            reasons.append("alpha_id_required")
        if decision.requested_mode not in {"queue_only", "execute_live"}:
            reasons.append("unsupported_requested_mode")
        if not decision.agent_id.strip():
            reasons.append("agent_id_required")
        if not decision.rationale.strip():
            reasons.append("rationale_required")

        allowed = not reasons
        return PolicyEvaluation(
            evaluation_id=f"eval-{decision.decision_id or datetime.now().strftime('%Y%m%d%H%M%S')}",
            decision_id=decision.decision_id,
            allowed=allowed,
            policy_action="allow" if allowed else "block",
            reasons=reasons,
        )
