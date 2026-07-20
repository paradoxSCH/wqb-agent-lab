from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Mapping, Sequence


POLICY_BY_DIAGNOSIS: dict[str, dict[str, Any]] = {
    "weak_behavior_proxy": {
        "recommended_policy": "replace_proxy_or_downweight_family",
        "budget_policy": "no_scale_until_proxy_replaced",
        "next_action": "replace primary proxy, behavior thesis, or expression chassis before another broad sweep",
        "success_metric": "replacement_submit_ready_rate",
        "failure_metric": "repeat_low_fitness_low_sharpe_rate",
    },
    "overcrowded_skeleton": {
        "recommended_policy": "controlled_structural_repair",
        "budget_policy": "small_repair_budget_with_chassis_change",
        "next_action": "generate self-corr escape variants with different operator chassis or primary proxy",
        "success_metric": "self_corr_escape_to_submit_ready_rate",
        "failure_metric": "repeat_self_corr_fail_rate",
    },
    "sub_universe_instability": {
        "recommended_policy": "grouping_or_neutralization_experiment",
        "budget_policy": "controlled_grouping_probe",
        "next_action": "test industry or sector neutralization before scaling the family",
        "success_metric": "sub_universe_pass_after_group_shift_rate",
        "failure_metric": "repeat_sub_universe_fail_rate",
    },
    "field_type_operator_mismatch": {
        "recommended_policy": "static_preflight_block",
        "budget_policy": "zero_simulation_until_guarded",
        "next_action": "add field-type/operator compatibility guard before simulation",
        "success_metric": "prevented_invalid_simulation_count",
        "failure_metric": "event_operator_error_recurrence",
    },
    "turnover_instability": {
        "recommended_policy": "smooth_or_decay_signal",
        "budget_policy": "small_parameter_repair",
        "next_action": "increase decay, lengthen lookback, or reduce short-horizon reversal legs",
        "success_metric": "turnover_pass_after_smoothing_rate",
        "failure_metric": "repeat_high_turnover_rate",
    },
    "unit_normalization_mismatch": {
        "recommended_policy": "unit_safe_expression_rewrite",
        "budget_policy": "preflight_rewrite_before_resimulation",
        "next_action": "rank or zscore each heterogeneous term before addition or subtraction",
        "success_metric": "unit_warning_elimination_rate",
        "failure_metric": "unit_warning_recurrence",
    },
    "weight_concentration": {
        "recommended_policy": "weight_distribution_repair",
        "budget_policy": "small_smoothing_or_structure_repair",
        "next_action": "smooth sparse drivers, adjust truncation, or replace concentrated expression structure",
        "success_metric": "concentrated_weight_pass_rate",
        "failure_metric": "repeat_concentrated_weight_rate",
    },
}


