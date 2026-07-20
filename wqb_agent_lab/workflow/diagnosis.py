from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wqb_agent_lab.evaluation.failure_diagnosis import (
    diagnose_failure_objects,
    primary_diagnosis_type,
)

from .artifacts import read_json, relative_path
from .candidates import (
    candidate_field_hint,
    candidate_score,
    check_value,
    failed_check_names,
    failed_checks_from_check_list,
    metric_value,
    normalize_expression,
    pending_check_names,
    row_metric_pass,
    row_near_pass,
    self_corr_bucket_from_checks,
    sub_universe_bucket_from_checks,
    units_warning_from_check_list,
    warning_check_names,
    weak_signal_bucket_from_row,
    weight_concentration_bucket_from_checks,
)


def row_family(row: dict[str, Any]) -> str:
    for key in ("behavior_family", "family", "route_family"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    note = str(row.get("note") or "")
    if ":" in note:
        return note.split(":", 1)[0].strip() or "unknown"
    return "unknown"


def row_skeleton(row: dict[str, Any]) -> str:
    for key in ("skeleton", "chassis"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    family = row_family(row)
    field = candidate_field_hint(str(row.get("expression") or ""))
    return f"{family}:{field}" if field != "unknown" else family


def load_scan_rows(root: Path, paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        payload = read_json(path, [])
        if not isinstance(payload, list):
            continue
        for result in payload:
            if not isinstance(result, dict):
                continue
            row = dict(result)
            row["source_path"] = relative_path(path, root)
            rows.append(row)
    return rows


def diagnose_scan_row(row: dict[str, Any]) -> dict[str, Any]:
    checks = row.get("checks") or []
    enriched = dict(row)
    enriched.update(
        {
            "alpha_id": str(row.get("alpha_id") or "").strip(),
            "expression": normalize_expression(str(row.get("expression") or "")),
            "family": row_family(row),
            "skeleton": row_skeleton(row),
            "failed_checks": failed_check_names(checks),
            "pending_checks": pending_check_names(checks),
            "warning_checks": warning_check_names(checks),
            "units_warning": units_warning_from_check_list(checks),
            "self_corr": check_value(checks, "SELF_CORRELATION"),
            "sub_universe_sharpe": check_value(checks, "LOW_SUB_UNIVERSE_SHARPE"),
            "score": round(candidate_score(row), 4),
        }
    )
    enriched["failure_diagnoses"] = diagnose_failure_objects(enriched)
    return enriched


def route_diagnosed_row(
    diagnosed: dict[str, Any],
    submitted_alpha_ids: set[str],
    submitted_expressions: set[str],
    failed_submit_alpha_ids: set[str] | None = None,
) -> dict[str, Any]:
    checks = diagnosed.get("checks") or []
    expression = str(diagnosed.get("expression") or "")
    alpha_id = str(diagnosed.get("alpha_id") or "").strip()
    failed_submit_alpha_ids = failed_submit_alpha_ids or set()
    already_submitted = bool(
        (alpha_id and alpha_id in submitted_alpha_ids)
        or (expression and expression in submitted_expressions)
    )
    previous_submit_failed = bool(alpha_id and alpha_id in failed_submit_alpha_ids)
    enriched = dict(diagnosed)
    enriched.update(
        {
            "already_submitted": already_submitted,
            "previous_submit_failed": previous_submit_failed,
        }
    )
    failures = set(enriched["failed_checks"])
    self_corr_bucket = self_corr_bucket_from_checks(checks)
    if already_submitted:
        bucket, route = "already_submitted", "skip_already_submitted"
    elif previous_submit_failed:
        bucket, route = "low_value", "skip_previous_submit_unconfirmed"
    elif row_metric_pass(diagnosed) and not failed_checks_from_check_list(checks):
        bucket, route = "direct_submit", "live_recheck_then_submit"
    elif "SELF_CORRELATION" in failures and self_corr_bucket == "extreme":
        bucket, route = "low_value", "replace_overcrowded_signal"
    elif (
        "SELF_CORRELATION" in failures
        and self_corr_bucket == "mild"
        and row_near_pass(diagnosed)
    ):
        bucket, route = "optimize_next", "self_corr_light_repair"
    elif "SELF_CORRELATION" in failures and row_near_pass(diagnosed):
        bucket, route = "low_value", "self_corr_escape"
    elif (
        "CONCENTRATED_WEIGHT" in failures
        and weight_concentration_bucket_from_checks(checks) == "severe"
    ):
        bucket, route = "low_value", "replace_concentrated_expression_structure"
    elif "CONCENTRATED_WEIGHT" in failures:
        bucket = "optimize_next" if row_near_pass(diagnosed) else "low_value"
        route = "smooth_or_truncate_weight_concentration"
    elif (
        "LOW_SUB_UNIVERSE_SHARPE" in failures
        and sub_universe_bucket_from_checks(checks) == "severe"
    ):
        bucket, route = "low_value", "replace_unstable_universe_proxy"
    elif (
        "LOW_SUB_UNIVERSE_SHARPE" in failures
        and sub_universe_bucket_from_checks(checks) == "moderate"
    ):
        bucket = "optimize_next" if row_near_pass(diagnosed) else "low_value"
        route = "controlled_sub_universe_repair"
    elif (
        "LOW_SHARPE" in failures or "LOW_FITNESS" in failures
    ) and weak_signal_bucket_from_row(diagnosed) == "deep_fail":
        bucket, route = "low_value", "replace_weak_behavior_proxy"
    elif (
        "LOW_SHARPE" in failures or "LOW_FITNESS" in failures
    ) and weak_signal_bucket_from_row(diagnosed) == "medium_gap":
        bucket, route = "low_value", "rewrite_weak_signal_chassis"
    elif row_near_pass(diagnosed):
        bucket, route = "optimize_next", "structural_repair_or_parameter_sweep"
    else:
        bucket, route = "low_value", "avoid_unchanged"
    enriched["triage_bucket"] = bucket
    enriched["route_decision"] = route
    return enriched


def classify_scan_row(
    row: dict[str, Any],
    submitted_alpha_ids: set[str],
    submitted_expressions: set[str],
    failed_submit_alpha_ids: set[str] | None = None,
) -> dict[str, Any]:
    return route_diagnosed_row(
        diagnose_scan_row(row),
        submitted_alpha_ids,
        submitted_expressions,
        failed_submit_alpha_ids,
    )


def low_value_avoid_entries(
    low_value_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in low_value_rows:
        grouped.setdefault(str(row.get("skeleton") or "unknown"), []).append(row)
    entries: list[dict[str, Any]] = []
    for skeleton, rows in sorted(grouped.items()):
        best = max(rows, key=lambda item: float(item.get("score") or 0.0))
        blockers = best.get("failed_checks") or best.get("pending_checks") or [
            "weak_signal_quality"
        ]
        entries.append(
            {
                "family": best.get("family") or "unknown",
                "skeleton": skeleton,
                "reason": ", ".join(str(item) for item in blockers),
                "primary_diagnosis_type": primary_diagnosis_type(best),
                "failure_diagnoses": best.get("failure_diagnoses")
                or diagnose_failure_objects(best),
                "avoid_mode": "do_not_regenerate_unchanged",
                "representative_alphas": [
                    row.get("alpha_id") for row in rows if row.get("alpha_id")
                ],
                "best_sharpe": metric_value(best, "sharpe"),
                "best_fitness": metric_value(best, "fitness"),
                "source_paths": sorted(
                    {
                        str(row.get("source_path") or "")
                        for row in rows
                        if row.get("source_path")
                    }
                ),
            }
        )
    return entries


def dedupe_triage_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        alpha_id = str(row.get("alpha_id") or "").strip()
        expression = normalize_expression(str(row.get("expression") or ""))
        settings = json.dumps(
            row.get("settings") or {}, sort_keys=True, ensure_ascii=False
        )
        key = alpha_id or f"{expression}|{settings}"
        if not key.strip("|"):
            continue
        existing = best_by_key.get(key)
        if existing is None or float(row.get("score") or 0.0) > float(
            existing.get("score") or 0.0
        ):
            best_by_key[key] = row
    return sorted(
        best_by_key.values(),
        key=lambda row: float(row.get("score") or 0.0),
        reverse=True,
    )


def family_efficiency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    families: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = str(row.get("family") or "unknown")
        entry = families.setdefault(
            family,
            {
                "family": family,
                "tested_count": 0,
                "direct_submit_count": 0,
                "optimize_next_count": 0,
                "low_value_count": 0,
                "already_submitted_count": 0,
                "local_pass_count": 0,
                "best_alpha_id": None,
                "best_score": None,
                "best_sharpe": None,
                "best_fitness": None,
            },
        )
        entry["tested_count"] += 1
        count_key = f"{row.get('triage_bucket') or 'low_value'}_count"
        if count_key in entry:
            entry[count_key] += 1
        if row_metric_pass(row):
            entry["local_pass_count"] += 1
        score = float(row.get("score") or 0.0)
        if entry["best_score"] is None or score > float(entry["best_score"]):
            entry["best_alpha_id"] = row.get("alpha_id")
            entry["best_score"] = score
            entry["best_sharpe"] = metric_value(row, "sharpe")
            entry["best_fitness"] = metric_value(row, "fitness")
    ordered = sorted(
        families.values(),
        key=lambda item: (
            -int(item.get("direct_submit_count") or 0),
            -int(item.get("optimize_next_count") or 0),
            -float(item.get("best_score") or 0.0),
            str(item.get("family") or ""),
        ),
    )
    return {"family_count": len(ordered), "families": ordered}


def diagnosis_policy_summary(run_tag: str, report: dict[str, Any]) -> str:
    lines = [
        "# Diagnosis Policy Evaluation",
        "",
        f"Daily run: `{run_tag}`",
        f"Generated at: `{report.get('generated_at')}`",
        f"Rows: `{report.get('total_rows')}`",
        f"Diagnoses: `{report.get('total_diagnoses')}`",
        f"Budget saved estimate: `{report.get('budget_saved_estimate')}`",
        "",
        "## Policies",
    ]
    for policy in report.get("policies", []):
        lines.extend(
            [
                "",
                f"### `{policy.get('diagnosis_type')}`",
                f"- Recommended policy: `{policy.get('recommended_policy')}`",
                f"- Budget policy: `{policy.get('budget_policy')}`",
                f"- Observed: `{policy.get('observed_count')}`",
                f"- Repair rate: `{policy.get('repair_candidate_rate')}`",
                f"- Blocked rate: `{policy.get('blocked_rate')}`",
                f"- Confidence: `{policy.get('policy_confidence')}`",
                f"- Next action: {policy.get('next_action')}",
            ]
        )
    return "\n".join(lines) + "\n"
