from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from wqb_agent_lab.runtime.atomic_json import atomic_write_json


VALID_MODES = {"off", "shadow", "advisory", "control"}
SHADOW_DECISIONS = "policy_feedback_shadow.json"
SHADOW_EVALUATION = "policy_feedback_shadow_evaluation.json"


def resolve_feedback_mode(
    config: Mapping[str, Any] | None,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    policy = dict(config or {})
    requested = str(policy.get("mode") or "shadow").strip().lower()
    if requested not in VALID_MODES:
        requested = "shadow"
        mode_reason = "invalid_mode_fell_back_to_shadow"
    else:
        mode_reason = "configured"
    gate = evaluate_promotion_gate(evidence, policy.get("promotion_gate"))
    effective = requested
    if requested == "control" and not gate["passed"]:
        effective = "shadow"
        mode_reason = "control_blocked_until_promotion_gate_passes"
    return {
        "requested_mode": requested,
        "effective_mode": effective,
        "mode_reason": mode_reason,
        "promotion_gate": gate,
    }


def evaluate_promotion_gate(
    evidence: Mapping[str, Any],
    configured: Any = None,
) -> dict[str, Any]:
    thresholds = {
        "min_runs": 3,
        "min_recommended_simulations": 100,
        "min_submit_ready_rate_lift": 0.0,
        "max_low_value_rate_delta": -0.02,
        "min_distinct_family_retention": 0.8,
    }
    if isinstance(configured, Mapping):
        for key in thresholds:
            if key in configured:
                thresholds[key] = configured[key]
    # Configuration may make promotion stricter, but it must not weaken the
    # conservative floor that protects the baseline research loop.
    thresholds["min_runs"] = max(3, _int(thresholds["min_runs"]))
    thresholds["min_recommended_simulations"] = max(
        100,
        _int(thresholds["min_recommended_simulations"]),
    )
    thresholds["min_submit_ready_rate_lift"] = max(
        0.0,
        _float(thresholds["min_submit_ready_rate_lift"]),
    )
    thresholds["max_low_value_rate_delta"] = min(
        -0.02,
        _float(thresholds["max_low_value_rate_delta"]),
    )
    thresholds["min_distinct_family_retention"] = max(
        0.8,
        _float(thresholds["min_distinct_family_retention"]),
    )
    checks = {
        "enough_runs": _int(evidence.get("run_count")) >= thresholds["min_runs"],
        "enough_recommended_simulations": _int(
            evidence.get("recommended_simulations_observed")
        )
        >= thresholds["min_recommended_simulations"],
        "submit_ready_rate_not_worse": _float(
            evidence.get("submit_ready_rate_lift")
        )
        >= _float(thresholds["min_submit_ready_rate_lift"]),
        "low_value_rate_improved": _float(evidence.get("low_value_rate_delta"))
        <= _float(thresholds["max_low_value_rate_delta"]),
        "family_diversity_retained": _float(
            evidence.get("distinct_family_retention"),
            default=1.0,
        )
        >= _float(thresholds["min_distinct_family_retention"]),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": thresholds,
        "evidence": dict(evidence),
    }


def aggregate_shadow_evidence(runs_root: Path | str) -> dict[str, Any]:
    root = Path(runs_root)
    aggregates: list[Mapping[str, Any]] = []
    for path in sorted(root.glob(f"*/{SHADOW_EVALUATION}")):
        payload = _read_json(path, {})
        aggregate = payload.get("aggregate") if isinstance(payload, Mapping) else None
        if isinstance(aggregate, Mapping) and _int(aggregate.get("recommended_simulations_observed")):
            aggregates.append(aggregate)
    baseline_simulations = sum(_int(item.get("baseline_simulations_observed")) for item in aggregates)
    recommended_simulations = sum(
        _int(item.get("recommended_simulations_observed")) for item in aggregates
    )
    baseline_submit_ready = sum(_int(item.get("baseline_submit_ready_count")) for item in aggregates)
    recommended_submit_ready = sum(
        _int(item.get("recommended_submit_ready_count")) for item in aggregates
    )
    baseline_low_value = sum(_int(item.get("baseline_low_value_count")) for item in aggregates)
    recommended_low_value = sum(
        _int(item.get("recommended_low_value_count")) for item in aggregates
    )
    baseline_rate = baseline_submit_ready / max(baseline_simulations, 1)
    recommended_rate = recommended_submit_ready / max(recommended_simulations, 1)
    baseline_low_rate = baseline_low_value / max(baseline_simulations, 1)
    recommended_low_rate = recommended_low_value / max(recommended_simulations, 1)
    baseline_families = sum(_int(item.get("baseline_distinct_family_count")) for item in aggregates)
    recommended_families = sum(
        _int(item.get("recommended_distinct_family_count")) for item in aggregates
    )
    return {
        "run_count": len(aggregates),
        "baseline_simulations_observed": baseline_simulations,
        "recommended_simulations_observed": recommended_simulations,
        "baseline_submit_ready_count": baseline_submit_ready,
        "recommended_submit_ready_count": recommended_submit_ready,
        "baseline_low_value_count": baseline_low_value,
        "recommended_low_value_count": recommended_low_value,
        "baseline_submit_ready_rate": round(baseline_rate, 6),
        "recommended_submit_ready_rate": round(recommended_rate, 6),
        "submit_ready_rate_lift": round(recommended_rate - baseline_rate, 6),
        "baseline_low_value_rate": round(baseline_low_rate, 6),
        "recommended_low_value_rate": round(recommended_low_rate, 6),
        "low_value_rate_delta": round(recommended_low_rate - baseline_low_rate, 6),
        "baseline_distinct_family_count": baseline_families,
        "recommended_distinct_family_count": recommended_families,
        "distinct_family_retention": round(
            recommended_families / max(baseline_families, 1),
            6,
        ),
    }


def cap_recommended_candidates(
    candidates: Sequence[Mapping[str, Any]],
    budget: int,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    capped_counts: dict[str, int] = {}
    caps_applied: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        copied = deepcopy(dict(candidate))
        feedback = candidate.get("policy_feedback")
        feedback = feedback if isinstance(feedback, Mapping) else {}
        max_share = feedback.get("max_budget_share")
        cap_key = _policy_cap_key(feedback)
        if max_share is not None and cap_key:
            cap_count = max(0, int(_float(max_share) * max(budget, 1)))
            caps_applied[cap_key] = {
                "max_budget_share": _float(max_share),
                "max_candidate_count": cap_count,
            }
            if capped_counts.get(cap_key, 0) >= cap_count:
                overflow.append(copied)
                continue
            capped_counts[cap_key] = capped_counts.get(cap_key, 0) + 1
        selected.append(copied)
    return selected, caps_applied, overflow


def record_shadow_decision(
    run_dir: Path | str,
    *,
    stage: str,
    output_path: str,
    baseline_candidates: Sequence[Mapping[str, Any]],
    recommended_candidates: Sequence[Mapping[str, Any]],
    governance: Mapping[str, Any],
    caps_applied: Mapping[str, Any],
    overflow_candidates: Sequence[Mapping[str, Any]],
    now: datetime | None = None,
) -> Path:
    target = Path(run_dir)
    decision_id = hashlib.sha256(f"{stage}\x1f{output_path}".encode("utf-8")).hexdigest()[:20]
    record = {
        "decision_id": decision_id,
        "stage": stage,
        "output_path": output_path,
        "recorded_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "governance": deepcopy(dict(governance)),
        "caps_applied": deepcopy(dict(caps_applied)),
        "baseline_candidates": [deepcopy(dict(item)) for item in baseline_candidates],
        "recommended_candidates": [deepcopy(dict(item)) for item in recommended_candidates],
        "overflow_candidates": [deepcopy(dict(item)) for item in overflow_candidates],
    }
    path = target / SHADOW_DECISIONS
    payload = _read_json(path, {"schema_version": 1, "decisions": []})
    decisions = payload.get("decisions") if isinstance(payload, Mapping) else []
    rows = [dict(item) for item in decisions or [] if isinstance(item, Mapping)]
    rows = [item for item in rows if item.get("decision_id") != decision_id]
    rows.append(record)
    rows.sort(key=lambda item: str(item.get("decision_id") or ""))
    _write_json(path, {"schema_version": 1, "decisions": rows})
    return path


def score_shadow_decisions(
    workspace_root: Path | str,
    run_dir: Path | str,
    *,
    now: datetime | None = None,
) -> Path | None:
    root = Path(workspace_root)
    target = Path(run_dir)
    source = _read_json(target / SHADOW_DECISIONS, {})
    decisions = source.get("decisions") if isinstance(source, Mapping) else None
    if not isinstance(decisions, list):
        return None
    scored: list[dict[str, Any]] = []
    for decision in decisions:
        if not isinstance(decision, Mapping):
            continue
        output_path = _resolve_path(root, target, str(decision.get("output_path") or ""))
        results = _read_json(output_path, [])
        result_rows = [item for item in results if isinstance(item, Mapping)] if isinstance(results, list) else []
        baseline = [item for item in decision.get("baseline_candidates") or [] if isinstance(item, Mapping)]
        recommended = [item for item in decision.get("recommended_candidates") or [] if isinstance(item, Mapping)]
        baseline_rows = _match_result_rows(baseline, result_rows)
        recommended_rows = _match_result_rows(recommended, result_rows)
        scored.append(
            {
                "decision_id": decision.get("decision_id"),
                "stage": decision.get("stage"),
                "governance": deepcopy(dict(decision.get("governance") or {})),
                "baseline": _outcome_metrics(baseline_rows, baseline),
                "recommended": _outcome_metrics(recommended_rows, recommended),
            }
        )
    aggregate = _aggregate_scored(scored)
    report = {
        "schema_version": 1,
        "generated_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "run_dir": _relative_path(target, root),
        "decisions": scored,
        "aggregate": aggregate,
    }
    path = target / SHADOW_EVALUATION
    _write_json(path, report)
    return path


def _aggregate_scored(scored: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    baseline_simulations = sum(_int((item.get("baseline") or {}).get("simulations_observed")) for item in scored)
    recommended_simulations = sum(_int((item.get("recommended") or {}).get("simulations_observed")) for item in scored)
    baseline_submit = sum(_int((item.get("baseline") or {}).get("submit_ready_count")) for item in scored)
    recommended_submit = sum(_int((item.get("recommended") or {}).get("submit_ready_count")) for item in scored)
    baseline_low = sum(_int((item.get("baseline") or {}).get("low_value_count")) for item in scored)
    recommended_low = sum(_int((item.get("recommended") or {}).get("low_value_count")) for item in scored)
    baseline_families = sum(_int((item.get("baseline") or {}).get("distinct_family_count")) for item in scored)
    recommended_families = sum(_int((item.get("recommended") or {}).get("distinct_family_count")) for item in scored)
    return {
        "decision_count": len(scored),
        "baseline_simulations_observed": baseline_simulations,
        "recommended_simulations_observed": recommended_simulations,
        "baseline_submit_ready_count": baseline_submit,
        "recommended_submit_ready_count": recommended_submit,
        "baseline_low_value_count": baseline_low,
        "recommended_low_value_count": recommended_low,
        "baseline_distinct_family_count": baseline_families,
        "recommended_distinct_family_count": recommended_families,
    }


def _outcome_metrics(
    rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    submit_ready = sum(1 for row in rows if _metric_pass(row) and _all_checks_pass(row))
    low_value = sum(1 for row in rows if not _metric_pass(row))
    families = {_candidate_family(item) for item in candidates if _candidate_family(item)}
    return {
        "simulations_observed": len(rows),
        "submit_ready_count": submit_ready,
        "low_value_count": low_value,
        "submit_ready_rate": round(submit_ready / max(len(rows), 1), 6),
        "low_value_rate": round(low_value / max(len(rows), 1), 6),
        "distinct_family_count": len(families),
    }


def _candidate_identity(candidate: Mapping[str, Any]) -> str:
    payload = {
        "expression": " ".join(str(candidate.get("expression") or "").split()),
        "settings": candidate.get("settings") if isinstance(candidate.get("settings"), Mapping) else {},
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _match_result_rows(
    candidates: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    by_identity = {_candidate_identity(item): item for item in results}
    by_expression: dict[str, list[Mapping[str, Any]]] = {}
    for item in results:
        by_expression.setdefault(_normalized_expression(item), []).append(item)
    matched: list[Mapping[str, Any]] = []
    seen: set[int] = set()
    for candidate in candidates:
        row = by_identity.get(_candidate_identity(candidate))
        if row is None:
            expression_matches = by_expression.get(_normalized_expression(candidate), [])
            row = expression_matches[0] if len(expression_matches) == 1 else None
        if row is not None and id(row) not in seen:
            matched.append(row)
            seen.add(id(row))
    return matched


def _normalized_expression(candidate: Mapping[str, Any]) -> str:
    return " ".join(str(candidate.get("expression") or "").split())


def _candidate_family(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("behavior_family") or candidate.get("family") or candidate.get("mechanism") or "")


def _policy_cap_key(feedback: Mapping[str, Any]) -> str:
    actions = feedback.get("budget_actions") if isinstance(feedback.get("budget_actions"), Mapping) else {}
    diagnoses = sorted(
        str(action.get("diagnosis_type") or key)
        for key, action in actions.items()
        if isinstance(action, Mapping)
    )
    return "+".join(diagnoses)


def _metric_pass(row: Mapping[str, Any]) -> bool:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    return _float(metrics.get("sharpe")) >= 1.25 and _float(metrics.get("fitness")) >= 1.0


def _all_checks_pass(row: Mapping[str, Any]) -> bool:
    checks = [item for item in row.get("checks") or [] if isinstance(item, Mapping)]
    return bool(checks) and all(str(item.get("result") or "").upper() == "PASS" for item in checks)


def _resolve_path(root: Path, run_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    rooted = root / path
    return rooted if rooted.exists() else run_dir / path.name


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload)


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
