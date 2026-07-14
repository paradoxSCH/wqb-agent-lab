"""Legacy experimental scheduler retained for historical-run compatibility.

Production automation is owned by ``src.wqb_agent_lab.workflow.ResearchWorkflow``.
New integrations must not depend on this module.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from src.atomic_json import atomic_write_json, locked_atomic_json_merge
from src.llm_provider import (
    LLMProvider,
    LLMProviderError,
    invalid_llm_config_diagnostic,
    invalid_llm_config_identity,
    llm_config_identity,
)
from src.llm_provider.config import resolve_llm_provider_config
from src.llm_provider.registry import create_llm_provider
from src.llm_template_generator import LLMTemplateGenerator
from src.wq.storage.paths import ProjectLayout, resolve_state_path as resolve_layout_state_path
from src.self_corr_policy import SELF_CORR_NEAR_REPAIR_MAX, self_corr_bucket as _policy_self_corr_bucket
from wqb_agent_lab.platform import WQBClient, load_operator_names


PASS_SHARPE = 1.25
PASS_FITNESS = 1.00
PASS_TURNOVER = 0.70

SCAN_MAX_CONCURRENCY = 3
TARGET_SCAN_CANDIDATES = 36
MAX_FAMILIES_PER_CHASSIS = 3
GOOD_SELF_CORR_MAX = 0.62
EDGE_SELF_CORR_MIN = 0.65
MILD_SELF_CORR_REPAIR_MAX = SELF_CORR_NEAR_REPAIR_MAX

NEAR_PASS_SHARPE = 1.15
NEAR_PASS_FITNESS = 0.90

DEFAULT_SCAN_SETTINGS = {
    "instrumentType": "EQUITY",
    "region": "USA",
    "universe": "TOP3000",
    "delay": 1,
    "decay": 6,
    "neutralization": "MARKET",
    "truncation": 0.05,
    "pasteurization": "ON",
    "unitHandling": "VERIFY",
    "nanHandling": "ON",
    "language": "FASTEXPR",
    "visualization": False,
}

STRUCTURAL_FIELD_NAMES = {
    "top3000",
    "top1000",
    "top500",
    "top200",
    "isin",
    "cusip",
    "sedol",
    "ticker",
    "currency",
    "currency_code",
    "country",
    "exchange",
    "split",
}

STRUCTURAL_KEYWORDS = (
    "classification",
    "code",
    "currency",
    "identifier",
    "grouping",
    "hierarchy",
    "universe",
    "membership",
    "pricing currency",
    "type indices",
)

VECTOR_KEYWORDS = (
    "typevec",
    "sentvec",
    "buzzvec",
)

EVENT_KEYWORDS = (
    "event ",
    "event-",
    "event_",
)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return deepcopy(default)


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _slug(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-")


def _relpath(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _load_state(state_path: Path) -> dict[str, Any]:
    return _read_json(state_path, {})


def _dedupe_entries_by_key(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        value = str(row.get(key, "")).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(row)
    return deduped


def _field_cluster(field_id: str, description: str = "") -> str:
    haystack = f"{field_id} {description}".lower()
    if any(token in haystack for token in ("eps", "earnings")):
        return "eps"
    if any(token in haystack for token in ("sale", "sales", "revenue", "revt")):
        return "sales"
    if any(token in haystack for token in ("cfps", "cashflow", "cash_flow", "cash flow")):
        return "cashflow"
    if any(token in haystack for token in ("dividend", "ady", "dps")):
        return "dividend"
    if any(token in haystack for token in ("breakeven", "option", "implied", "put", "call", "forward")):
        return "option"
    if any(token in haystack for token in ("sentiment", "buzz", "social", "snt", "scl")):
        return "social"
    if any(token in haystack for token in ("rating", "target")):
        return "analyst-rating"
    return _slug(field_id).split("-")[0] or "unknown"


def _chassis_signature(expression: str, fields: list[str] | None = None) -> str:
    normalized = re.sub(r"\s+", " ", str(expression or "").strip())
    for field_id in sorted(fields or [], key=len, reverse=True):
        normalized = re.sub(rf"\b{re.escape(field_id)}\b", "FIELD", normalized)
    operators = load_operator_names()
    def normalize_identifier(match: re.Match[str]) -> str:
        token = match.group(0)
        lower = token.lower()
        if lower in operators:
            return lower
        if lower in {"cap", "close", "returns", "volume", "vwap", "open", "high", "low"}:
            return "SERIES"
        if lower in {"subindustry", "industry", "sector", "market"}:
            return "GROUP"
        return "FIELD"

    normalized = re.sub(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", normalize_identifier, normalized)
    normalized = re.sub(r"\b(cap|close|returns|volume|vwap|open|high|low)\b", "SERIES", normalized)
    normalized = re.sub(r"\b(subindustry|industry|sector|market)\b", "GROUP", normalized)
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "N", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _scoreboard_entry(scoreboard: dict[str, Any], key: str) -> dict[str, Any]:
    return scoreboard.setdefault(
        key,
        {
            "scanned_count": 0,
            "pass_count": 0,
            "near_pass_count": 0,
            "self_corr_fail_count": 0,
            "low_sharpe_fail_count": 0,
            "submitted_count": 0,
            "last_seen_iteration": 0,
        },
    )


def _update_score_entry(
    entry: dict[str, Any],
    *,
    passed: bool,
    near_pass: bool,
    failures: set[str],
    iteration: int,
    metrics: dict[str, Any] | None = None,
) -> None:
    entry["scanned_count"] = int(entry.get("scanned_count", 0) or 0) + 1
    entry["pass_count"] = int(entry.get("pass_count", 0) or 0) + int(passed)
    entry["near_pass_count"] = int(entry.get("near_pass_count", 0) or 0) + int(near_pass)
    entry["self_corr_fail_count"] = int(entry.get("self_corr_fail_count", 0) or 0) + int("SELF_CORRELATION" in failures)
    entry["low_sharpe_fail_count"] = int(entry.get("low_sharpe_fail_count", 0) or 0) + int("LOW_SHARPE" in failures)
    entry["terminal_error_count"] = int(entry.get("terminal_error_count", 0) or 0) + int("TERMINAL_ERROR" in failures)
    entry["last_seen_iteration"] = max(int(entry.get("last_seen_iteration", 0) or 0), iteration)
    if passed:
        entry["last_pass_iteration"] = max(int(entry.get("last_pass_iteration", 0) or 0), iteration)
    if near_pass:
        entry["last_near_pass_iteration"] = max(int(entry.get("last_near_pass_iteration", 0) or 0), iteration)
    if passed or near_pass:
        entry["low_value_streak"] = 0
    else:
        entry["low_value_streak"] = int(entry.get("low_value_streak", 0) or 0) + 1
    metrics = metrics or {}
    if metrics:
        entry["best_sharpe"] = max(float(entry.get("best_sharpe", 0.0) or 0.0), float(metrics.get("sharpe", 0.0) or 0.0))
        entry["best_fitness"] = max(float(entry.get("best_fitness", 0.0) or 0.0), float(metrics.get("fitness", 0.0) or 0.0))


def _rate(entry: dict[str, Any], key: str) -> float:
    scanned = int(entry.get("scanned_count", 0) or 0)
    if not scanned:
        return 0.0
    return float(entry.get(key, 0) or 0) / scanned


def _dataset_is_in_cooldown(dataset_stats: dict[str, Any], *, iteration: int, cooldown_rounds: int = 3) -> bool:
    scanned = int(dataset_stats.get("scanned_count", 0) or 0)
    if not scanned:
        return False
    terminal_error_rate = _rate(dataset_stats, "terminal_error_count")
    last_seen = int(dataset_stats.get("last_seen_iteration", 0) or 0)
    low_value_streak = int(dataset_stats.get("low_value_streak", 0) or 0)
    pass_rate = _rate(dataset_stats, "pass_count")
    near_rate = _rate(dataset_stats, "near_pass_count")
    stale_failure = low_value_streak >= 12 and pass_rate < 0.08 and near_rate < 0.12
    return (terminal_error_rate >= 0.5 or stale_failure) and iteration - last_seen <= cooldown_rounds


def _recent_dataset_dead_zone(
    workspace_root: Path,
    state: dict[str, Any],
    dataset: str,
    *,
    lookback: int = 8,
    min_rows: int = 12,
) -> bool:
    family_by_iteration = {
        int(entry.get("iteration", 0) or 0): str(entry.get("chosen_bucket", ""))
        for entry in state.get("completed_stages", [])
        if entry.get("stage") == "family_generation"
    }
    scan_entries = [
        entry
        for entry in state.get("completed_stages", [])
        if entry.get("stage") in {"scan", "scan-retry"}
        and dataset in family_by_iteration.get(int(entry.get("iteration", 0) or 0), "")
    ]

    reviewed = 0
    clean_pass = 0
    metric_ready = 0
    for entry in scan_entries[-lookback:]:
        scan_output = entry.get("scan_output")
        if not scan_output:
            continue
        rows = _read_json(workspace_root / str(scan_output), [])
        for row in rows:
            reviewed += 1
            metrics = row.get("metrics") or {}
            sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
            fitness = float(metrics.get("fitness", 0.0) or 0.0)
            turnover = float(metrics.get("turnover", 1.0) or 1.0)
            if _pass_row(row):
                clean_pass += 1
            if sharpe >= PASS_SHARPE and fitness >= PASS_FITNESS and turnover <= PASS_TURNOVER:
                metric_ready += 1
    return reviewed >= min_rows and clean_pass == 0 and metric_ready == 0


def _dataset_bandit_score(
    *,
    priority: int,
    dataset: str,
    rows: list[dict[str, Any]],
    dataset_stats: dict[str, Any],
    iteration: int,
    blocked_skeletons: set[str],
) -> tuple[float, int, float]:
    count = len(rows)
    avg_value = sum(float(row.get("research_value_score", 0.0) or 0.0) for row in rows) / max(count, 1)
    avg_crowding = sum(float(row.get("crowding_score", 0.0) or 0.0) for row in rows) / max(count, 1)
    score = avg_value - 0.25 * avg_crowding
    scanned = int(dataset_stats.get("scanned_count", 0) or 0)
    if scanned:
        score += 30.0 * _rate(dataset_stats, "pass_count")
        score += 18.0 * _rate(dataset_stats, "near_pass_count")
        score += 4.0 * float(dataset_stats.get("best_fitness", 0.0) or 0.0)
        score += 1.5 * float(dataset_stats.get("best_sharpe", 0.0) or 0.0)
        score -= 28.0 * _rate(dataset_stats, "terminal_error_count")
        score -= 12.0 * _rate(dataset_stats, "self_corr_fail_count")
        score -= 8.0 * _rate(dataset_stats, "low_sharpe_fail_count")
    score -= min(int(dataset_stats.get("submitted_count", 0) or 0) * 2.5, 10.0)
    score += 2.0 / (1 + scanned)

    if _dataset_is_in_cooldown(dataset_stats, iteration=iteration):
        score -= 100.0

    if dataset in ("fundamental6", "fund6"):
        cfo_block_count = sum(1 for skeleton in blocked_skeletons if "cfo" in skeleton or "cashflow" in skeleton)
        score -= min(cfo_block_count * 0.05, 2.0)
        if cfo_block_count >= 20:
            score -= 5.0

    return (-priority, score, count)


def _load_chassis_blocklist(run_dir: Path) -> set[str]:
    explicit = _read_json(run_dir / "chassis_blocklist.json", [])
    blocked = {str(row.get("chassis", "")).strip() for row in explicit if row.get("chassis")}
    submitted_skeletons = {
        str(row.get("skeleton", ""))
        for row in _read_json(run_dir / "alpha_skeleton_blocklist.json", [])
        if row.get("status") == "submitted"
    }
    for path in sorted(run_dir.glob("submission_tiers_round*.json")) + [run_dir / "submission_tiers.json"]:
        payload = _read_json(path, {})
        if not isinstance(payload, dict):
            continue
        for tier_name in ("tier_1", "tier_2", "tier_3"):
            for row in payload.get(tier_name, []):
                if row.get("skeleton") in submitted_skeletons and row.get("expression"):
                    blocked.add(str(row.get("chassis") or _chassis_signature(row.get("expression", ""), row.get("fields", []))))
    return {item for item in blocked if item}


def _simulation_cache_key(expression: str, settings: dict[str, Any] | None = None) -> str:
    normalized_expression = re.sub(r"\s+", " ", str(expression or "").strip())
    normalized_settings = json.dumps(settings or {}, sort_keys=True, separators=(",", ":"))
    return f"{normalized_expression}||{normalized_settings}"


def _update_simulation_cache(run_dir: Path, rows: list[dict[str, Any]], *, default_settings: dict[str, Any] | None = None) -> None:
    cache_path = run_dir / "simulation_cache.json"
    cache = _read_json(cache_path, {})
    if not isinstance(cache, dict):
        cache = {}
    for row in rows:
        expression = str(row.get("expression", "")).strip()
        if not expression:
            continue
        settings = row.get("settings") or default_settings or {}
        cache[_simulation_cache_key(expression, settings)] = {
            "alpha_id": row.get("alpha_id"),
            "metrics": row.get("metrics", {}),
            "checks": row.get("checks", []),
            "note": row.get("note", ""),
        }
    _write_json(cache_path, cache)


def _append_completed_stage(state: dict[str, Any], entry: dict[str, Any]) -> None:
    state.setdefault("completed_stages", []).append(entry)


def _fail_names(checks: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for check in checks or []:
        if check.get("result") in {"FAIL", "ERROR"}:
            names.append(str(check.get("name", "")).strip().upper())
    return names


def _warning_names(checks: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for check in checks or []:
        if check.get("result") == "WARNING":
            names.append(str(check.get("name", "")).strip().upper())
    return names


def _pending_names(checks: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for check in checks or []:
        if check.get("result") == "PENDING":
            names.append(str(check.get("name", "")).strip().upper())
    return names


def _submission_blockers(checks: list[dict[str, Any]] | None) -> list[str]:
    blockers = set(_fail_names(checks))
    blockers.update(name for name in _warning_names(checks) if name == "UNITS")
    blockers.update(name for name in _pending_names(checks) if name == "SELF_CORRELATION")
    return sorted(blockers)


def _operator_chain(expression: str) -> list[str]:
    return [match.group(1).lower() for match in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", str(expression or ""))]


def _normalized_check_details(row: dict[str, Any]) -> dict[str, Any]:
    passed: list[str] = []
    failed: list[str] = []
    pending: list[str] = []
    warnings: list[str] = []
    details: list[dict[str, Any]] = []
    for check in row.get("checks") or []:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name", "")).strip().upper()
        result = str(check.get("result", "PENDING")).strip().upper()
        detail = {
            "name": name,
            "result": result,
            "limit": check.get("limit"),
            "value": check.get("value"),
        }
        details.append(detail)
        if result == "PASS":
            passed.append(name)
        elif result in {"FAIL", "ERROR"}:
            failed.append(name)
        elif result == "WARNING":
            warnings.append(name)
        else:
            pending.append(name)

    if row.get("error") or not row.get("alpha_id"):
        failed.append("TERMINAL_ERROR")
        details.append({"name": "TERMINAL_ERROR", "result": "ERROR", "limit": None, "value": row.get("error")})

    return {
        "passed_checks": passed,
        "failed_checks": failed,
        "pending_checks": pending,
        "warning_checks": warnings,
        "check_details": details,
        "route_decision": _route_decision(row, set(failed), set(warnings), set(pending)),
    }


def _route_decision(
    row: dict[str, Any],
    failures: set[str] | None = None,
    warnings: set[str] | None = None,
    pending: set[str] | None = None,
) -> str:
    failures = failures if failures is not None else set(_fail_names(row.get("checks")))
    warnings = warnings if warnings is not None else set(_warning_names(row.get("checks")))
    pending = pending if pending is not None else set(_pending_names(row.get("checks")))
    metrics = row.get("metrics") or {}
    sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
    fitness = float(metrics.get("fitness", 0.0) or 0.0)
    turnover = float(metrics.get("turnover", 0.0) or 0.0)
    if row.get("error") or "TERMINAL_ERROR" in failures:
        return "avoid_invalid_or_terminal_error"
    if _pass_row(row):
        return "direct_submit"
    if "UNITS" in warnings:
        return "fix_units_or_rescale"
    if "SELF_CORRELATION" in pending:
        return "pending_self_correlation_check"
    if "SELF_CORRELATION" in failures:
        bucket = _self_corr_bucket(row)
        if bucket == "extreme":
            return "replace_overcrowded_signal"
        if bucket == "mild":
            return "self_corr_light_repair"
        return "self_corr_escape"
    if "CONCENTRATED_WEIGHT" in failures:
        bucket = _weight_concentration_bucket(row)
        if bucket == "severe":
            return "replace_concentrated_expression_structure"
        return "smooth_or_truncate_weight_concentration"
    if "LOW_SUB_UNIVERSE_SHARPE" in failures:
        bucket = _sub_universe_bucket(row)
        if bucket == "severe":
            return "replace_unstable_universe_proxy"
        if bucket == "moderate":
            return "controlled_sub_universe_repair"
        return "neutralization_or_grouping_shift"
    if "LOW_SHARPE" in failures and sharpe < NEAR_PASS_SHARPE:
        if _weak_signal_bucket(sharpe, fitness, row) == "deep_fail":
            return "replace_weak_behavior_proxy"
        return "rewrite_weak_signal_chassis"
    if "LOW_FITNESS" in failures and _weak_signal_bucket(sharpe, fitness, row) == "deep_fail":
        return "replace_weak_behavior_proxy"
    if "HIGH_TURNOVER" in failures or turnover > 0.55:
        return "increase_decay_or_reduce_turnover"
    if _near_pass_row(row):
        return "local_parameter_optimization"
    return "avoid_low_value"


def _failure_severity(route: str) -> str:
    if route in {"avoid_invalid_or_terminal_error", "abandon_low_sharpe"}:
        return "high"
    if route == "replace_overcrowded_signal":
        return "high"
    if route in {"replace_unstable_universe_proxy", "replace_weak_behavior_proxy", "replace_concentrated_expression_structure"}:
        return "high"
    if route in {
        "self_corr_escape",
        "self_corr_light_repair",
        "neutralization_or_grouping_shift",
        "controlled_sub_universe_repair",
        "rewrite_weak_signal_chassis",
        "smooth_or_truncate_weight_concentration",
    }:
        return "medium"
    return "low"


def _diversity_suggestions(
    *,
    selected_dataset: str,
    selected_fields: list[dict[str, Any]],
    field_scoreboard: dict[str, Any],
    dataset_scoreboard: dict[str, Any],
) -> dict[str, Any]:
    cluster_counts: dict[str, int] = {}
    field_candidates: list[tuple[int, str]] = []
    for row in selected_fields:
        field_id = str(row.get("field_id", ""))
        cluster = _field_cluster(field_id, str(row.get("description", "")))
        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
        scanned = int((field_scoreboard.get(field_id, {}) or {}).get("scanned_count", 0) or 0)
        field_candidates.append((scanned, field_id))
    underused_fields = [field_id for _, field_id in sorted(field_candidates)[:5]]
    overused_fields = [field_id for scanned, field_id in sorted(field_candidates, reverse=True) if scanned >= 5][:5]
    dataset_stats = dataset_scoreboard.get(selected_dataset, {}) if isinstance(dataset_scoreboard, dict) else {}
    return {
        "selected_dataset": selected_dataset,
        "dataset_attempts": int(dataset_stats.get("scanned_count", 0) or 0),
        "underused_fields": underused_fields,
        "overused_fields": overused_fields,
        "field_clusters": cluster_counts,
        "required_slots": [
            "normalized-anchor",
            "mild-reversal-blend",
            "temporal-delta",
            "spread-ratio",
            "conditional-gated",
            "correlation-orthogonal",
        ],
    }


def _append_decision_trace(run_dir: Path, iteration: int, stage: str, payload: dict[str, Any]) -> tuple[Path, Path]:
    trace_path = run_dir / f"decision_trace_round{iteration}.json"
    trace_md_path = run_dir / f"decision_trace_round{iteration}.md"
    trace = _read_json(trace_path, {"iteration": iteration, "steps": []})
    if not isinstance(trace, dict):
        trace = {"iteration": iteration, "steps": []}
    trace.setdefault("iteration", iteration)
    trace.setdefault("steps", []).append({"stage": stage, **payload})
    lines = [f"# Decision Trace Round {iteration}", ""]
    for step in trace.get("steps", []):
        lines.append(f"## {step.get('stage', 'unknown')}")
        summary = step.get("summary") or step.get("decision") or ""
        if summary:
            lines.append(f"- Summary: {summary}")
        for key in ("chosen_dataset", "route_counts", "diversity_suggestions", "cache_skipped_count"):
            if key in step:
                lines.append(f"- {key}: `{json.dumps(step[key], ensure_ascii=False)}`")
        lines.append("")
    _write_json(trace_path, trace)
    _write_text(trace_md_path, "\n".join(lines))
    return trace_path, trace_md_path


def _update_knowledge_artifacts(
    run_dir: Path,
    *,
    iteration: int,
    rows: list[dict[str, Any]],
    direct_submit: list[dict[str, Any]],
    optimize_next: list[dict[str, Any]],
    low_value_rows: list[dict[str, Any]],
) -> dict[str, int]:
    knowledge_path = run_dir / "knowledge_base.json"
    success_path = run_dir / "success_patterns.json"
    failure_path = run_dir / "failure_pitfalls.json"
    field_path = run_dir / "field_insights.json"
    knowledge = _read_json(knowledge_path, {"success_patterns": [], "failure_pitfalls": [], "field_insights": []})
    if not isinstance(knowledge, dict):
        knowledge = {"success_patterns": [], "failure_pitfalls": [], "field_insights": []}

    success_patterns = list(knowledge.get("success_patterns", []))
    failure_pitfalls = list(knowledge.get("failure_pitfalls", []))
    field_insights = list(knowledge.get("field_insights", []))
    seen_success = {str(item.get("pattern", "")) for item in success_patterns}
    seen_failure = {str(item.get("pattern", "")) for item in failure_pitfalls}
    seen_field = {str(item.get("pattern", "")) for item in field_insights}

    for row in direct_submit + optimize_next:
        pattern = str(row.get("chassis") or _chassis_signature(row.get("expression", ""), row.get("fields", [])))
        if pattern and pattern not in seen_success:
            metrics = row.get("metrics") or {}
            success_patterns.append(
                {
                    "pattern": pattern,
                    "skeleton": row.get("skeleton", ""),
                    "dataset": row.get("dataset", ""),
                    "example_expression": row.get("expression", ""),
                    "alpha_id": row.get("alpha_id"),
                    "metrics": metrics,
                    "source_iteration": iteration,
                    "source_bucket": "direct_submit" if row in direct_submit else "optimize_next",
                }
            )
            seen_success.add(pattern)
        for field_id in row.get("fields", []) or []:
            field_pattern = f"FIELD_EFFECTIVE:{field_id}"
            if field_pattern not in seen_field:
                field_insights.append({"pattern": field_pattern, "field": field_id, "dataset": row.get("dataset", ""), "source_iteration": iteration})
                seen_field.add(field_pattern)

    for row in low_value_rows:
        normalized = _normalized_check_details(row)
        pattern = str(row.get("chassis") or _chassis_signature(row.get("expression", ""), row.get("fields", [])))
        if pattern and pattern not in seen_failure:
            failure_pitfalls.append(
                {
                    "pattern": pattern,
                    "skeleton": row.get("skeleton", ""),
                    "dataset": row.get("dataset", ""),
                    "example_expression": row.get("expression", ""),
                    "failed_checks": normalized["failed_checks"],
                    "route_decision": normalized["route_decision"],
                    "severity": _failure_severity(normalized["route_decision"]),
                    "metrics": row.get("metrics", {}),
                    "source_iteration": iteration,
                }
            )
            seen_failure.add(pattern)
        if normalized["route_decision"] == "avoid_invalid_or_terminal_error":
            for field_id in row.get("fields", []) or []:
                field_pattern = f"FIELD_PROBLEMATIC:{field_id}"
                if field_pattern not in seen_field:
                    field_insights.append({"pattern": field_pattern, "field": field_id, "dataset": row.get("dataset", ""), "reason": row.get("error", "terminal error"), "source_iteration": iteration})
                    seen_field.add(field_pattern)

    knowledge = {
        "success_patterns": success_patterns,
        "failure_pitfalls": failure_pitfalls,
        "field_insights": field_insights,
        "last_updated_iteration": iteration,
        "source_row_count": len(rows),
    }
    _write_json(knowledge_path, knowledge)
    _write_json(success_path, success_patterns)
    _write_json(failure_path, failure_pitfalls)
    _write_json(field_path, field_insights)
    return {
        "success_patterns": len(success_patterns),
        "failure_pitfalls": len(failure_pitfalls),
        "field_insights": len(field_insights),
    }


def _pass_row(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics") or {}
    return (
        float(metrics.get("sharpe", 0.0) or 0.0) >= PASS_SHARPE
        and float(metrics.get("fitness", 0.0) or 0.0) >= PASS_FITNESS
        and float(metrics.get("turnover", 1.0) or 1.0) <= PASS_TURNOVER
        and not _submission_blockers(row.get("checks"))
    )


def _near_pass_row(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics") or {}
    sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
    fitness = float(metrics.get("fitness", 0.0) or 0.0)
    turnover = float(metrics.get("turnover", 0.0) or 0.0)
    failures = set(_fail_names(row.get("checks")))
    if not failures:
        return False
    if turnover > PASS_TURNOVER:
        return False
    if failures - {"LOW_SHARPE", "LOW_FITNESS", "LOW_SUB_UNIVERSE_SHARPE"}:
        return False
    if sharpe >= NEAR_PASS_SHARPE and fitness >= NEAR_PASS_FITNESS:
        return True
    if sharpe >= PASS_SHARPE and fitness >= 0.80:
        return True
    if fitness >= PASS_FITNESS and sharpe >= 1.20:
        return True
    return False


def _row_score(row: dict[str, Any]) -> tuple[float, float, float, float]:
    metrics = row.get("metrics") or {}
    penalty = len(_fail_names(row.get("checks")))
    return (
        float(metrics.get("fitness", 0.0) or 0.0) - penalty,
        float(metrics.get("sharpe", 0.0) or 0.0),
        float(metrics.get("returns", 0.0) or 0.0),
        -float(metrics.get("drawdown", 0.0) or 0.0),
    )


def _self_corr_value(row: dict[str, Any]) -> float | None:
    for check in row.get("checks") or []:
        if str(check.get("name", "")).upper() == "SELF_CORRELATION" and check.get("value") is not None:
            try:
                return float(check.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def _check_value(row: dict[str, Any], name: str) -> float | None:
    for check in row.get("checks") or []:
        if str(check.get("name", "")).upper() == name.upper() and check.get("value") is not None:
            try:
                return float(check.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def _check_limit(row: dict[str, Any], name: str) -> float | None:
    for check in row.get("checks") or []:
        if str(check.get("name", "")).upper() == name.upper() and check.get("limit") is not None:
            try:
                return float(check.get("limit"))
            except (TypeError, ValueError):
                return None
    return None


def _self_corr_bucket(row: dict[str, Any]) -> str:
    return _policy_self_corr_bucket(_self_corr_value(row))


def _sub_universe_bucket(row: dict[str, Any]) -> str:
    value = _check_value(row, "LOW_SUB_UNIVERSE_SHARPE")
    if value is None:
        return "unknown"
    limit = _check_limit(row, "LOW_SUB_UNIVERSE_SHARPE") or 0.70
    gap = limit - value
    if gap >= 0.35:
        return "severe"
    if gap >= 0.10:
        return "moderate"
    return "mild"


def _weak_signal_bucket(sharpe: float, fitness: float, row: dict[str, Any]) -> str:
    sharpe_limit = _check_limit(row, "LOW_SHARPE") or PASS_SHARPE
    fitness_limit = _check_limit(row, "LOW_FITNESS") or PASS_FITNESS
    if sharpe >= NEAR_PASS_SHARPE and fitness >= NEAR_PASS_FITNESS:
        return "near_pass"
    if sharpe / max(sharpe_limit, 1e-9) < 0.65 or fitness / max(fitness_limit, 1e-9) < 0.50:
        return "deep_fail"
    return "medium_gap"


def _weight_concentration_bucket(row: dict[str, Any]) -> str:
    value = _check_value(row, "CONCENTRATED_WEIGHT")
    if value is None:
        return "unknown"
    limit = _check_limit(row, "CONCENTRATED_WEIGHT") or 0.10
    ratio = value / max(limit, 1e-9)
    if ratio >= 2.0:
        return "severe"
    if ratio >= 1.25:
        return "moderate"
    return "mild"


def _quality_label(row: dict[str, Any]) -> str:
    metrics = row.get("metrics") or {}
    sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
    fitness = float(metrics.get("fitness", 0.0) or 0.0)
    turnover = float(metrics.get("turnover", 0.0) or 0.0)
    self_corr = _self_corr_value(row)
    sub_margin = 0.0
    for check in row.get("checks") or []:
        if str(check.get("name", "")).upper() == "LOW_SUB_UNIVERSE_SHARPE":
            try:
                sub_margin = float(check.get("value") or 0.0) - float(check.get("limit") or 0.0)
            except (TypeError, ValueError):
                sub_margin = 0.0
            break
    if sharpe >= 1.80 and fitness >= 1.15 and turnover <= 0.35 and self_corr is not None and self_corr <= GOOD_SELF_CORR_MAX and sub_margin >= 0.10:
        return "excellent"
    if sharpe >= 1.58 and fitness >= 1.10 and turnover <= 0.45 and self_corr is not None and self_corr <= GOOD_SELF_CORR_MAX and sub_margin >= 0.05:
        return "good"
    if self_corr is not None and self_corr >= EDGE_SELF_CORR_MIN:
        return "edge_submit"
    return "submit"


def _best_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=_row_score)


def _load_candidate_family_map(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "candidate_families.json"
    families = _read_json(path, [])
    mapping: dict[str, dict[str, Any]] = {}
    for family in families:
        expr = str(family.get("expression", ""))
        if expr:
            mapping[expr] = family
    return mapping


def _load_optimization_candidate_map(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "optimization_candidates.json"
    rows = _read_json(path, [])
    mapping: dict[str, dict[str, Any]] = {}
    for row in rows:
        note = str(row.get("note", ""))
        if note:
            mapping[note] = row
    return mapping


def _infer_row_context(
    row: dict[str, Any],
    family_map: dict[str, dict[str, Any]],
    optimization_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    note = str(row.get("note", ""))
    optimization_meta = optimization_map.get(note)
    if optimization_meta:
        return {
            "dataset": optimization_meta.get("dataset", ""),
            "family": optimization_meta.get("family", optimization_meta.get("skeleton", "")),
            "skeleton": optimization_meta.get("skeleton", ""),
            "fields": optimization_meta.get("fields", []),
            "archetype": optimization_meta.get("archetype", ""),
            "chassis": optimization_meta.get("chassis", ""),
            "source": "optimization",
        }

    family_meta = family_map.get(str(row.get("expression", "")), {})
    return {
        "dataset": family_meta.get("dataset", ""),
        "family": family_meta.get("family", family_meta.get("signal_idea", "")),
        "skeleton": family_meta.get("skeleton", _skeleton_from_note(note)),
        "fields": family_meta.get("fields", []),
        "archetype": family_meta.get("archetype", ""),
        "chassis": family_meta.get("chassis", ""),
        "source": "scan",
    }


def _skeleton_from_note(note: str) -> str:
    if not note:
        return "unknown"
    parts = note.split()
    if len(parts) >= 2:
        return parts[1]
    return _slug(note)


def _is_structural_field(field: dict[str, Any]) -> tuple[bool, str]:
    field_id = str(field.get("field_id", ""))
    field_id_lower = field_id.lower()
    description = str(field.get("description", "")).lower()
    if field_id_lower in STRUCTURAL_FIELD_NAMES:
        return True, "Structural identifier or market metadata field."
    if any(token in field_id_lower for token in ("currency", "ticker", "typevec", "sentvec")):
        return True, "Structural, currency, or vector-like field name."
    if any(keyword in description for keyword in STRUCTURAL_KEYWORDS):
        return True, "Structural grouping or universe field."
    return False, ""


def _is_operator_incompatible(field: dict[str, Any]) -> tuple[bool, str]:
    field_id = str(field.get("field_id", "")).lower()
    description = str(field.get("description", "")).lower()
    if any(keyword in field_id for keyword in VECTOR_KEYWORDS):
        return True, "Vector-style field is incompatible with the scalar group_rank templates."
    if any(keyword in description for keyword in EVENT_KEYWORDS):
        return True, "Event-style field is skipped by the scalar scheduler templates."
    return False, ""


def _template_candidates(field: dict[str, Any]) -> list[dict[str, str]]:
    field_id = str(field.get("field_id", ""))
    category = str(field.get("category", ""))
    description = str(field.get("description", ""))
    base = _slug(field_id)
    label = field_id.replace("_", " ")
    field_term = _scheduler_field_term(field_id, category, description)

    templates = [
        {
            "family": f"{label} level",
            "signal_idea": "Level",
            "skeleton": f"{base}-level",
            "expression": f"group_rank({field_id}, subindustry)",
        },
        {
            "family": f"{label} by cap",
            "signal_idea": "Normalization",
            "skeleton": f"{base}-cap",
            "expression": f"group_rank({field_id} / cap, subindustry)",
        },
        {
            "family": f"{label} by price",
            "signal_idea": "Normalization",
            "skeleton": f"{base}-price",
            "expression": f"group_rank({field_id} / close, subindustry)",
        },
    ]

    if category in {"analyst", "fundamental", "option", "model", "socialmedia", "sentiment"}:
        templates.append(
            {
                "family": f"{label} revision",
                "signal_idea": "Revision",
                "skeleton": f"{base}-revision",
                "expression": f"group_rank(ts_delta({field_id}, 20), subindustry)",
            }
        )
    templates.extend(
        [
            {
                "family": f"{label} mild reversal blend",
                "signal_idea": "Winner-neighborhood reversal blend",
                "skeleton": f"{base}-winner-reversal-blend",
                "expression": f"group_rank(rank(-returns) / 10 + {field_term} / 10, industry)",
            },
            {
                "family": f"{label} orthogonal corr blend",
                "signal_idea": "Orthogonal correlation blend",
                "skeleton": f"{base}-winner-corr-orthogonal",
                "expression": f"group_rank(rank(ts_corr(rank(-ts_delta(close, 3)), rank({field_term}), 20)) / 12 + rank(-returns) / 15, industry)",
            },
            {
                "family": f"{label} gated reversal",
                "signal_idea": "Conditional gated reversal",
                "skeleton": f"{base}-winner-gated-reversal",
                "expression": f"group_rank(if_else(rank({field_term}) > 0.8, rank(-ts_delta({field_term}, 5)) / 12, rank(-returns) / 15), industry)",
            },
        ]
    )
    return templates


def _scheduler_field_term(field_id: str, category: str = "", description: str = "") -> str:
    haystack = f"{field_id} {category} {description}".lower()
    if any(token in haystack for token in ("per_share", "eps", "price", "breakeven", "forward_price", "target")):
        return f"{field_id} / close"
    if any(token in haystack for token in ("sentiment", "buzz", "score", "rank", "ratio", "pcr", "correlation", "risk")):
        return field_id
    return f"{field_id} / cap"


def _supplement_rule_based_families(
    families: list[dict[str, Any]],
    *,
    selected_fields: list[dict[str, Any]],
    dataset: str,
    blocked_skeletons: set[str],
    blocked_chassis: set[str],
    kept_fields: list[str],
    target_count: int,
    archetype: str,
    reason: str,
    simulation_cache: dict[str, Any] | None = None,
    scan_settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    seen_skeletons = {str(family.get("skeleton", "")) for family in families}
    seen_expressions = {str(family.get("expression", "")).strip() for family in families}
    chassis_counts: dict[str, int] = {}
    for family in families:
        fields = family.get("fields", []) or []
        chassis = str(family.get("chassis") or _chassis_signature(str(family.get("expression", "")), fields))
        chassis_counts[chassis] = chassis_counts.get(chassis, 0) + 1

    for row in selected_fields:
        field_id = str(row.get("field_id", ""))
        if not field_id:
            continue
        for template in _template_candidates(row):
            skeleton = template["skeleton"]
            expression = template["expression"].strip()
            chassis = _chassis_signature(expression, [field_id])
            if skeleton in blocked_skeletons or skeleton in seen_skeletons or expression in seen_expressions:
                continue
            if chassis in blocked_chassis or chassis_counts.get(chassis, 0) >= MAX_FAMILIES_PER_CHASSIS:
                continue
            if simulation_cache is not None and scan_settings is not None:
                if _simulation_cache_key(expression, scan_settings) in simulation_cache:
                    continue
            families.append(
                {
                    "family_id": f"g{len(families) + 1}",
                    "dataset": dataset,
                    "family": template["family"].title(),
                    "skeleton": skeleton,
                    "signal_idea": template["signal_idea"],
                    "fields": [field_id],
                    "expression": expression,
                    "archetype": archetype,
                    "self_corr_risk": "medium",
                    "chassis": chassis,
                    "reason": reason,
                }
            )
            seen_skeletons.add(skeleton)
            seen_expressions.add(expression)
            chassis_counts[chassis] = chassis_counts.get(chassis, 0) + 1
            if field_id not in kept_fields:
                kept_fields.append(field_id)
            if len(families) >= target_count:
                return _renumber_families(families)
    return _renumber_families(families)


def _apply_chassis_budget(families: list[dict[str, Any]], *, max_per_chassis: int = MAX_FAMILIES_PER_CHASSIS) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for family in families:
        fields = family.get("fields", []) or []
        chassis = str(family.get("chassis") or _chassis_signature(str(family.get("expression", "")), fields))
        if counts.get(chassis, 0) >= max_per_chassis:
            continue
        family["chassis"] = chassis
        counts[chassis] = counts.get(chassis, 0) + 1
        kept.append(family)
    return _renumber_families(kept)


def _renumber_families(families: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, family in enumerate(families, start=1):
        family["family_id"] = f"g{index}"
    return families


def _scan_output_complete(config_path: Path, output_path: Path) -> bool:
    config = _read_json(config_path, {})
    rows = _read_json(output_path, [])
    candidates = config.get("candidates", [])
    return bool(candidates) and isinstance(rows, list) and len(rows) >= len(candidates)


def _find_latest_stage_entry(
    state: dict[str, Any],
    stage: str,
    iteration: int | None = None,
) -> dict[str, Any] | None:
    matches = []
    for entry in state.get("completed_stages", []):
        if entry.get("stage") != stage:
            continue
        if iteration is not None and int(entry.get("iteration", -1)) != iteration:
            continue
        matches.append(entry)
    return matches[-1] if matches else None


@dataclass
class StageResult:
    advanced: bool
    summary: str


class ContinuousAlphaScheduler:
    def __init__(
        self,
        workspace_root: Path,
        state_path: Path,
        *,
        dry_run: bool = False,
        workflow_config: Path | Mapping[str, Any] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.state_path = Path(state_path).resolve()
        self.state = _load_state(self.state_path)
        self._baseline_state = deepcopy(self.state)
        self.run_tag = str(self.state.get("run_tag") or self.state_path.parent.name)
        layout = ProjectLayout.from_state_path(
            self.workspace_root,
            self.state_path,
            self.run_tag,
        )
        self.run_dir = layout.run_dir
        self.scan_config_dir = layout.scan_config_dir
        self.dry_run = dry_run
        self.workflow_config_metadata: dict[str, Any] = {}
        self._safe_workflow_config_reference: str | None = None
        self.workflow_config = self._load_workflow_config(workflow_config)
        self.resolved_llm_provider: Any | None = None
        self.llm_provider: LLMProvider | None = None
        self.llm_initialization_diagnostic: dict[str, Any] | None = None
        self._initialize_llm_provider()

    def _initialize_llm_provider(self) -> None:
        metadata_resolved: Any | None = None
        initialization_error: LLMProviderError | None = None
        try:
            metadata_resolved = resolve_llm_provider_config(
                self.workflow_config,
                require_credentials=False,
            )
            self.resolved_llm_provider = metadata_resolved
            self.llm_provider = create_llm_provider(
                metadata_resolved,
                workspace_root=self.workspace_root,
            )
        except LLMProviderError as exc:
            if self.resolved_llm_provider is None:
                self.resolved_llm_provider = metadata_resolved
            self.llm_provider = None
            self.llm_initialization_diagnostic = invalid_llm_config_diagnostic(exc)
            initialization_error = exc

        if self.resolved_llm_provider is not None:
            identity = llm_config_identity(self.resolved_llm_provider)
        else:
            if initialization_error is None:
                initialization_error = LLMProviderError(
                    code="invalid_configuration",
                    message="LLM provider configuration could not be resolved.",
                )
            identity = invalid_llm_config_identity(
                self.workflow_config,
                initialization_error,
            )
        self.llm_provider_config_digest = str(identity["config_digest"])
        self.llm_provider_metadata = dict(identity)
        if self.llm_initialization_diagnostic is not None:
            self.llm_provider_metadata["initialization_diagnostic"] = (
                self.llm_initialization_diagnostic
            )
        self.state["llm_provider"] = self.llm_provider_metadata
        self._persist_initialization_state()

    def _load_workflow_config(
        self,
        workflow_config: Path | Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        explicit = workflow_config is not None
        source: Path | Mapping[str, Any] | None = workflow_config
        state_source = self.state.get("workflow_config_path") or self.state.get("workflow_config")
        trusted_state_path: Path | None = None
        if state_source and not isinstance(state_source, Mapping):
            raw_state_path = Path(str(state_source))
            if raw_state_path.is_absolute() or ".." in raw_state_path.parts:
                raise ValueError("untrusted workflow config path in iteration_state")
            trusted_state_path = (self.workspace_root / raw_state_path).resolve()
            try:
                trusted_state_path.relative_to(self.workspace_root.resolve())
            except ValueError as exc:
                raise ValueError(
                    "untrusted workflow config path in iteration_state"
                ) from exc
        if isinstance(source, Path) and state_source and not isinstance(
            state_source, Mapping
        ):
            explicit_path = (
                source if source.is_absolute() else self.workspace_root / source
            ).resolve()
            state_path = trusted_state_path
            if explicit_path != state_path:
                raise ValueError(
                    "Explicit workflow config conflicts with iteration_state "
                    f"workflow_config_path: {explicit_path} != {state_path}"
                )
        if source is None:
            if isinstance(state_source, Mapping):
                source = state_source
            else:
                source = trusted_state_path
        if source is None:
            self.workflow_config_metadata = {
                "source": "legacy",
                "path_status": "disabled_no_config",
            }
            return {}
        if isinstance(source, Mapping):
            self.workflow_config_metadata = {
                "source": "explicit" if explicit else "state_legacy_embedded",
                "path_status": "inline_not_persisted",
            }
            return deepcopy(dict(source))
        path = (source if source.is_absolute() else self.workspace_root / source).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Workflow config does not exist: {path}")
        try:
            config_reference = path.relative_to(self.workspace_root.resolve()).as_posix()
        except ValueError:
            if not explicit:
                raise ValueError("untrusted workflow config path in iteration_state")
            self.workflow_config_metadata = {
                "source": "explicit",
                "path_status": "external_not_persisted",
            }
        else:
            self._safe_workflow_config_reference = config_reference
            self.workflow_config_metadata = {
                "source": "explicit" if explicit else "state",
                "path_status": "workspace_relative",
                "path": config_reference,
            }
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Workflow config must be a JSON object.")
        return payload

    def _create_template_generator(self) -> LLMTemplateGenerator:
        return LLMTemplateGenerator(provider=self.llm_provider)

    def _initialization_diagnostic(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "config_digest": self.llm_provider_config_digest,
            "evaluated_config_digest": self.llm_provider_config_digest,
            "phase": "initialization",
        }
        if self.llm_initialization_diagnostic is not None:
            payload.update(
                {"status": "error", "error": self.llm_initialization_diagnostic}
            )
        elif self.llm_provider is None:
            payload["status"] = "offline"
        else:
            payload["status"] = "not_evaluated"
        return payload

    def _persist_initialization_state(self) -> None:
        diagnostic = self._initialization_diagnostic()
        self.state["llm_provider"] = self.llm_provider_metadata
        self.state["workflow_config_metadata"] = self.workflow_config_metadata
        self.state["llm_template_diagnostic"] = diagnostic
        if self._safe_workflow_config_reference is not None:
            self.state["workflow_config_path"] = self._safe_workflow_config_reference
        if self.dry_run:
            return
        updates: dict[str, Any] = {
            "llm_provider": self.llm_provider_metadata,
            "workflow_config_metadata": self.workflow_config_metadata,
            "llm_template_diagnostic": diagnostic,
        }
        if self._safe_workflow_config_reference is not None:
            updates["workflow_config_path"] = self._safe_workflow_config_reference
        persisted = locked_atomic_json_merge(self.state_path, updates)
        atomic_write_json(self.run_dir / "llm_template_diagnostic.json", diagnostic)
        self.state = deepcopy(persisted)
        self._baseline_state = deepcopy(persisted)

    def _write_template_diagnostic(
        self,
        generator: LLMTemplateGenerator | None,
        *,
        unexpected_error: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "config_digest": self.llm_provider_config_digest,
            "evaluated_config_digest": self.llm_provider_config_digest,
        }
        if self.llm_initialization_diagnostic is not None:
            payload.update(
                {
                    "status": "error",
                    "phase": "initialization",
                    "error": self.llm_initialization_diagnostic,
                }
            )
        elif unexpected_error:
            payload.update(
                {
                    "status": "error",
                    "phase": "generation",
                    "error": {
                        "code": "provider_error",
                        "message": "Candidate generation failed unexpectedly.",
                        "retryable": False,
                    },
                }
            )
        elif generator is not None and generator.last_diagnostic is not None:
            payload.update(
                {
                    "status": "error",
                    "phase": "generation",
                    "error": generator.last_diagnostic,
                }
            )
        elif self.llm_provider is None:
            payload.update({"status": "offline", "phase": "generation"})
        else:
            payload.update({"status": "success", "phase": "generation"})
        self.state["llm_template_diagnostic"] = payload
        if self.dry_run:
            return
        atomic_write_json(self.run_dir / "llm_template_diagnostic.json", payload)
        persisted = locked_atomic_json_merge(
            self.state_path,
            {"llm_template_diagnostic": payload},
        )
        self.state = deepcopy(persisted)
        self._baseline_state = deepcopy(persisted)

    def save(self) -> None:
        updates = {
            key: deepcopy(value)
            for key, value in self.state.items()
            if key not in self._baseline_state
            or value != self._baseline_state[key]
        }
        delete_keys = tuple(
            key for key in self._baseline_state if key not in self.state
        )
        persisted = locked_atomic_json_merge(
            self.state_path,
            updates,
            delete_keys=delete_keys,
        )
        self.state = deepcopy(persisted)
        self._baseline_state = deepcopy(persisted)

    def step(self) -> StageResult:
        workflow_status = str(self.state.get("workflow_status", "active") or "active").lower()
        if workflow_status in {"stopped", "paused"}:
            return StageResult(False, f"Workflow {self.run_tag} is {workflow_status}; no stages executed.")
        stage = str(self.state.get("current_stage", "family_generation"))
        if stage == "family_generation":
            return self._family_generation_stage()
        if stage in {"scan", "scan-retry"}:
            return self._scan_stage(stage)
        if stage == "triage":
            return self._triage_stage()
        if stage == "optimization":
            return self._optimization_stage()
        if stage == "grading":
            return self._grading_stage()
        raise ValueError(f"Unsupported workflow stage: {stage}")

    def run(self, *, max_stages: int = 0, continuous: bool = False) -> list[str]:
        messages: list[str] = []
        stages_run = 0
        start_iteration = int(self.state.get("current_iteration", 1) or 1)

        while True:
            result = self.step()
            messages.append(result.summary)
            stages_run += 1
            if max_stages and stages_run >= max_stages:
                break
            if not result.advanced:
                break
            if not continuous:
                current_iteration = int(self.state.get("current_iteration", 1) or 1)
                current_stage = str(self.state.get("current_stage", "family_generation"))
                if current_iteration > start_iteration and current_stage == "family_generation":
                    break
        return messages

    def _family_generation_stage(self) -> StageResult:
        iteration = int(self.state.get("current_iteration", 1) or 1)
        blocked = _read_json(self.run_dir / "alpha_skeleton_blocklist.json", [])
        blocked_skeletons = {str(item.get("skeleton", "")) for item in blocked}
        chassis_blocklist = _load_chassis_blocklist(self.run_dir)
        field_scoreboard = _read_json(self.run_dir / "field_scoreboard.json", {})
        dataset_scoreboard = _read_json(self.run_dir / "dataset_scoreboard.json", {})

        quadrant_rows = _read_json(self.workspace_root / ".local" / "data" / "field_quadrant_analysis.json", [])
        by_dataset: dict[tuple[int, str], list[dict[str, Any]]] = {}
        excluded: list[dict[str, str]] = []

        for row in quadrant_rows:
            quadrant = str(row.get("quadrant", ""))
            if quadrant.startswith("Q1"):
                priority = 0
            elif quadrant.startswith("Q3"):
                priority = 1
            else:
                continue

            is_structural, reason = _is_structural_field(row)
            if is_structural:
                excluded.append({"field": str(row.get("field_id", "")), "reason": reason})
                continue

            incompatible, reason = _is_operator_incompatible(row)
            if incompatible:
                excluded.append({"field": str(row.get("field_id", "")), "reason": reason})
                continue

            templates = _template_candidates(row)
            usable_templates = [item for item in templates if item["skeleton"] not in blocked_skeletons]
            if not usable_templates:
                excluded.append(
                    {
                        "field": str(row.get("field_id", "")),
                        "reason": "All scalar templates for this field are already blocked unchanged.",
                    }
                )
                continue

            dataset = str(row.get("dataset_id", ""))
            by_dataset.setdefault((priority, dataset), []).append(row)

        preferences = self.state.get("dataset_preferences") or {}
        forced_dataset = str(preferences.get("forced_dataset", "")).strip()
        excluded_datasets = {
            str(item).strip()
            for item in preferences.get("exclude_datasets", [])
            if str(item).strip()
        }
        if excluded_datasets:
            by_dataset = {
                key: rows for key, rows in by_dataset.items()
                if key[1] not in excluded_datasets
            }

        if not by_dataset:
            return StageResult(False, "No remaining fields could be generated into unblocked families.")

        def dataset_score(item: tuple[tuple[int, str], list[dict[str, Any]]]) -> tuple[float, int, float]:
            (priority, _dataset), rows = item
            dataset_stats = dataset_scoreboard.get(_dataset, {}) if isinstance(dataset_scoreboard, dict) else {}
            score = _dataset_bandit_score(
                priority=priority,
                dataset=_dataset,
                rows=rows,
                dataset_stats=dataset_stats,
                iteration=iteration,
                blocked_skeletons=blocked_skeletons,
            )
            if _recent_dataset_dead_zone(self.workspace_root, self.state, _dataset):
                return (score[0], score[1] - 500, score[2])
            return score

        dataset_score_table = [
            {
                "dataset": key[1],
                "priority": key[0],
                "score": dataset_score((key, rows))[1],
                "field_count": len(rows),
                "cooldown": _dataset_is_in_cooldown(dataset_scoreboard.get(key[1], {}), iteration=iteration)
                or _recent_dataset_dead_zone(self.workspace_root, self.state, key[1])
                if isinstance(dataset_scoreboard, dict) else False,
                "recent_dead_zone": _recent_dataset_dead_zone(self.workspace_root, self.state, key[1]),
            }
            for key, rows in by_dataset.items()
        ]

        if forced_dataset:
            forced_matches = [
                item for item in by_dataset.items()
                if item[0][1] == forced_dataset
            ]
            if not forced_matches:
                return StageResult(False, f"Forced dataset {forced_dataset} has no remaining unblocked quadrant fields.")
            (_, dataset), selected_fields = max(forced_matches, key=dataset_score)
        else:
            (_, dataset), selected_fields = max(by_dataset.items(), key=dataset_score)
        selected_fields.sort(
            key=lambda row: (
                -float((field_scoreboard.get(str(row.get("field_id", "")), {}) or {}).get("pass_count", 0) or 0),
                float((field_scoreboard.get(str(row.get("field_id", "")), {}) or {}).get("self_corr_fail_count", 0) or 0),
                -float(row.get("research_value_score", 0.0) or 0.0),
                float(row.get("crowding_score", 0.0) or 0.0),
                float(row.get("alpha_count", 0.0) or 0.0),
            )
        )

        # ------------------------------------------------------------------
        # LLM-driven family generation (primary)
        # ------------------------------------------------------------------
        families: list[dict[str, Any]] = []

        kept_fields: list[str] = []

        if not self.dry_run:
            try:
                generator = self._create_template_generator()
                try:
                    families = generator.generate(
                        workspace_root=self.workspace_root,
                        run_dir=self.run_dir,
                        selected_dataset=dataset,
                        selected_fields=selected_fields[:30],
                        max_families=TARGET_SCAN_CANDIDATES,
                    )
                except Exception:
                    self._write_template_diagnostic(generator, unexpected_error=True)
                    raise
                self._write_template_diagnostic(generator)
                provider_label = (
                    f"{self.llm_provider.provider_id}/{self.llm_provider.model}"
                    if self.llm_provider is not None
                    else "deterministic"
                )
                print(f"[LLM] Generated {len(families)} families via {provider_label}")
                # Extract kept fields from LLM-generated families
                for fam in families:
                    for f in fam.get("fields", []):
                        if f not in kept_fields:
                            kept_fields.append(f)
            except Exception:
                print("[LLM] Generation failed, falling back to rule-based generation.")
                families = []

        # ------------------------------------------------------------------
        # Fallback: rule-based template generation
        # ------------------------------------------------------------------
        if not families:
            for row in selected_fields:
                field_id = str(row.get("field_id", ""))
                field_templates = _template_candidates(row)
                added = 0
                for template in field_templates:
                    if template["skeleton"] in blocked_skeletons:
                        continue
                    families.append(
                        {
                            "family_id": f"g{len(families) + 1}",
                            "dataset": dataset,
                            "family": template["family"].title(),
                            "skeleton": template["skeleton"],
                            "signal_idea": template["signal_idea"],
                            "fields": [field_id],
                            "expression": template["expression"],
                            "archetype": "rule_based",
                            "self_corr_risk": "medium",
                            "chassis": _chassis_signature(template["expression"], [field_id]),
                            "reason": "Auto-generated from the quadrant field pool while respecting the skeleton blocklist.",
                        }
                    )
                    added += 1
                    if len(families) >= 10:
                        break
                if added:
                    kept_fields.append(field_id)
                if len(families) >= 10 or len(kept_fields) >= 4:
                    break

        families = _apply_chassis_budget(families)
        if len(families) < TARGET_SCAN_CANDIDATES:
            families = _supplement_rule_based_families(
                families,
                selected_fields=selected_fields,
                dataset=dataset,
                blocked_skeletons=blocked_skeletons,
                blocked_chassis=chassis_blocklist,
                kept_fields=kept_fields,
                target_count=TARGET_SCAN_CANDIDATES,
                archetype="throughput_seed",
                reason="Supplemental winner-neighborhood scan candidate added to keep the concurrency-3 pipeline saturated.",
            )

        if not families:
            return StageResult(False, "No candidate families were generated after blocklist filtering.")

        filtered_families: list[dict[str, Any]] = []
        seen_high_risk_chassis: set[str] = set()
        for family in families:
            fields = family.get("fields", []) or []
            chassis = str(family.get("chassis") or _chassis_signature(family.get("expression", ""), fields))
            if chassis in chassis_blocklist:
                continue
            if family.get("self_corr_risk") == "high" and chassis in seen_high_risk_chassis:
                continue
            family["chassis"] = chassis
            filtered_families.append(family)
            seen_high_risk_chassis.add(chassis)
        families = filtered_families

        if not families:
            return StageResult(False, "No candidate families remained after submitted chassis filtering.")

        scan_settings = {
            **DEFAULT_SCAN_SETTINGS,
            "region": self.state.get("region", "USA"),
            "delay": self.state.get("delay", 1),
            "universe": self.state.get("universe", "TOP3000"),
        }
        simulation_cache = _read_json(self.run_dir / "simulation_cache.json", {})
        cache_skipped_count = 0
        if isinstance(simulation_cache, dict):
            uncached_families = [
                family for family in families
                if _simulation_cache_key(str(family.get("expression", "")), scan_settings) not in simulation_cache
            ]
            if uncached_families:
                cache_skipped_count = len(families) - len(uncached_families)
                families = uncached_families
        families = _apply_chassis_budget(families)
        if isinstance(simulation_cache, dict) and len(families) < TARGET_SCAN_CANDIDATES:
            families = _supplement_rule_based_families(
                families,
                selected_fields=selected_fields,
                dataset=dataset,
                blocked_skeletons=blocked_skeletons,
                blocked_chassis=chassis_blocklist,
                kept_fields=kept_fields,
                target_count=TARGET_SCAN_CANDIDATES,
                archetype="cache_refill_seed",
                reason="Supplemental candidate added after simulation-cache filtering to keep the scan batch full.",
                simulation_cache=simulation_cache,
                scan_settings=scan_settings,
            )

        quadrant_name = str(selected_fields[0].get("quadrant", "Q1"))
        chosen_bucket = f"{quadrant_name} {dataset} autogenerated branch"

        field_pool_path = self.run_dir / "field_pool.json"
        field_pool_round_path = self.run_dir / f"field_pool_round{iteration}.json"
        candidate_families_path = self.run_dir / "candidate_families.json"
        candidate_families_round_path = self.run_dir / f"candidate_families_round{iteration}.json"
        candidate_families_md_path = self.run_dir / "candidate_families.md"
        scan_config_path = self.scan_config_dir / f"scan_config_round{iteration}.json"
        scan_output_path = self.run_dir / f"scan_round{iteration}_iteration{iteration}.json"

        field_pool = {
            "run_tag": self.run_tag,
            "chosen_bucket": chosen_bucket,
            "source_report": ".local/data/field_quadrant_report_20260423.md",
            "source_analysis": ".local/data/field_quadrant_analysis.json",
            "kept_fields": {dataset: kept_fields},
            "excluded_fields": excluded,
            "dataset_score_table": sorted(dataset_score_table, key=lambda item: item["score"], reverse=True),
            "diversity_suggestions": _diversity_suggestions(
                selected_dataset=dataset,
                selected_fields=selected_fields[:20],
                field_scoreboard=field_scoreboard,
                dataset_scoreboard=dataset_scoreboard,
            ),
            "structural_jump_required": any(item.get("recent_dead_zone") for item in dataset_score_table),
            "blocked_skeletons_respected": sorted(blocked_skeletons),
            "blocked_chassis_respected": sorted(chassis_blocklist),
        }
        scan_config = {
            "output": _relpath(scan_output_path, self.workspace_root),
            "continue_on_pass": True,
            "max_concurrency": SCAN_MAX_CONCURRENCY,
            "settings": scan_settings,
            "candidates": [
                {
                    "expression": family["expression"],
                    "note": f"iteration{iteration} {family['skeleton']}",
                }
                for family in families
            ],
        }
        markdown = "# Candidate Families\n\n" + "\n".join(
            f"- {family['skeleton']}: {family['expression']}" for family in families
        ) + "\n"

        if not self.dry_run:
            _write_json(field_pool_path, field_pool)
            _write_json(field_pool_round_path, field_pool)
            _write_json(candidate_families_path, families)
            _write_json(candidate_families_round_path, families)
            _write_text(candidate_families_md_path, markdown)
            _write_json(scan_config_path, scan_config)
            trace_path, trace_md_path = _append_decision_trace(
                self.run_dir,
                iteration,
                "family_generation",
                {
                    "summary": f"Selected {dataset} from {len(by_dataset)} dataset arms and prepared {len(families)} candidates.",
                    "chosen_dataset": dataset,
                    "dataset_score_table": sorted(dataset_score_table, key=lambda item: item["score"], reverse=True)[:10],
                    "diversity_suggestions": field_pool["diversity_suggestions"],
                    "excluded_field_count": len(excluded),
                    "cache_skipped_count": cache_skipped_count,
                },
            )
        else:
            trace_path = self.run_dir / f"decision_trace_round{iteration}.json"
            trace_md_path = self.run_dir / f"decision_trace_round{iteration}.md"

        _append_completed_stage(
            self.state,
            {
                "iteration": iteration,
                "stage": "family_generation",
                "status": "prepared",
                "chosen_bucket": chosen_bucket,
                "field_pool": _relpath(field_pool_path, self.workspace_root),
                "field_pool_round": _relpath(field_pool_round_path, self.workspace_root),
                "candidate_families": _relpath(candidate_families_path, self.workspace_root),
                "candidate_families_round": _relpath(candidate_families_round_path, self.workspace_root),
                "scan_config": _relpath(scan_config_path, self.workspace_root),
                "decision_trace": _relpath(trace_path, self.workspace_root),
                "decision_trace_md": _relpath(trace_md_path, self.workspace_root),
                "planned_scan_count": len(families),
            },
        )
        self.state["current_stage"] = "scan"
        self.state.setdefault("active_stage_inputs", {})["next_scan_config"] = _relpath(scan_config_path, self.workspace_root)
        self.state.setdefault("active_stage_inputs", {})["next_scan_output"] = _relpath(scan_output_path, self.workspace_root)
        self.state.setdefault("active_stage_inputs", {})["planned_scan_count"] = len(families)
        self.state["recommended_next_step"] = f"Run scan for iteration {iteration} on the newly generated {dataset} families."
        if not self.dry_run:
            self.save()
        return StageResult(True, f"Prepared family generation for iteration {iteration} with {len(families)} candidates from {dataset}.")

    def _run_scan_command(self, config_path: Path) -> None:
        command = [
            sys.executable,
            str(self.workspace_root / "run_scan.py"),
            "--config",
            str(config_path),
            "--continue-on-pass",
            "--max-concurrency",
            str(SCAN_MAX_CONCURRENCY),
        ]
        result = subprocess.run(command, cwd=self.workspace_root, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"run_scan.py exited with code {result.returncode}")

    def _live_recheck_pass_rows(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self.dry_run or os.getenv("WQB_DISABLE_LIVE_RECHECK") == "1":
            return rows, []
        if not rows:
            return [], []
        if not (self.workspace_root / ".env").exists() and os.getenv("WQB_ENABLE_LIVE_RECHECK") != "1":
            return rows, []
        try:
            client = WQBClient.from_config()
        except ValueError as exc:
            if "WQB_EMAIL" in str(exc) or "WQB_PASSWORD" in str(exc):
                return rows, []
            return [dict(row, live_check_status="skipped", live_check_error=str(exc)) for row in rows], []
        except Exception as exc:
            return [dict(row, live_check_status="skipped", live_check_error=str(exc)) for row in rows], []

        clean: list[dict[str, Any]] = []
        held: list[dict[str, Any]] = []
        for row in rows:
            alpha_id = str(row.get("alpha_id", "")).strip()
            if not alpha_id:
                held.append(dict(row, live_check_status="missing_alpha_id"))
                continue
            try:
                checks = client.get_alpha_checks(alpha_id)
                detail = None
                if not checks:
                    detail = client.get_alpha(alpha_id)
                    if detail.http_status == 429:
                        held.append(dict(row, live_check_status="throttled"))
                        continue
                    if detail.http_status is not None and detail.http_status >= 400:
                        held.append(dict(row, live_check_status=f"detail_http_{detail.http_status}"))
                        continue
                    checks = detail.checks
                checked = dict(row)
                if checks:
                    checked["checks"] = [check.to_dict() for check in checks]
                if detail is not None and detail.metrics:
                    checked.update({k: v for k, v in detail.metrics.items() if v is not None})
                checked["live_check_status"] = "checked"
                checked.update(_normalized_check_details(checked))
                if _pass_row(checked):
                    clean.append(checked)
                else:
                    held.append(checked)
            except Exception as exc:
                held.append(dict(row, live_check_status="error", live_check_error=str(exc)))
        return clean, held

    def _scan_stage(self, stage_name: str) -> StageResult:
        iteration = int(self.state.get("current_iteration", 1) or 1)
        active = self.state.setdefault("active_stage_inputs", {})
        config_rel = active.get("next_scan_config") or active.get("latest_scan_config")
        output_rel = active.get("next_scan_output") or active.get("latest_scan_output")
        if not config_rel or not output_rel:
            entry = _find_latest_stage_entry(self.state, "family_generation", iteration)
            if not entry:
                return StageResult(False, "Scan stage could not find a prepared scan config.")
            config_rel = entry.get("scan_config")
            output_rel = entry.get("scan_output") or active.get("next_scan_output")
        config_path = self.workspace_root / str(config_rel)
        output_path = self.workspace_root / str(output_rel)
        config = _read_json(config_path, {})
        candidate_count = len(config.get("candidates", []))

        if not self.dry_run and not _scan_output_complete(config_path, output_path):
            self._run_scan_command(config_path)

        if not self.dry_run and not _scan_output_complete(config_path, output_path):
            active["scan_status"] = "waiting_for_completion"
            self.state["current_stage"] = stage_name
            self.state["recommended_next_step"] = (
                f"Resume {stage_name} for iteration {iteration} after scan output reaches {candidate_count} rows."
            )
            if not self.dry_run:
                self.save()
            return StageResult(False, f"Scan output for iteration {iteration} is still incomplete ({len(_read_json(output_path, []))}/{candidate_count} rows).")

        rows = _read_json(output_path, [])
        if not self.dry_run:
            _update_simulation_cache(self.run_dir, rows, default_settings=config.get("settings", {}))
        valid_result_count = sum(1 for row in rows if row.get("alpha_id"))
        terminal_error_count = sum(1 for row in rows if row.get("error"))

        _append_completed_stage(
            self.state,
            {
                "iteration": iteration,
                "stage": stage_name,
                "status": "completed",
                "scan_config": str(config_rel),
                "scan_output": str(output_rel),
                "planned_scan_count": candidate_count,
                "reviewed_row_count": len(rows),
                "valid_result_count": valid_result_count,
                "terminal_error_count": terminal_error_count,
            },
        )
        active["latest_scan_config"] = str(config_rel)
        active["latest_scan_output"] = str(output_rel)
        active["scan_status"] = "completed"
        self.state["current_stage"] = "triage"
        self.state["recommended_next_step"] = f"Triage the completed scan output for iteration {iteration}."
        if not self.dry_run:
            self.save()
        return StageResult(True, f"Completed scan stage for iteration {iteration} with {len(rows)} reviewed rows.")

    def _triage_stage(self) -> StageResult:
        iteration = int(self.state.get("current_iteration", 1) or 1)
        active = self.state.setdefault("active_stage_inputs", {})
        scan_rel = active.get("latest_scan_output")
        if not scan_rel:
            latest_scan = _find_latest_stage_entry(self.state, "scan", iteration) or _find_latest_stage_entry(self.state, "scan-retry", iteration)
            if latest_scan:
                scan_rel = latest_scan.get("scan_output")
        if not scan_rel:
            return StageResult(False, "Triage stage could not find the latest scan output.")

        scan_path = self.workspace_root / str(scan_rel)
        rows = _read_json(scan_path, [])
        family_map = _load_candidate_family_map(self.run_dir)
        optimization_map = _load_optimization_candidate_map(self.run_dir)

        direct_submit: list[dict[str, Any]] = []
        optimize_next: list[dict[str, Any]] = []
        low_value_groups: dict[str, list[dict[str, Any]]] = {}

        for row in rows:
            context = _infer_row_context(row, family_map, optimization_map)
            enriched = {**row, **context}
            enriched["chassis"] = str(enriched.get("chassis") or _chassis_signature(enriched.get("expression", ""), enriched.get("fields", [])))
            enriched.update(_normalized_check_details(enriched))
            if _pass_row(row):
                direct_submit.append(enriched)
            elif _near_pass_row(row):
                optimize_next.append(enriched)
            else:
                low_value_groups.setdefault(context.get("skeleton", "unknown"), []).append(enriched)

        low_value_avoid_path = self.run_dir / "low_value_avoid.json"
        alpha_skeleton_blocklist_path = self.run_dir / "alpha_skeleton_blocklist.json"
        chassis_blocklist_path = self.run_dir / "chassis_blocklist.json"
        field_scoreboard_path = self.run_dir / "field_scoreboard.json"
        dataset_scoreboard_path = self.run_dir / "dataset_scoreboard.json"
        chassis_scoreboard_path = self.run_dir / "chassis_scoreboard.json"
        existing_low_value = _read_json(low_value_avoid_path, [])
        existing_blocklist = _read_json(alpha_skeleton_blocklist_path, [])
        existing_chassis_blocklist = _read_json(chassis_blocklist_path, [])
        field_scoreboard = _read_json(field_scoreboard_path, {})
        dataset_scoreboard = _read_json(dataset_scoreboard_path, {})
        chassis_scoreboard = _read_json(chassis_scoreboard_path, {})
        existing_blocked = {str(item.get("skeleton", "")) for item in existing_blocklist}
        existing_blocked_chassis = {str(item.get("chassis", "")) for item in existing_chassis_blocklist}
        optimize_skeletons = {str(item.get("skeleton", "")) for item in optimize_next}
        direct_skeletons = {str(item.get("skeleton", "")) for item in direct_submit}

        for row in direct_submit + optimize_next + [item for group in low_value_groups.values() for item in group]:
            failures = set(_submission_blockers(row.get("checks")))
            if row.get("error") or not row.get("alpha_id"):
                failures.add("TERMINAL_ERROR")
            passed = _pass_row(row)
            near_pass = _near_pass_row(row)
            dataset_key = str(row.get("dataset", "unknown") or "unknown")
            chassis_key = str(row.get("chassis") or _chassis_signature(row.get("expression", ""), row.get("fields", [])))
            _update_score_entry(_scoreboard_entry(dataset_scoreboard, dataset_key), passed=passed, near_pass=near_pass, failures=failures, iteration=iteration, metrics=row.get("metrics", {}))
            _update_score_entry(_scoreboard_entry(chassis_scoreboard, chassis_key), passed=passed, near_pass=near_pass, failures=failures, iteration=iteration, metrics=row.get("metrics", {}))
            for field_id in row.get("fields", []) or []:
                field_entry = _scoreboard_entry(field_scoreboard, str(field_id))
                field_entry.setdefault("dataset", dataset_key)
                field_entry.setdefault("cluster", _field_cluster(str(field_id)))
                _update_score_entry(field_entry, passed=passed, near_pass=near_pass, failures=failures, iteration=iteration, metrics=row.get("metrics", {}))

        existing_low_value_by_skeleton = {str(item.get("skeleton", "")) for item in existing_low_value}
        new_low_value: list[dict[str, Any]] = []
        new_blocked: list[dict[str, Any]] = []
        new_chassis_blocked: list[dict[str, Any]] = []
        for skeleton, grouped_rows in sorted(low_value_groups.items()):
            if skeleton in optimize_skeletons or skeleton in direct_skeletons:
                continue
            best = _best_row(grouped_rows) or grouped_rows[0]
            fail_text = ", ".join(_submission_blockers(best.get("checks"))) or "weak signal quality"
            reason = f"Iteration {iteration} remained low value for skeleton {skeleton}: {fail_text}."
            entry = {
                "dataset": best.get("dataset", ""),
                "family": best.get("family", skeleton),
                "skeleton": skeleton,
                "reason": reason,
                "avoid_mode": "do_not_regenerate_unchanged",
                "blocked_in_iteration": iteration,
                "representative_alphas": [row.get("alpha_id") for row in grouped_rows if row.get("alpha_id")],
            }
            if skeleton not in existing_low_value_by_skeleton:
                new_low_value.append(entry)
            if skeleton not in existing_blocked:
                new_blocked.append(
                    {
                        "skeleton": skeleton,
                        "dataset": best.get("dataset", ""),
                        "reason": reason,
                        "blocked_in_iteration": iteration,
                        "status": "blocked_unchanged",
                    }
                )
            best_chassis = str(best.get("chassis") or _chassis_signature(best.get("expression", ""), best.get("fields", [])))
            blockers = set(_submission_blockers(best.get("checks")))
            if best_chassis and best_chassis not in existing_blocked_chassis and blockers & {"SELF_CORRELATION", "UNITS"}:
                new_chassis_blocked.append(
                    {
                        "chassis": best_chassis,
                        "dataset": best.get("dataset", ""),
                        "reason": f"Iteration {iteration} blocked unchanged chassis due to {', '.join(sorted(blockers))}.",
                        "blocked_in_iteration": iteration,
                        "status": "blocked_submission_risk",
                    }
                )
                existing_blocked_chassis.add(best_chassis)

        scan_snapshot_path = self.run_dir / "scan_results_snapshot.json"
        direct_submit_path = self.run_dir / "direct_submit.json"
        optimize_next_path = self.run_dir / "optimize_next.json"
        triage_summary_path = self.run_dir / "triage_summary.md"
        low_value_rows = [item for group in low_value_groups.values() for item in group]
        route_counts: dict[str, int] = {}
        for row in direct_submit + optimize_next + low_value_rows:
            route = str(row.get("route_decision", "unknown"))
            route_counts[route] = route_counts.get(route, 0) + 1

        summary = [
            "# Triage Summary",
            "",
            f"- Source scan file: `{scan_rel}`",
            f"- Total scanned rows reviewed: {len(rows)}",
            f"- Direct-submit bucket: {len(direct_submit)}",
            f"- Optimize-next bucket: {len(optimize_next)}",
            f"- Low-value additions: {len(new_low_value)}",
            f"- Skeletons blocked unchanged total: {len(existing_blocklist) + len(new_blocked)}",
            "",
            "## Direct-submit",
        ]
        if direct_submit:
            summary.extend(
                f"- {item['skeleton']} ({item['alpha_id']}): Sharpe {item['metrics']['sharpe']:.2f}, Fitness {item['metrics']['fitness']:.2f}"
                for item in direct_submit
            )
        else:
            summary.append(f"- None in iteration {iteration}.")
        summary.extend(["", "## Optimize-next"])
        if optimize_next:
            summary.extend(
                f"- {item['skeleton']} ({item['alpha_id']}): route={item.get('route_decision', 'local_parameter_optimization')}"
                for item in optimize_next
            )
        else:
            summary.append(f"- None in iteration {iteration}.")
        summary.extend(["", "## Low-value families to avoid unchanged"])
        if new_low_value:
            summary.extend(f"- `{item['skeleton']}`" for item in new_low_value)
        else:
            summary.append("- None newly added.")

        if not self.dry_run:
            _write_json(scan_snapshot_path, rows)
            _write_json(direct_submit_path, direct_submit)
            _write_json(optimize_next_path, optimize_next)
            _write_json(low_value_avoid_path, _dedupe_entries_by_key(existing_low_value + new_low_value, "skeleton"))
            _write_json(alpha_skeleton_blocklist_path, existing_blocklist + new_blocked)
            _write_json(chassis_blocklist_path, _dedupe_entries_by_key(existing_chassis_blocklist + new_chassis_blocked, "chassis"))
            _write_json(field_scoreboard_path, field_scoreboard)
            _write_json(dataset_scoreboard_path, dataset_scoreboard)
            _write_json(chassis_scoreboard_path, chassis_scoreboard)
            _write_text(triage_summary_path, "\n".join(summary) + "\n")
            knowledge_counts = _update_knowledge_artifacts(
                self.run_dir,
                iteration=iteration,
                rows=direct_submit + optimize_next + low_value_rows,
                direct_submit=direct_submit,
                optimize_next=optimize_next,
                low_value_rows=low_value_rows,
            )
            trace_path, trace_md_path = _append_decision_trace(
                self.run_dir,
                iteration,
                "triage",
                {
                    "summary": f"Triaged {len(rows)} rows into direct={len(direct_submit)}, optimize={len(optimize_next)}, low={len(low_value_rows)}.",
                    "route_counts": route_counts,
                    "knowledge_counts": knowledge_counts,
                    "best_alpha": (_best_row(direct_submit + optimize_next + low_value_rows) or {}).get("alpha_id"),
                },
            )
        else:
            knowledge_counts = {"success_patterns": 0, "failure_pitfalls": 0, "field_insights": 0}
            trace_path = self.run_dir / f"decision_trace_round{iteration}.json"
            trace_md_path = self.run_dir / f"decision_trace_round{iteration}.md"

        _append_completed_stage(
            self.state,
            {
                "iteration": iteration,
                "stage": "triage",
                "status": "completed",
                "direct_submit_count": len(direct_submit),
                "optimize_next_count": len(optimize_next),
                "new_low_value_avoid_count": len(new_low_value),
                "new_blocked_skeleton_count": len(new_blocked),
                "new_blocked_chassis_count": len(new_chassis_blocked),
                "triage_summary": _relpath(triage_summary_path, self.workspace_root),
                "decision_trace": _relpath(trace_path, self.workspace_root),
                "decision_trace_md": _relpath(trace_md_path, self.workspace_root),
                "knowledge_counts": knowledge_counts,
            },
        )
        active["source_families"] = _relpath(self.run_dir / "candidate_families.json", self.workspace_root)
        if direct_submit or optimize_next:
            self.state["current_stage"] = "optimization"
            self.state["recommended_next_step"] = f"Optimize {len(direct_submit) + len(optimize_next)} candidates from iteration {iteration}."
        else:
            self.state["current_iteration"] = iteration + 1
            self.state["current_stage"] = "family_generation"
            self.state["recommended_next_step"] = f"Generate a fresh family set for iteration {iteration + 1}."
        if not self.dry_run:
            self.save()
        return StageResult(True, f"Triaged iteration {iteration}: {len(direct_submit)} direct-submit, {len(optimize_next)} optimize-next.")

    def _build_optimization_candidates(
        self,
        iteration: int,
        direct_submit: list[dict[str, Any]],
        optimize_next: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        base_rows = direct_submit + optimize_next
        for row in base_rows:
            skeleton = str(row.get("skeleton", "unknown"))
            dataset = str(row.get("dataset", ""))
            family = str(row.get("family", skeleton))
            fields = row.get("fields", []) or []
            chassis = str(row.get("chassis") or _chassis_signature(row.get("expression", ""), fields))
            base_settings = deepcopy(row.get("settings") or {})
            base_decay = int(base_settings.get("decay", DEFAULT_SCAN_SETTINGS["decay"]))
            proposed_decays = []
            fail_names = set(_fail_names(row.get("checks")))
            metrics = row.get("metrics") or {}
            sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
            turnover = float(metrics.get("turnover", 0.0) or 0.0)
            if "SELF_CORRELATION" in fail_names:
                candidates.extend(self._self_corr_escape_candidates(iteration, row, skeleton, dataset, family, fields, base_settings))
                continue
            self_corr = _self_corr_value(row)
            if self_corr is not None and self_corr >= EDGE_SELF_CORR_MIN:
                candidates.extend(self._self_corr_escape_candidates(iteration, row, skeleton, dataset, family, fields, base_settings)[:2])
            if "LOW_SHARPE" in fail_names and sharpe < NEAR_PASS_SHARPE:
                continue
            if "HIGH_TURNOVER" in fail_names or turnover > 0.55:
                for decay in (8, 10):
                    if decay != base_decay:
                        proposed_decays.append(decay)
            elif "LOW_FITNESS" in fail_names and sharpe >= PASS_SHARPE:
                for decay in (8, 4):
                    if decay != base_decay:
                        proposed_decays.append(decay)
            else:
                for decay in (4, 6, 8):
                    if decay != base_decay:
                        proposed_decays.append(decay)
            proposed_neutralizations: list[str] = []
            if "LOW_SUB_UNIVERSE_SHARPE" in fail_names:
                for neutralization in ("SECTOR", "INDUSTRY", "SUBINDUSTRY", "NONE"):
                    if neutralization != str(base_settings.get("neutralization", "MARKET")):
                        proposed_neutralizations.append(neutralization)
            proposed_truncations: list[float] = []
            if "HIGH_TURNOVER" in fail_names or turnover > 0.45 or (not fail_names and sharpe >= 1.58):
                base_truncation = float(base_settings.get("truncation", DEFAULT_SCAN_SETTINGS["truncation"]) or 0.05)
                for truncation in (0.08, 0.10):
                    if abs(truncation - base_truncation) > 1e-9:
                        proposed_truncations.append(truncation)

            for decay in proposed_decays[:2]:
                candidates.append(
                    {
                        "skeleton": skeleton,
                        "dataset": dataset,
                        "family": family,
                        "fields": fields,
                        "chassis": chassis,
                        "archetype": row.get("archetype", ""),
                        "base_alpha_id": row.get("alpha_id"),
                        "base_expression": row.get("expression"),
                        "base_settings": base_settings,
                        "expression": row.get("expression"),
                        "settings": {"decay": decay},
                        "note": f"optimization{iteration} {skeleton} decay{decay}",
                        "axis": "decay",
                    }
                )

            for neutralization in proposed_neutralizations[:2]:
                candidates.append(
                    {
                        "skeleton": skeleton,
                        "dataset": dataset,
                        "family": family,
                        "fields": fields,
                        "chassis": chassis,
                        "archetype": row.get("archetype", ""),
                        "base_alpha_id": row.get("alpha_id"),
                        "base_expression": row.get("expression"),
                        "base_settings": base_settings,
                        "expression": row.get("expression"),
                        "settings": {"neutralization": neutralization},
                        "note": f"optimization{iteration} {skeleton} {neutralization.lower()}",
                        "axis": "neutralization",
                    }
                )
            for truncation in proposed_truncations[:1]:
                candidates.append(
                    {
                        "skeleton": skeleton,
                        "dataset": dataset,
                        "family": family,
                        "fields": fields,
                        "chassis": chassis,
                        "archetype": row.get("archetype", ""),
                        "base_alpha_id": row.get("alpha_id"),
                        "base_expression": row.get("expression"),
                        "base_settings": base_settings,
                        "expression": row.get("expression"),
                        "settings": {"truncation": truncation},
                        "note": f"optimization{iteration} {skeleton} truncation{truncation}",
                        "axis": "truncation",
                    }
                )
        return candidates

    def _self_corr_escape_candidates(
        self,
        iteration: int,
        row: dict[str, Any],
        skeleton: str,
        dataset: str,
        family: str,
        fields: list[str],
        base_settings: dict[str, Any],
    ) -> list[dict[str, Any]]:
        expression = str(row.get("expression", ""))
        mutations: list[tuple[str, str]] = []
        if "rank(-returns) / 10" in expression:
            mutations.append(("weaker_reversal", expression.replace("rank(-returns) / 10", "rank(-returns) / 20")))
            mutations.append(("delta_reversal", expression.replace("rank(-returns) / 10", "rank(-ts_delta(close, 5)) / 20")))
        if "industry" in expression:
            mutations.append(("group_shift_sector", expression.replace("industry", "sector")))
        if expression.startswith("group_rank("):
            mutations.append(("group_neutralize", f"group_neutralize({expression}, subindustry)"))

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for axis, mutated in mutations:
            mutated = re.sub(r"\s+", " ", mutated.strip())
            if not mutated or mutated == expression or mutated in seen:
                continue
            seen.add(mutated)
            candidates.append(
                {
                    "skeleton": skeleton,
                    "dataset": dataset,
                    "family": family,
                    "fields": fields,
                    "chassis": _chassis_signature(mutated, fields),
                    "archetype": "self-corr-escape",
                    "base_alpha_id": row.get("alpha_id"),
                    "base_expression": expression,
                    "base_settings": base_settings,
                    "expression": mutated,
                    "settings": {},
                    "note": f"optimization{iteration} {skeleton} {axis}",
                    "axis": "self_corr_escape",
                }
            )
            if len(candidates) >= 3:
                break
        return candidates

    def _optimization_stage(self) -> StageResult:
        iteration = int(self.state.get("current_iteration", 1) or 1)
        direct_submit = _read_json(self.run_dir / "direct_submit.json", [])
        optimize_next = _read_json(self.run_dir / "optimize_next.json", [])
        optimization_round = sum(1 for entry in self.state.get("completed_stages", []) if entry.get("stage") == "optimization") + 1
        candidates = self._build_optimization_candidates(optimization_round, direct_submit, optimize_next)
        base_settings = {
            **DEFAULT_SCAN_SETTINGS,
            "region": self.state.get("region", "USA"),
            "delay": self.state.get("delay", 1),
            "universe": self.state.get("universe", "TOP3000"),
        }
        simulation_cache = _read_json(self.run_dir / "simulation_cache.json", {})
        if isinstance(simulation_cache, dict):
            candidates = [
                item for item in candidates
                if _simulation_cache_key(str(item.get("expression", "")), {**base_settings, **(item.get("settings") or {})}) not in simulation_cache
            ]

        optimization_candidates_path = self.run_dir / "optimization_candidates.json"
        optimization_config_path = self.scan_config_dir / f"optimization_round{optimization_round}.json"
        optimization_output_path = self.run_dir / f"optimization_round{optimization_round}.json"
        optimization_summary_path = self.run_dir / "optimization_summary.md"

        if not candidates:
            _append_completed_stage(
                self.state,
                {
                    "iteration": iteration,
                    "stage": "optimization",
                    "status": "completed_no_candidates",
                    "optimization_config": _relpath(optimization_config_path, self.workspace_root),
                    "optimization_output": _relpath(optimization_output_path, self.workspace_root),
                    "planned_candidate_count": 0,
                },
            )
            self.state["current_stage"] = "grading"
            if not self.dry_run:
                self.save()
            return StageResult(True, f"Optimization stage found no candidates for iteration {iteration}; moving to grading.")

        config = {
            "output": _relpath(optimization_output_path, self.workspace_root),
            "continue_on_pass": True,
            "settings": base_settings,
            "candidates": [
                {
                    "expression": item["expression"],
                    "settings": item["settings"],
                    "note": item["note"],
                }
                for item in candidates
            ],
        }

        if not self.dry_run:
            _write_json(optimization_candidates_path, candidates)
            _write_json(optimization_config_path, config)
            if not _scan_output_complete(optimization_config_path, optimization_output_path):
                self._run_scan_command(optimization_config_path)

        if not self.dry_run and not _scan_output_complete(optimization_config_path, optimization_output_path):
            active = self.state.setdefault("active_stage_inputs", {})
            active["optimization_config"] = _relpath(optimization_config_path, self.workspace_root)
            active["optimization_output"] = _relpath(optimization_output_path, self.workspace_root)
            active["optimization_status"] = "waiting_for_completion"
            self.state["current_stage"] = "optimization"
            self.state["recommended_next_step"] = (
                f"Resume optimization for iteration {iteration} after output reaches {len(candidates)} rows."
            )
            if not self.dry_run:
                self.save()
            return StageResult(
                False,
                f"Optimization output for iteration {iteration} is still incomplete ({len(_read_json(optimization_output_path, []))}/{len(candidates)} rows).",
            )

        result_rows = _read_json(optimization_output_path, [])
        if not self.dry_run:
            _update_simulation_cache(self.run_dir, result_rows, default_settings=base_settings)
        result_map = _load_optimization_candidate_map(self.run_dir)

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in result_rows:
            meta = result_map.get(str(row.get("note", "")), {})
            grouped.setdefault(str(meta.get("skeleton", _skeleton_from_note(str(row.get("note", ""))))), []).append({**row, **meta})

        promoted = 0
        retained = 0
        abandoned = 0
        lines = ["# Optimization Summary", ""]
        for skeleton, grouped_rows in sorted(grouped.items()):
            if any(_pass_row(row) for row in grouped_rows):
                promoted += 1
                best = _best_row([row for row in grouped_rows if _pass_row(row)])
                lines.append(f"- promoted `{skeleton}` via {best.get('note', '')}")
            elif any(_near_pass_row(row) for row in grouped_rows):
                retained += 1
                best = _best_row([row for row in grouped_rows if _near_pass_row(row)])
                lines.append(f"- retained `{skeleton}` as near-pass via {best.get('note', '')}")
            else:
                abandoned += 1
                lines.append(f"- abandoned `{skeleton}` after failed local optimization sweep")

        if not self.dry_run:
            _write_text(optimization_summary_path, "\n".join(lines) + "\n")

        _append_completed_stage(
            self.state,
            {
                "iteration": iteration,
                "stage": "optimization",
                "status": "completed" if promoted else "completed_no_promotion",
                "optimization_config": _relpath(optimization_config_path, self.workspace_root),
                "optimization_output": _relpath(optimization_output_path, self.workspace_root),
                "planned_candidate_count": len(candidates),
                "promoted_count": promoted,
                "retained_near_pass_count": retained,
                "abandoned_unchanged_count": abandoned,
            },
        )
        active = self.state.setdefault("active_stage_inputs", {})
        active["optimization_config"] = _relpath(optimization_config_path, self.workspace_root)
        active["optimization_output"] = _relpath(optimization_output_path, self.workspace_root)
        self.state["current_stage"] = "grading"
        self.state["recommended_next_step"] = f"Grade the optimized candidates for iteration {iteration}."
        if not self.dry_run:
            self.save()
        return StageResult(True, f"Completed optimization stage for iteration {iteration} with {len(candidates)} probes.")

    def _grading_stage(self) -> StageResult:
        iteration = int(self.state.get("current_iteration", 1) or 1)
        direct_submit = _read_json(self.run_dir / "direct_submit.json", [])
        optimization_output_rel = self.state.setdefault("active_stage_inputs", {}).get("optimization_output")
        optimization_rows = _read_json(self.workspace_root / str(optimization_output_rel), []) if optimization_output_rel else []
        optimization_meta = _load_optimization_candidate_map(self.run_dir)

        local_passes: list[dict[str, Any]] = []
        for row in direct_submit:
            if _pass_row(row):
                local_passes.append(row)
        for row in optimization_rows:
            meta = optimization_meta.get(str(row.get("note", "")), {})
            enriched = {**row, **meta}
            if _pass_row(enriched):
                local_passes.append(enriched)

        live_passes, held_passes = self._live_recheck_pass_rows(local_passes)
        grouped_passes: dict[str, list[dict[str, Any]]] = {}
        for row in live_passes:
            grouped_passes.setdefault(str(row.get("skeleton", "unknown")), []).append(row)

        best_by_skeleton: list[dict[str, Any]] = []
        for skeleton, rows in grouped_passes.items():
            best = _best_row(rows)
            if not best:
                continue
            best_by_skeleton.append(
                {
                    "skeleton": skeleton,
                    "base_alpha_id": best.get("base_alpha_id") or best.get("alpha_id"),
                    "optimized_alpha_id": best.get("alpha_id"),
                    "expression": best.get("expression"),
                    "settings": best.get("settings"),
                    "metrics": best.get("metrics"),
                    "fields": best.get("fields", []),
                    "chassis": best.get("chassis") or _chassis_signature(best.get("expression", ""), best.get("fields", [])),
                    "archetype": best.get("archetype", ""),
                    "checks_status": "all_pass",
                    "quality_label": _quality_label(best),
                    "selection_reason": "Best passing candidate for this skeleton by fitness, sharpe, and returns.",
                }
            )

        best_by_skeleton.sort(key=lambda item: _row_score(item), reverse=True)
        for index, item in enumerate(best_by_skeleton[:3], start=1):
            item["tier"] = index

        tier_1 = []
        tier_2 = []
        tier_3 = []
        for item in best_by_skeleton[:3]:
            tier_entry = {
                "alpha_id": item.get("optimized_alpha_id"),
                "skeleton": item.get("skeleton"),
                "expression": item.get("expression"),
                "settings": item.get("settings"),
                "metrics": item.get("metrics"),
                "fields": item.get("fields", []),
                "chassis": item.get("chassis", ""),
                "archetype": item.get("archetype", ""),
                "quality_label": item.get("quality_label", "submit"),
                "reason": item.get("selection_reason"),
            }
            if item["tier"] == 1:
                tier_1.append(tier_entry)
            elif item["tier"] == 2:
                tier_2.append(tier_entry)
            else:
                tier_3.append(tier_entry)

        best_parameters_path = self.run_dir / "best_parameters.json"
        submission_tiers_path = self.run_dir / f"submission_tiers_round{iteration}.json"
        stable_submission_tiers_path = self.run_dir / "submission_tiers.json"
        submission_tiers_md_path = self.run_dir / f"submission_tiers_round{iteration}.md"

        best_parameters = {
            "status": "complete_optimization_round",
            "best_by_skeleton": best_by_skeleton[:3],
            "retained_near_pass": [],
            "abandoned_candidates": [],
            "pending_transport_retries": held_passes,
        }
        submission_tiers = {
            "status": "complete",
            "tier_1": tier_1,
            "tier_2": tier_2,
            "tier_3": tier_3,
            "retained_near_pass": [],
            "pending_transport_retries": held_passes,
            "reason": "Automated grading is complete for the latest optimization and baseline pass set.",
        }
        markdown = "# Submission Tiers\n\n" + "\n".join(
            [
                *(f"- Tier 1: {item['skeleton']} ({item['alpha_id']})" for item in tier_1),
                *(f"- Tier 2: {item['skeleton']} ({item['alpha_id']})" for item in tier_2),
                *(f"- Tier 3: {item['skeleton']} ({item['alpha_id']})" for item in tier_3),
            ]
        ) + "\n"

        if not self.dry_run:
            _write_json(best_parameters_path, best_parameters)
            _write_json(submission_tiers_path, submission_tiers)
            _write_json(stable_submission_tiers_path, submission_tiers)
            _write_text(submission_tiers_md_path, markdown)

        _append_completed_stage(
            self.state,
            {
                "iteration": iteration,
                "stage": "grading",
                "status": "completed",
                "best_parameters": _relpath(best_parameters_path, self.workspace_root),
                "submission_tiers": _relpath(submission_tiers_path, self.workspace_root),
                "tier_1_count": len(tier_1),
                "tier_2_count": len(tier_2),
                "tier_3_count": len(tier_3),
            },
        )
        self.state.setdefault("state_files", {})["best_parameters"] = _relpath(best_parameters_path, self.workspace_root)
        self.state.setdefault("state_files", {})["submission_tiers"] = _relpath(stable_submission_tiers_path, self.workspace_root)
        self.state["current_iteration"] = iteration + 1
        self.state["current_stage"] = "family_generation"
        self.state["recommended_next_step"] = f"Generate the next family pool for iteration {iteration + 1}."
        if not self.dry_run:
            self.save()
        return StageResult(True, f"Completed grading for iteration {iteration}; advancing to family generation for iteration {iteration + 1}.")


def resolve_state_path(workspace_root: Path, run_tag: str | None, state_path: str | None) -> Path:
    return resolve_layout_state_path(workspace_root, run_tag, state_path)
