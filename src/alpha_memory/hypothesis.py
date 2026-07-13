from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class HypothesisDraft:
    behavior_thesis: str
    mechanism: str
    proxies: list[str]
    operator_skeletons: list[str]
    kill_conditions: list[str]
    success_criteria: list[str]


@dataclass(frozen=True)
class HypothesisValidation:
    ok: bool
    missing_fields: list[str]


def validate_hypothesis(draft: HypothesisDraft) -> HypothesisValidation:
    missing_fields: list[str] = []

    if not draft.behavior_thesis.strip():
        missing_fields.append("behavior_thesis")
    if not draft.mechanism.strip():
        missing_fields.append("mechanism")
    if not draft.proxies:
        missing_fields.append("proxies")
    if not draft.operator_skeletons:
        missing_fields.append("operator_skeletons")
    if not draft.kill_conditions:
        missing_fields.append("kill_conditions")
    if not draft.success_criteria:
        missing_fields.append("success_criteria")

    return HypothesisValidation(ok=not missing_fields, missing_fields=missing_fields)


def _coerce_self_corr(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def classify_wqb_action_lane(metrics: Mapping[str, Any]) -> str:
    if metrics.get("duplicate") or metrics.get("blocked"):
        return "block"
    if metrics.get("submit_ready"):
        return "submit"
    if metrics.get("near_pass"):
        return "repair"
    self_corr = _coerce_self_corr(metrics.get("self_corr"))
    if metrics.get("pass") and self_corr is not None and self_corr < 0.35:
        return "scale"
    if metrics.get("new_thesis"):
        return "probe"
    return "holdout"
