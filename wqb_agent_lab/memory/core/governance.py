from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class GovernanceDecision:
    action: str
    reason: str
    decay_score: float = 0.0

    def to_dict(self) -> dict[str, str | float]:
        return {
            "action": self.action,
            "reason": self.reason,
            "decay_score": self.decay_score,
        }


def _coerce_self_corr(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def decide_memory_governance(metrics: Mapping[str, Any]) -> GovernanceDecision:
    if metrics.get("duplicate"):
        return GovernanceDecision(action="block", reason="duplicate memory skeleton", decay_score=1.0)

    non_actionable_retrievals = int(metrics.get("non_actionable_retrievals", 0) or 0)
    proxy_mapping_count = int(metrics.get("proxy_mapping_count", 0) or 0)
    if non_actionable_retrievals >= 3 and proxy_mapping_count == 0:
        return GovernanceDecision(action="forget", reason="decorative non-actionable memory", decay_score=1.0)

    submit_ready_count = int(metrics.get("submit_ready_count", 0) or 0)
    self_corr = _coerce_self_corr(metrics.get("self_corr"))
    if submit_ready_count > 0 and self_corr is not None and self_corr < 0.35:
        return GovernanceDecision(action="promote", reason="submit-ready low-correlation memory")

    near_pass_count = int(metrics.get("near_pass_count", 0) or 0)
    if near_pass_count >= 2 and self_corr is not None and self_corr < 0.35:
        return GovernanceDecision(action="promote", reason="repeated near-pass low-correlation memory")

    spent_simulations = int(metrics.get("spent_simulations", 0) or 0)
    if spent_simulations >= 500 and near_pass_count == 0 and submit_ready_count == 0:
        return GovernanceDecision(action="decay", reason="simulation budget sink", decay_score=0.75)

    low_fitness_count = int(metrics.get("low_fitness_count", 0) or 0)
    if low_fitness_count >= 10:
        return GovernanceDecision(action="decay", reason="persistent low fitness", decay_score=0.6)

    return GovernanceDecision(action="hold", reason="insufficient governance signal")


def suggest_merge_key(expression_or_title: str) -> str:
    return "".join(expression_or_title.lower().split())
