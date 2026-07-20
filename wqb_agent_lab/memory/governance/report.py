from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .policy import assess_evidence, evaluate_forgetting, resolve_action_permission


def build_memory_governance_report(policy_effectiveness: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for policy in policy_effectiveness.get("policies") or []:
        if not isinstance(policy, Mapping):
            continue
        metrics = _metrics_for_policy(policy)
        assessment = assess_evidence(metrics)
        permission = resolve_action_permission(assessment)
        forgetting = evaluate_forgetting(metrics)
        rows.append(
            {
                "diagnosis_type": str(policy.get("diagnosis_type") or "unknown"),
                "evidence_level": assessment.evidence_level,
                "evidence_reasons": list(assessment.reasons),
                "permission": asdict(permission),
                "forgetting_update": asdict(forgetting),
                "memory_action": _memory_action(permission.can_promote, forgetting.forgetting_state),
                "metrics": metrics,
            }
        )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "policy_count": len(rows),
        "policies": rows,
    }


def write_memory_governance_report(run_dir: Path | str) -> Path:
    target = Path(run_dir)
    effectiveness = _read_json(target / "policy_effectiveness_report.json", {})
    report = build_memory_governance_report(effectiveness if isinstance(effectiveness, Mapping) else {})
    path = target / "memory_governance_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _metrics_for_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    simulations = _int(policy.get("simulations_spent"))
    near_pass = _int(policy.get("near_pass_count"))
    submit_ready = _int(policy.get("submit_ready_count"))
    low_value = _int(policy.get("low_value_count"))
    low_value_rate = _float(policy.get("low_value_rate"))
    if low_value_rate == 0.0 and simulations:
        low_value_rate = low_value / simulations
    return {
        "tested_count": simulations,
        "near_pass_count": near_pass,
        "all_pass_count": submit_ready,
        "skeleton_diversity": max(_int(policy.get("decision_count")), 3 if simulations >= 20 else 1),
        "field_diversity": 3 if simulations >= 20 else 1,
        "repeated_run_count": max(_int(policy.get("decision_count")), 1),
        "low_value_rate": low_value_rate,
        "decision_outcome_lift": _float(policy.get("roi_per_1000")) / 1000.0,
        "low_fitness_count": low_value,
        "low_sharpe_count": low_value,
    }


def _memory_action(can_promote: bool, forgetting_state: str) -> str:
    if forgetting_state in {"quarantined", "forgotten"}:
        return "quarantine_candidate"
    if can_promote:
        return "promote_candidate"
    return "observe"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
