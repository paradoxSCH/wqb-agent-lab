from __future__ import annotations

from typing import Any, Mapping, Sequence

from src.self_corr_policy import self_corr_bucket as _policy_self_corr_bucket


def diagnose_failure_objects(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks = [check for check in row.get("checks") or [] if isinstance(check, Mapping)]
    failed = {str(check.get("name") or "UNKNOWN").upper() for check in checks if str(check.get("result") or "").upper() in {"FAIL", "ERROR"}}
    pending = {str(check.get("name") or "UNKNOWN").upper() for check in checks if str(check.get("result") or "").upper() == "PENDING"}
    warnings = {str(check.get("name") or "UNKNOWN").upper() for check in checks if str(check.get("result") or "").upper() == "WARNING"}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    diagnoses: list[dict[str, Any]] = []

    error = str(row.get("error") or "")
    if error or not str(row.get("alpha_id") or "").strip():
        if _looks_like_event_operator_error(error):
            diagnoses.append(
                _diagnosis(
                    row,
                    diagnosis_type="field_type_operator_mismatch",
                    severity="high",
                    check_names=["TERMINAL_ERROR"],
                    evidence={
                        "error": error,
                        "operator_family": "time_series_or_arithmetic_on_event_field",
                    },
                    recommended_action="add_static_field_operator_guard",
                    generation_feedback=[
                        "block_event_field_time_series_operator_pair",
                        "require_field_type_compatible_operator",
                        "prefer_cross_sectional_or_event_supported_transform",
                    ],
                )
            )
        elif error:
            diagnoses.append(
                _diagnosis(
                    row,
                    diagnosis_type="invalid_simulation_request",
                    severity="high",
                    check_names=["TERMINAL_ERROR"],
                    evidence={"error": error},
                    recommended_action="fix_expression_before_resimulation",
                    generation_feedback=["do_not_resubmit_unchanged", "add_preflight_static_validation"],
                )
            )

    if "SELF_CORRELATION" in failed or "SELF_CORRELATION" in pending:
        self_corr = _check_value(checks, "SELF_CORRELATION")
        self_corr_bucket = _self_corr_bucket(self_corr)
        if self_corr_bucket == "extreme":
            severity = "high"
            recommended_action = "replace_overcrowded_signal"
            generation_feedback = [
                "replace_primary_field_or_behavior_proxy",
                "change_behavior_thesis_not_parameters",
                "block_window_decay_only_mutation",
                "start_new_chassis_family",
            ]
        elif self_corr_bucket == "moderate":
            severity = "medium"
            recommended_action = "structural_self_corr_escape"
            generation_feedback = [
                "change_operator_chassis",
                "change_primary_field_or_behavior_proxy",
                "avoid_window_decay_only_mutation",
            ]
        elif self_corr_bucket == "mild":
            severity = "medium"
            recommended_action = "light_self_corr_repair"
            generation_feedback = [
                "try_group_or_neutralization_shift",
                "weaken_shared_reversal_leg",
                "limit_to_small_repair_budget",
            ]
        else:
            severity = "medium"
            recommended_action = "self_corr_live_recheck_or_structural_escape"
            generation_feedback = [
                "fetch_self_corr_value_before_spending_repair_budget",
                "avoid_window_decay_only_mutation",
            ]
        diagnoses.append(
            _diagnosis(
                row,
                diagnosis_type="overcrowded_skeleton",
                severity=severity,
                check_names=sorted({"SELF_CORRELATION"} & (failed | pending)),
                evidence={
                    "self_corr": self_corr,
                    "self_corr_bucket": self_corr_bucket,
                    "sharpe": _number(metrics.get("sharpe")),
                    "fitness": _number(metrics.get("fitness")),
                },
                recommended_action=recommended_action,
                generation_feedback=generation_feedback,
            )
        )

    if "LOW_SUB_UNIVERSE_SHARPE" in failed:
        sub_value = _check_value(checks, "LOW_SUB_UNIVERSE_SHARPE")
        sub_limit = _check_limit(checks, "LOW_SUB_UNIVERSE_SHARPE")
        sub_bucket = _sub_universe_bucket(sub_value, sub_limit)
        if sub_bucket == "severe":
            sub_severity = "high"
            sub_action = "replace_unstable_universe_proxy"
            sub_feedback = [
                "do_not_scale_across_universes",
                "replace_primary_proxy_or_behavior_thesis",
                "avoid_neutralization_only_repair",
            ]
        elif sub_bucket == "moderate":
            sub_severity = "medium"
            sub_action = "controlled_sub_universe_repair"
            sub_feedback = [
                "test_grouping_shift_before_scaling",
                "try_sector_or_industry_neutralization",
                "limit_to_small_repair_budget",
            ]
        else:
            sub_severity = "medium"
            sub_action = "neutralization_or_grouping_shift"
            sub_feedback = [
                "prefer_industry_over_subindustry_when_unstable",
                "test_grouping_shift_before_scaling",
                "avoid_family_scale_until_sub_universe_passes",
            ]
        diagnoses.append(
            _diagnosis(
                row,
                diagnosis_type="sub_universe_instability",
                severity=sub_severity,
                check_names=["LOW_SUB_UNIVERSE_SHARPE"],
                evidence={
                    "sub_universe_sharpe": sub_value,
                    "sub_universe_limit": sub_limit,
                    "sub_universe_bucket": sub_bucket,
                    "neutralization": (row.get("settings") or {}).get("neutralization") if isinstance(row.get("settings"), Mapping) else None,
                },
                recommended_action=sub_action,
                generation_feedback=sub_feedback,
            )
        )

    if "HIGH_TURNOVER" in failed:
        diagnoses.append(
            _diagnosis(
                row,
                diagnosis_type="turnover_instability",
                severity="medium",
                check_names=["HIGH_TURNOVER"],
                evidence={"turnover": _number(metrics.get("turnover"))},
                recommended_action="increase_decay_or_smooth_signal",
                generation_feedback=["increase_decay", "use_longer_lookback", "reduce_short_horizon_reversal_leg"],
            )
        )

    weak_checks = sorted({"LOW_SHARPE", "LOW_FITNESS"} & failed)
    if weak_checks:
        weak_bucket = _weak_signal_bucket(metrics, checks)
        severity = "medium" if weak_bucket == "near_pass" else "high" if weak_bucket == "deep_fail" else "medium"
        if weak_bucket != "near_pass" and "LOW_SHARPE" in failed and _number(metrics.get("sharpe")) < 1.10:
            severity = "high"
        if weak_bucket == "deep_fail":
            weak_action = "replace_proxy_or_behavior_thesis"
            weak_feedback = ["do_not_scale", "replace_primary_proxy_or_mechanism", "avoid_parameter_only_sweep"]
        elif weak_bucket == "near_pass":
            weak_action = "local_parameter_or_weight_repair"
            weak_feedback = ["allow_small_parameter_probe", "adjust_decay_or_neutralization", "do_not_change_behavior_thesis_first"]
        else:
            weak_action = "rewrite_signal_chassis"
            weak_feedback = ["change_operator_chassis", "replace_primary_proxy_or_mechanism", "limit_repair_budget"]
        diagnoses.append(
            _diagnosis(
                row,
                diagnosis_type="weak_behavior_proxy",
                severity=severity,
                check_names=weak_checks,
                evidence={
                    "sharpe": _number(metrics.get("sharpe")),
                    "fitness": _number(metrics.get("fitness")),
                    "sharpe_limit": _check_limit(checks, "LOW_SHARPE"),
                    "fitness_limit": _check_limit(checks, "LOW_FITNESS"),
                    "weak_signal_bucket": weak_bucket,
                    "returns": _number(metrics.get("returns")),
                    "drawdown": _number(metrics.get("drawdown")),
                },
                recommended_action=weak_action,
                generation_feedback=weak_feedback,
            )
        )

    if "CONCENTRATED_WEIGHT" in failed or "CONCENTRATED_WEIGHT" in warnings:
        concentration_value = _check_value(checks, "CONCENTRATED_WEIGHT")
        concentration_limit = _check_limit(checks, "CONCENTRATED_WEIGHT")
        concentration_bucket = _weight_concentration_bucket(concentration_value, concentration_limit)
        if concentration_bucket == "severe":
            concentration_severity = "high"
            concentration_action = "replace_concentrated_expression_structure"
            concentration_feedback = ["replace_sparse_tail_driver", "add_cross_sectional_smoothing", "avoid_truncation_only_repair"]
        elif concentration_bucket == "moderate":
            concentration_severity = "medium"
            concentration_action = "smooth_or_truncate_weight_concentration"
            concentration_feedback = ["increase_truncation", "group_neutralize_or_rank_terms", "smooth_sparse_leg"]
        else:
            concentration_severity = "medium"
            concentration_action = "light_weight_smoothing"
            concentration_feedback = ["small_truncation_probe", "rank_sparse_component"]
        diagnoses.append(
            _diagnosis(
                row,
                diagnosis_type="weight_concentration",
                severity=concentration_severity,
                check_names=["CONCENTRATED_WEIGHT"],
                evidence={
                    "concentrated_weight": concentration_value,
                    "concentrated_weight_limit": concentration_limit,
                    "weight_concentration_bucket": concentration_bucket,
                },
                recommended_action=concentration_action,
                generation_feedback=concentration_feedback,
            )
        )

    if "UNITS" in warnings:
        diagnoses.append(
            _diagnosis(
                row,
                diagnosis_type="unit_normalization_mismatch",
                severity="medium",
                check_names=["UNITS"],
                evidence={"warnings": sorted(warnings)},
                recommended_action="normalize_terms_before_combining",
                generation_feedback=["rank_or_zscore_each_term", "avoid_raw_addition_of_heterogeneous_units"],
            )
        )

    return _dedupe_diagnoses(diagnoses)


def primary_diagnosis_type(row: Mapping[str, Any]) -> str:
    diagnoses = row.get("failure_diagnoses")
    if not isinstance(diagnoses, Sequence) or isinstance(diagnoses, (str, bytes)):
        diagnoses = diagnose_failure_objects(row)
    if not diagnoses:
        return "none"
    return str(diagnoses[0].get("diagnosis_type") or "unknown")


def _diagnosis(
    row: Mapping[str, Any],
    *,
    diagnosis_type: str,
    severity: str,
    check_names: list[str],
    evidence: Mapping[str, Any],
    recommended_action: str,
    generation_feedback: list[str],
) -> dict[str, Any]:
    return {
        "diagnosis_type": diagnosis_type,
        "severity": severity,
        "check_names": check_names,
        "family": row.get("family") or row.get("behavior_family") or row.get("route_family") or "unknown",
        "skeleton": row.get("skeleton") or row.get("chassis") or "unknown",
        "evidence": dict(evidence),
        "recommended_action": recommended_action,
        "generation_feedback": generation_feedback,
    }


def _dedupe_diagnoses(diagnoses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    best_by_type: dict[str, dict[str, Any]] = {}
    for diagnosis in diagnoses:
        key = str(diagnosis.get("diagnosis_type") or "unknown")
        existing = best_by_type.get(key)
        if existing is None or severity_rank.get(str(diagnosis.get("severity")), 9) < severity_rank.get(str(existing.get("severity")), 9):
            best_by_type[key] = diagnosis
    return sorted(best_by_type.values(), key=lambda item: (severity_rank.get(str(item.get("severity")), 9), str(item.get("diagnosis_type"))))


def _looks_like_event_operator_error(error: str) -> bool:
    lowered = error.lower()
    return "event inputs" in lowered and ("operator" in lowered or "does not support" in lowered)


def _check_value(checks: Sequence[Mapping[str, Any]], name: str) -> Any:
    for check in checks:
        if str(check.get("name") or "").upper() == name.upper():
            return check.get("value")
    return None


def _check_limit(checks: Sequence[Mapping[str, Any]], name: str) -> Any:
    for check in checks:
        if str(check.get("name") or "").upper() == name.upper():
            return check.get("limit")
    return None


def _self_corr_bucket(value: Any) -> str:
    return _policy_self_corr_bucket(value)


def _sub_universe_bucket(value: Any, limit: Any) -> str:
    numeric = _number_or_none(value)
    threshold = _number_or_none(limit)
    if numeric is None:
        return "unknown"
    if threshold is None or threshold == 0:
        threshold = 0.70
    gap = threshold - numeric
    if gap >= 0.35:
        return "severe"
    if gap >= 0.10:
        return "moderate"
    return "mild"


def _weak_signal_bucket(metrics: Mapping[str, Any], checks: Sequence[Mapping[str, Any]]) -> str:
    sharpe = _number(metrics.get("sharpe"))
    fitness = _number(metrics.get("fitness"))
    sharpe_limit = _number_or_none(_check_limit(checks, "LOW_SHARPE")) or 1.25
    fitness_limit = _number_or_none(_check_limit(checks, "LOW_FITNESS")) or 1.00
    sharpe_ratio = sharpe / sharpe_limit if sharpe_limit else 0.0
    fitness_ratio = fitness / fitness_limit if fitness_limit else 0.0
    if sharpe >= 1.10 and fitness >= 0.85:
        return "near_pass"
    if sharpe_ratio < 0.65 or fitness_ratio < 0.50:
        return "deep_fail"
    return "medium_gap"


def _weight_concentration_bucket(value: Any, limit: Any) -> str:
    numeric = _number_or_none(value)
    threshold = _number_or_none(limit)
    if numeric is None:
        return "unknown"
    if threshold is None or threshold <= 0:
        threshold = 0.10
    ratio = numeric / threshold
    if ratio >= 2.0:
        return "severe"
    if ratio >= 1.25:
        return "moderate"
    return "mild"


def _number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
