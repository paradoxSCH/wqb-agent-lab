from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def normalize_policy_feedback(report: Mapping[str, Any] | None) -> dict[str, Any]:
    actions = {}
    actions_by_key = {}
    if isinstance(report, Mapping):
        for action in report.get("budget_policy_actions") or []:
            if not isinstance(action, Mapping):
                continue
            diagnosis_type = str(action.get("diagnosis_type") or "")
            if diagnosis_type:
                bucket = str(action.get("bucket") or "")
                policy_key = str(action.get("policy_key") or (f"{diagnosis_type}:{bucket}" if bucket else diagnosis_type))
                normalized_action = {
                    "diagnosis_type": diagnosis_type,
                    "bucket": bucket,
                    "policy_key": policy_key,
                    "budget_action": str(action.get("budget_action") or "quarantine_unknown_diagnosis"),
                    "max_budget_share": _number_or_none(action.get("max_budget_share")),
                    "policy_confidence": str(action.get("policy_confidence") or ""),
                }
                actions_by_key[policy_key] = normalized_action
                actions.setdefault(diagnosis_type, normalized_action)
    return {
        "source": "output_evaluation_report" if actions_by_key else "none",
        "static_preflight_required": "field_type_operator_mismatch" in actions,
        "budget_actions": actions,
        "budget_actions_by_key": actions_by_key,
    }


def apply_policy_feedback(
    queue: Mapping[str, Any],
    field_map: Mapping[str, Any],
    policy_feedback: Mapping[str, Any] | None,
    *,
    mode: str = "shadow",
) -> dict[str, Any]:
    normalized_mode = str(mode or "shadow").strip().lower()
    if normalized_mode not in {"off", "shadow", "advisory", "control"}:
        normalized_mode = "shadow"
    feedback = normalize_policy_feedback(policy_feedback)
    feedback["mode"] = normalized_mode
    result = deepcopy(dict(queue))
    result["policy_feedback"] = feedback
    if normalized_mode == "off":
        return result
    proxy_strength_by_mechanism = {
        str(row.get("mechanism")): str(row.get("proxy_strength") or "unknown")
        for row in field_map.get("mappings", [])
        if isinstance(row, Mapping)
    }
    hypotheses = []
    for item in result.get("hypotheses") or []:
        if not isinstance(item, Mapping):
            continue
        hypothesis = deepcopy(dict(item))
        mechanism = str(hypothesis.get("mechanism") or "")
        proxy_strength = proxy_strength_by_mechanism.get(mechanism, "unknown")
        item_feedback = {
            "source": feedback["source"],
            "proxy_strength": proxy_strength,
            "static_preflight_required": bool(feedback["static_preflight_required"]),
            "requires_chassis_change": False,
            "required_experiments": [],
            "max_budget_share": None,
            "budget_actions": {},
            "recommended_action_lane": str(hypothesis.get("wqb_action_lane") or "probe"),
        }
        _apply_weak_proxy_feedback(item_feedback, feedback)
        _apply_overcrowded_feedback(item_feedback, feedback)
        _apply_sub_universe_feedback(item_feedback, feedback)
        _apply_lane_from_actions(item_feedback)
        if normalized_mode == "control":
            hypothesis["wqb_action_lane"] = item_feedback["recommended_action_lane"]
        hypothesis["policy_feedback"] = item_feedback
        hypotheses.append(hypothesis)
    result["hypotheses"] = hypotheses
    return result


def _apply_weak_proxy_feedback(item_feedback: dict[str, Any], feedback: Mapping[str, Any]) -> None:
    action = _first_action(feedback, ["weak_behavior_proxy:deep_fail", "weak_behavior_proxy:near_pass", "weak_behavior_proxy"])
    if not action or item_feedback["proxy_strength"] != "weak":
        return
    item_feedback["max_budget_share"] = action.get("max_budget_share")
    item_feedback["budget_actions"][str(action.get("policy_key") or "weak_behavior_proxy")] = action
    if str(action.get("budget_action") or "") == "replace_proxy_before_resimulation":
        item_feedback["requires_chassis_change"] = True


def _apply_overcrowded_feedback(
    item_feedback: dict[str, Any],
    feedback: Mapping[str, Any],
) -> None:
    action = _first_action(feedback, ["overcrowded_skeleton"])
    if not action:
        return
    item_feedback["requires_chassis_change"] = True
    item_feedback["budget_actions"][str(action.get("policy_key") or "overcrowded_skeleton")] = action
    item_feedback["recommended_action_lane"] = "repair_probe"


def _apply_sub_universe_feedback(
    item_feedback: dict[str, Any],
    feedback: Mapping[str, Any],
) -> None:
    action = _first_action(feedback, ["sub_universe_instability:severe", "sub_universe_instability"])
    if not action:
        return
    if str(action.get("budget_action") or "") == "replace_unstable_universe_proxy":
        item_feedback["requires_chassis_change"] = True
        item_feedback["recommended_action_lane"] = "replace_probe"
    else:
        item_feedback["required_experiments"] = ["industry_neutralization", "sector_neutralization"]
    item_feedback["budget_actions"][str(action.get("policy_key") or "sub_universe_instability")] = action


def _apply_lane_from_actions(item_feedback: dict[str, Any]) -> None:
    actions = item_feedback.get("budget_actions") or {}
    budget_actions = {str(action.get("budget_action") or "") for action in actions.values() if isinstance(action, Mapping)}
    replacement_actions = {
        "replace_proxy_before_resimulation",
        "replace_unstable_universe_proxy",
        "replace_concentrated_expression_structure",
    }
    if budget_actions & replacement_actions:
        item_feedback["recommended_action_lane"] = "replace_probe"
    elif "allocate_small_parameter_repair" in budget_actions:
        item_feedback["recommended_action_lane"] = "repair_probe"


def _first_action(feedback: Mapping[str, Any], keys: list[str]) -> Mapping[str, Any] | None:
    actions_by_key = feedback.get("budget_actions_by_key") or {}
    for key in keys:
        action = actions_by_key.get(key)
        if isinstance(action, Mapping):
            return action
    actions = feedback.get("budget_actions") or {}
    for key in keys:
        action = actions.get(key.split(":", 1)[0])
        if isinstance(action, Mapping):
            return action
    return None


def _number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
