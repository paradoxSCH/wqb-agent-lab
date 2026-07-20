from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from wqb_agent_lab.research.self_corr_policy import self_corr_bucket as _policy_self_corr_bucket

from .artifacts import read_json


def failed_checks_from_check_list(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [check for check in checks if str(check.get("result", "")).upper() in {"FAIL", "ERROR"}]


def pending_checks_from_check_list(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [check for check in checks if str(check.get("result", "")).upper() == "PENDING"]


def units_warning_from_check_list(checks: list[dict[str, Any]]) -> bool:
    return any(
        check.get("name") == "UNITS" and str(check.get("result", "")).upper() == "WARNING"
        for check in checks
    )


def row_metric_pass(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics") or {}
    return (
        float(metrics.get("sharpe") or 0.0) >= 1.25
        and float(metrics.get("fitness") or 0.0) >= 1.0
        and float(metrics.get("turnover") or 1.0) <= 0.7
    )


def metric_value(row: dict[str, Any], name: str, default: float = 0.0) -> float:
    try:
        return float((row.get("metrics") or {}).get(name) or default)
    except (TypeError, ValueError):
        return default


def check_names_with_results(checks: list[dict[str, Any]], results: set[str]) -> list[str]:
    names: list[str] = []
    for check in checks:
        result = str(check.get("result", "") or "").upper()
        if result in results:
            name = str(check.get("name", "UNKNOWN") or "UNKNOWN").upper()
            names.append(name)
    return sorted(set(names))


def failed_check_names(checks: list[dict[str, Any]]) -> list[str]:
    return check_names_with_results(checks, {"FAIL", "ERROR"})


def pending_check_names(checks: list[dict[str, Any]]) -> list[str]:
    return check_names_with_results(checks, {"PENDING"})


def warning_check_names(checks: list[dict[str, Any]]) -> list[str]:
    return check_names_with_results(checks, {"WARNING"})


def row_near_pass(row: dict[str, Any]) -> bool:
    checks = row.get("checks") or []
    failures = set(failed_check_names(checks))
    pending = set(pending_check_names(checks))
    warnings = set(warning_check_names(checks))
    allowed_repair_checks = {
        "LOW_SHARPE",
        "LOW_FITNESS",
        "LOW_SUB_UNIVERSE_SHARPE",
        "SELF_CORRELATION",
        "CONCENTRATED_WEIGHT",
        "UNITS",
    }
    if (failures | pending | warnings) - allowed_repair_checks:
        return False
    if "SELF_CORRELATION" in failures and self_corr_bucket_from_checks(checks) == "extreme":
        return False
    return (
        metric_value(row, "sharpe") >= 1.10
        and metric_value(row, "fitness") >= 0.85
        and metric_value(row, "turnover", 1.0) <= 0.85
    )


def check_value(checks: list[dict[str, Any]], name: str) -> Any:
    for check in checks:
        if str(check.get("name") or "").upper() == name.upper():
            return check.get("value")
    return None


def check_limit(checks: list[dict[str, Any]], name: str) -> Any:
    for check in checks:
        if str(check.get("name") or "").upper() == name.upper():
            return check.get("limit")
    return None


def self_corr_bucket_from_checks(checks: list[dict[str, Any]]) -> str:
    return _policy_self_corr_bucket(check_value(checks, "SELF_CORRELATION"))


def sub_universe_bucket_from_checks(checks: list[dict[str, Any]]) -> str:
    value = _number_or_none(check_value(checks, "LOW_SUB_UNIVERSE_SHARPE"))
    if value is None:
        return "unknown"
    limit = _number_or_none(check_limit(checks, "LOW_SUB_UNIVERSE_SHARPE")) or 0.70
    gap = limit - value
    if gap >= 0.35:
        return "severe"
    if gap >= 0.10:
        return "moderate"
    return "mild"


def weak_signal_bucket_from_row(row: dict[str, Any]) -> str:
    checks = row.get("checks") or []
    sharpe = metric_value(row, "sharpe")
    fitness = metric_value(row, "fitness")
    sharpe_limit = _number_or_none(check_limit(checks, "LOW_SHARPE")) or 1.25
    fitness_limit = _number_or_none(check_limit(checks, "LOW_FITNESS")) or 1.00
    if sharpe >= 1.10 and fitness >= 0.85:
        return "near_pass"
    if sharpe / max(sharpe_limit, 1e-9) < 0.65 or fitness / max(fitness_limit, 1e-9) < 0.50:
        return "deep_fail"
    return "medium_gap"


def weight_concentration_bucket_from_checks(checks: list[dict[str, Any]]) -> str:
    value = _number_or_none(check_value(checks, "CONCENTRATED_WEIGHT"))
    if value is None:
        return "unknown"
    limit = _number_or_none(check_limit(checks, "CONCENTRATED_WEIGHT")) or 0.10
    ratio = value / max(limit, 1e-9)
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


def live_checks_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    return (((result.get("data") or {}).get("is") or {}).get("checks") or [])


def budget_exhausted(ledger: dict[str, Any]) -> bool:
    return int(ledger.get("remaining_simulations_after_commitments") or 0) <= 0


def candidate_score(row: dict[str, Any]) -> float:
    metrics = row.get("metrics") or {}
    checks = row.get("live_checks") or row.get("checks") or []
    self_corr = check_value(checks, "SELF_CORRELATION")
    score = 2.0 * float(metrics.get("sharpe") or 0.0)
    score += 1.5 * float(metrics.get("fitness") or 0.0)
    score -= 0.35 * float(metrics.get("turnover") or 0.0)
    if self_corr is not None:
        score += max(0.0, 0.7 - float(self_corr)) * 2.5
    if row.get("units_warning"):
        score -= 0.05
    return score


def alpha_id_from_column(column: str) -> str:
    return str(column).split(":", 1)[0]


def normalize_expression(expression: str) -> str:
    return " ".join(str(expression or "").split())


def submitted_registry_entries(payload: dict[str, Any]) -> tuple[set[str], set[str]]:
    submitted = payload.get("submitted") or []
    alpha_ids: set[str] = set()
    expressions: set[str] = set()
    for row in submitted:
        if not isinstance(row, dict):
            continue
        alpha_id = str(row.get("alpha_id") or "").strip()
        expression = normalize_expression(str(row.get("expression") or ""))
        if alpha_id:
            alpha_ids.add(alpha_id)
        if expression:
            expressions.add(expression)
    return alpha_ids, expressions


def confirmed_submission_state_alpha_ids(payload: Any) -> set[str]:
    """Return Alpha IDs already owned by another durable submission job.

    The historical name is retained for compatibility. Queued and ambiguous jobs also
    block a competing run from creating a second submission attempt.
    """
    if not isinstance(payload, dict):
        return set()
    non_blocking_statuses = {"rejected", "failed"}
    alpha_ids: set[str] = set()
    for job in payload.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        status = str(job.get("status") or "").strip()
        alpha_id = str(job.get("alpha_id") or "").strip()
        if alpha_id and status and status not in non_blocking_statuses:
            alpha_ids.add(alpha_id)
    return alpha_ids


def failed_submit_attempt_alpha_ids(payload: Any) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    results = payload.get("results") or []
    if not isinstance(results, list):
        return set()
    alpha_ids: set[str] = set()
    for row in results:
        if not isinstance(row, dict):
            continue
        alpha_id = str(row.get("alpha_id") or "").strip()
        if not alpha_id:
            continue
        if row.get("submitted") is True or str(row.get("action") or "") == "already_submitted":
            continue
        if row.get("post_attempted") is True:
            alpha_ids.add(alpha_id)
    return alpha_ids


def candidate_identity(row: dict[str, Any]) -> tuple[str, str]:
    expression = normalize_expression(str(row.get("expression", "")))
    settings = row.get("settings") or {}
    return expression, json.dumps(settings, sort_keys=True, ensure_ascii=False)


def completed_candidate_count(output_path: Path, candidates: list[dict[str, Any]]) -> int:
    if not output_path.exists():
        return 0
    target_keys = {candidate_identity(candidate) for candidate in candidates if normalize_expression(str(candidate.get("expression", "")))}
    if not target_keys:
        return 0
    rows = read_json(output_path, [])
    if not isinstance(rows, list):
        return 0
    completed_keys = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = candidate_identity(row)
        if key in target_keys:
            completed_keys.add(key)
    return len(completed_keys)


def candidate_field_hint(expression: str) -> str:
    tokens = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", expression)
    ignored = {
        "rank", "group_rank", "ts_delta", "ts_std_dev", "ts_corr", "ts_mean", "ts_zscore",
        "returns", "close", "volume", "vwap", "cap", "industry", "subindustry", "sector",
    }
    for token in tokens:
        if token not in ignored:
            return token
    return "unknown"


def candidate_family_hint(candidate: dict[str, Any]) -> str:
    for key in ("behavior_family", "family", "route_family"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return value
    note = str(candidate.get("note") or "")
    if ":" in note:
        return note.split(":", 1)[0].strip() or "unknown"
    return "unknown"


def candidate_skeleton_hint(candidate: dict[str, Any]) -> str:
    for key in ("skeleton", "chassis"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return value
    note = str(candidate.get("note") or "").strip()
    if note:
        family = candidate_family_hint(candidate)
        body = note.split(":", 1)[1].strip() if ":" in note else note
        body = re.sub(r"\s+variant\s+\S+.*$", "", body)
        if body:
            return f"{family}:{body}"
    field = candidate_field_hint(str(candidate.get("expression") or ""))
    family = candidate_family_hint(candidate)
    return f"{family}:{field}" if field != "unknown" else family


def is_pure_price_volume_candidate(candidate: dict[str, Any]) -> bool:
    expression = str(candidate.get("expression") or "")
    price_tokens = ("close", "open", "vwap", "volume", "returns")
    semantic_tokens = ("mdl", "analyst", "fundamental", "news_", "snt_", "implied_volatility", "shortsentiment")
    return any(token in expression for token in price_tokens) and not any(token in expression for token in semantic_tokens)


def choose_budgeted_candidates(
    candidates: list[dict[str, Any]],
    budget: int,
    *,
    single_base_share: float = 0.12,
    single_field_share: float = 0.12,
    single_family_share: float | None = None,
    single_skeleton_share: float | None = None,
    pure_price_volume_share: float | None = None,
    downweighted_families: set[str] | None = None,
    downweighted_family_share: float | None = None,
) -> list[dict[str, Any]]:
    """Pick a deterministic, diverse subset without exceeding the stage budget."""
    if budget <= 0:
        return []
    if len(candidates) <= budget:
        return list(candidates)

    base_cap = max(1, math.ceil(budget * single_base_share))
    field_cap = max(1, math.ceil(budget * single_field_share))
    family_cap = max(1, math.ceil(budget * single_family_share)) if single_family_share is not None else None
    skeleton_cap = max(1, math.ceil(budget * single_skeleton_share)) if single_skeleton_share is not None else None
    downweighted_family_cap = (
        max(0, math.floor(budget * downweighted_family_share))
        if downweighted_family_share is not None
        else None
    )
    downweighted_families = {str(family) for family in (downweighted_families or set()) if str(family)}
    pure_price_volume_cap = None
    if pure_price_volume_share is not None:
        pure_price_volume_cap = max(0, math.floor(budget * pure_price_volume_share))
    buckets: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        family = str(candidate.get("behavior_family") or candidate.get("family") or "unknown")
        buckets.setdefault(family, []).append(candidate)

    selected: list[dict[str, Any]] = []
    seen_expr: set[str] = set()
    base_counts: dict[str, int] = {}
    field_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    skeleton_counts: dict[str, int] = {}
    downweighted_family_counts: dict[str, int] = {}
    pure_price_volume_count = 0
    ordered_families = sorted(buckets, key=lambda family: (-len(buckets[family]), family))

    while len(selected) < budget and any(buckets.values()):
        made_progress = False
        for family in ordered_families:
            queue = buckets.get(family) or []
            while queue:
                candidate = queue.pop(0)
                expression = normalize_expression(str(candidate.get("expression", "")))
                if not expression or expression in seen_expr:
                    continue
                base = str(candidate.get("base_alpha_id") or candidate.get("source_alpha_id") or "")
                family_name = candidate_family_hint(candidate)
                skeleton = candidate_skeleton_hint(candidate)
                field = candidate_field_hint(expression)
                if base and base_counts.get(base, 0) >= base_cap:
                    continue
                if field_counts.get(field, 0) >= field_cap:
                    continue
                if family_cap is not None and family_counts.get(family_name, 0) >= family_cap:
                    continue
                if skeleton_cap is not None and skeleton_counts.get(skeleton, 0) >= skeleton_cap:
                    continue
                if (
                    downweighted_family_cap is not None
                    and family_name in downweighted_families
                    and downweighted_family_counts.get(family_name, 0) >= downweighted_family_cap
                ):
                    continue
                is_pure_price_volume = is_pure_price_volume_candidate(candidate)
                if pure_price_volume_cap is not None and is_pure_price_volume and pure_price_volume_count >= pure_price_volume_cap:
                    continue
                selected.append(candidate)
                seen_expr.add(expression)
                if base:
                    base_counts[base] = base_counts.get(base, 0) + 1
                field_counts[field] = field_counts.get(field, 0) + 1
                family_counts[family_name] = family_counts.get(family_name, 0) + 1
                skeleton_counts[skeleton] = skeleton_counts.get(skeleton, 0) + 1
                if family_name in downweighted_families:
                    downweighted_family_counts[family_name] = downweighted_family_counts.get(family_name, 0) + 1
                if is_pure_price_volume:
                    pure_price_volume_count += 1
                made_progress = True
                break
            if len(selected) >= budget:
                break
        if not made_progress:
            break

    return selected[:budget]


