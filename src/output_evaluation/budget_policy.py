from __future__ import annotations

from typing import Any, Mapping


def build_budget_policy_actions(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    policies = report.get("policies") or []
    actions: list[dict[str, Any]] = []
    for policy in policies:
        if not isinstance(policy, Mapping):
            continue
        diagnosis_type = str(policy.get("diagnosis_type") or "unknown")
        confidence = str(policy.get("policy_confidence") or "low")
        actions.append(_action_for_policy(diagnosis_type, confidence, policy))
    return actions


def _action_for_policy(diagnosis_type: str, confidence: str, policy: Mapping[str, Any]) -> dict[str, Any]:
    bucket = str(policy.get("bucket") or "")
    if diagnosis_type == "field_type_operator_mismatch":
        budget_action = "block_until_preflight_guard"
        max_budget_share = 0.0
    elif diagnosis_type == "weak_behavior_proxy" and bucket == "deep_fail":
        budget_action = "replace_proxy_before_resimulation"
        max_budget_share = 0.0
    elif diagnosis_type == "weak_behavior_proxy" and bucket == "near_pass":
        budget_action = "allocate_small_parameter_repair"
        max_budget_share = 0.08
    elif diagnosis_type == "weak_behavior_proxy":
        budget_action = "downweight_family_or_proxy"
        max_budget_share = 0.05 if confidence == "high" else 0.10
    elif diagnosis_type == "overcrowded_skeleton":
        budget_action = "allocate_controlled_repair_budget"
        max_budget_share = 0.15
    elif diagnosis_type == "sub_universe_instability" and bucket == "severe":
        budget_action = "replace_unstable_universe_proxy"
        max_budget_share = 0.0
    elif diagnosis_type == "sub_universe_instability":
        budget_action = "allocate_grouping_probe_budget"
        max_budget_share = 0.08
    elif diagnosis_type == "weight_concentration" and bucket == "severe":
        budget_action = "replace_concentrated_expression_structure"
        max_budget_share = 0.0
    elif diagnosis_type == "weight_concentration":
        budget_action = "allocate_weight_smoothing_probe"
        max_budget_share = 0.05
    else:
        budget_action = "quarantine_unknown_diagnosis"
        max_budget_share = 0.0

    return {
        "diagnosis_type": diagnosis_type,
        "bucket": bucket,
        "policy_key": str(policy.get("policy_key") or (f"{diagnosis_type}:{bucket}" if bucket else diagnosis_type)),
        "budget_action": budget_action,
        "max_budget_share": max_budget_share,
        "policy_confidence": confidence,
        "observed_count": int(policy.get("observed_count") or 0),
        "success_metric": policy.get("success_metric") or "classification_resolution_rate",
        "failure_metric": policy.get("failure_metric") or "repeat_failure_rate",
    }