def evaluate_diagnosis_policies(
    rows: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    stats: dict[str, dict[str, Any]] = defaultdict(_empty_stats)
    total_diagnoses = 0
    for row in rows:
        diagnoses = row.get("failure_diagnoses") or []
        if not isinstance(diagnoses, Sequence) or isinstance(diagnoses, (str, bytes)):
            continue
        for diagnosis in diagnoses:
            if not isinstance(diagnosis, Mapping):
                continue
            diagnosis_type = str(diagnosis.get("diagnosis_type") or "unknown")
            bucket = _diagnosis_bucket(diagnosis)
            policy_key = f"{diagnosis_type}:{bucket}" if bucket else diagnosis_type
            item = stats[policy_key]
            item["diagnosis_type"] = diagnosis_type
            item["bucket"] = bucket
            item["policy_key"] = policy_key
            item["observed_count"] += 1
            item["affected_alpha_ids"].add(str(row.get("alpha_id") or ""))
            item["families"].add(str(row.get("family") or diagnosis.get("family") or "unknown"))
            item["skeletons"].add(str(row.get("skeleton") or diagnosis.get("skeleton") or "unknown"))
            item["severity_counts"][str(diagnosis.get("severity") or "unknown")] += 1
            _count_bucket(item, row)
            _track_quality(item, row)
            if row.get("error"):
                item["terminal_error_count"] += 1
            total_diagnoses += 1

    policies = [_finalize_policy(item) for item in stats.values()]
    policies.sort(key=lambda item: (-int(item["observed_count"]), str(item["diagnosis_type"])))
    return {
        "generated_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "total_rows": len(rows),
        "total_diagnoses": total_diagnoses,
        "policy_count": len(policies),
        "budget_saved_estimate": sum(int(item["budget_saved_estimate"]) for item in policies),
        "policies": policies,
    }


def _empty_stats() -> dict[str, Any]:
    return {
        "diagnosis_type": "unknown",
        "bucket": "",
        "policy_key": "unknown",
        "observed_count": 0,
        "affected_alpha_ids": set(),
        "families": set(),
        "skeletons": set(),
        "severity_counts": Counter(),
        "bucket_counts": Counter(),
        "repair_count": 0,
        "blocked_count": 0,
        "direct_submit_count": 0,
        "submit_ready_count": 0,
        "terminal_error_count": 0,
        "sharpe_sum": 0.0,
        "fitness_sum": 0.0,
        "metric_count": 0,
    }


def _count_bucket(item: dict[str, Any], row: Mapping[str, Any]) -> None:
    bucket = str(row.get("triage_bucket") or row.get("recommended_action") or "unknown")
    item["bucket_counts"][bucket] += 1
    if bucket == "optimize_next" or bucket == "live_recheck_then_submit":
        item["repair_count"] += 1
    if bucket in {"low_value", "blocked", "avoid_unchanged"}:
        item["blocked_count"] += 1
    if bucket == "direct_submit":
        item["direct_submit_count"] += 1
    if bucket in {"submit_ready", "submit", "live_recheck_then_submit"}:
        item["submit_ready_count"] += 1


def _track_quality(item: dict[str, Any], row: Mapping[str, Any]) -> None:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    if not metrics:
        return
    item["sharpe_sum"] += _number(metrics.get("sharpe"))
    item["fitness_sum"] += _number(metrics.get("fitness"))
    item["metric_count"] += 1


def _finalize_policy(item: dict[str, Any]) -> dict[str, Any]:
    observed = max(int(item["observed_count"]), 1)
    diagnosis_type = str(item["diagnosis_type"])
    bucket = str(item.get("bucket") or "")
    template = _policy_template(diagnosis_type, bucket)
    budget_saved = _budget_saved_estimate(diagnosis_type, item)
    metric_count = max(int(item["metric_count"]), 1)
    return {
        "diagnosis_type": diagnosis_type,
        "bucket": bucket,
        "policy_key": str(item.get("policy_key") or diagnosis_type),
        "recommended_policy": template["recommended_policy"],
        "budget_policy": template["budget_policy"],
        "next_action": template["next_action"],
        "success_metric": template["success_metric"],
        "failure_metric": template["failure_metric"],
        "observed_count": int(item["observed_count"]),
        "affected_candidate_count": len({alpha_id for alpha_id in item["affected_alpha_ids"] if alpha_id}),
        "family_count": len({family for family in item["families"] if family and family != "unknown"}),
        "skeleton_count": len({skeleton for skeleton in item["skeletons"] if skeleton and skeleton != "unknown"}),
        "severity_counts": dict(item["severity_counts"]),
        "bucket_counts": dict(item["bucket_counts"]),
        "repair_count": int(item["repair_count"]),
        "blocked_count": int(item["blocked_count"]),
        "direct_submit_count": int(item["direct_submit_count"]),
        "submit_ready_count": int(item["submit_ready_count"]),
        "terminal_error_count": int(item["terminal_error_count"]),
        "repair_candidate_rate": round(int(item["repair_count"]) / observed, 4),
        "blocked_rate": round(int(item["blocked_count"]) / observed, 4),
        "direct_submit_rate": round(int(item["direct_submit_count"]) / observed, 4),
        "avg_sharpe": round(float(item["sharpe_sum"]) / metric_count, 4),
        "avg_fitness": round(float(item["fitness_sum"]) / metric_count, 4),
        "budget_saved_estimate": budget_saved,
        "policy_confidence": _policy_confidence(item),
    }


def _budget_saved_estimate(diagnosis_type: str, item: Mapping[str, Any]) -> int:
    observed = int(item.get("observed_count") or 0)
    if diagnosis_type == "field_type_operator_mismatch":
        return observed
    if diagnosis_type == "unit_normalization_mismatch":
        return max(0, observed // 2)
    if diagnosis_type == "weak_behavior_proxy":
        return max(0, int(item.get("blocked_count") or 0) // 2)
    return 0


def _diagnosis_bucket(diagnosis: Mapping[str, Any]) -> str:
    evidence = diagnosis.get("evidence") if isinstance(diagnosis.get("evidence"), Mapping) else {}
    for key in (
        "self_corr_bucket",
        "sub_universe_bucket",
        "weak_signal_bucket",
        "weight_concentration_bucket",
        "turnover_bucket",
    ):
        value = str(evidence.get(key) or "").strip()
        if value:
            return value
    return ""


def _policy_template(diagnosis_type: str, bucket: str) -> dict[str, str]:
    if diagnosis_type == "weak_behavior_proxy" and bucket == "near_pass":
        return {
            "recommended_policy": "small_parameter_repair",
            "budget_policy": "small_repair_budget",
            "next_action": "try limited decay, neutralization, or weighting repair before changing thesis",
            "success_metric": "near_pass_repair_to_submit_ready_rate",
            "failure_metric": "near_pass_repair_failure_rate",
        }
    if diagnosis_type == "weak_behavior_proxy" and bucket == "deep_fail":
        return {
            "recommended_policy": "replace_behavior_proxy",
            "budget_policy": "zero_scale_until_proxy_replaced",
            "next_action": "replace primary proxy or behavior thesis before another simulation batch",
            "success_metric": "replacement_submit_ready_rate",
            "failure_metric": "repeat_deep_weak_signal_rate",
        }
    if diagnosis_type == "sub_universe_instability" and bucket == "severe":
        return {
            "recommended_policy": "replace_unstable_universe_proxy",
            "budget_policy": "block_scale_until_proxy_replaced",
            "next_action": "replace unstable universe proxy or behavior thesis; do not spend on neutralization-only repair",
            "success_metric": "replacement_sub_universe_pass_rate",
            "failure_metric": "repeat_severe_sub_universe_fail_rate",
        }
    if diagnosis_type == "sub_universe_instability" and bucket == "mild":
        return {
            "recommended_policy": "light_grouping_repair",
            "budget_policy": "small_grouping_probe",
            "next_action": "try industry/sector neutralization or group shift with tight budget",
            "success_metric": "mild_sub_universe_repair_pass_rate",
            "failure_metric": "repeat_sub_universe_fail_rate",
        }
    if diagnosis_type == "weight_concentration" and bucket == "severe":
        return {
            "recommended_policy": "replace_concentrated_structure",
            "budget_policy": "block_until_structure_replaced",
            "next_action": "replace sparse driver or expression structure before resimulation",
            "success_metric": "structure_replacement_weight_pass_rate",
            "failure_metric": "repeat_severe_weight_concentration_rate",
        }
    template = POLICY_BY_DIAGNOSIS.get(diagnosis_type)
    if template:
        return template
    return {
        "recommended_policy": "quarantine_unknown_diagnosis",
        "budget_policy": "no_policy_until_classified",
        "next_action": "quarantine examples and add a diagnosis policy",
        "success_metric": "classification_resolution_rate",
        "failure_metric": "repeat_unknown_diagnosis_rate",
    }


def _policy_confidence(item: Mapping[str, Any]) -> str:
    observed = int(item.get("observed_count") or 0)
    family_count = len(item.get("families") or [])
    if observed >= 40 and family_count >= 3:
        return "high"
    if observed >= 10:
        return "medium"
    return "low"


def _number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
