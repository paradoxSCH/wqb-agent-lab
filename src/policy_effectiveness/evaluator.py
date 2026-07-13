from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


def build_policy_effectiveness_report(run_dir: Path | str) -> dict[str, Any]:
    target = Path(run_dir)
    records = _read_json(target / "decision_attribution.json", [])
    stats: dict[str, dict[str, Any]] = defaultdict(_empty_policy_stats)
    if not isinstance(records, list):
        records = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        outcome = record.get("outcome") if isinstance(record.get("outcome"), Mapping) else {}
        actions = record.get("policy_actions_used") or []
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            diagnosis_type = str(action.get("diagnosis_type") or "unknown")
            item = stats[diagnosis_type]
            item["diagnosis_type"] = diagnosis_type
            item["budget_actions"].add(str(action.get("budget_action") or "unknown"))
            item["decision_count"] += 1
            item["simulations_spent"] += _int(outcome.get("simulations_spent"))
            item["submit_ready_count"] += _int(outcome.get("submit_ready_count"))
            item["near_pass_count"] += _int(outcome.get("near_pass_count"))
            item["low_value_count"] += _int(outcome.get("low_value_count"))

    policies = [_finalize(item) for item in stats.values()]
    policies.sort(key=lambda item: (-float(item["roi_per_1000"]), str(item["diagnosis_type"])))
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_dir": target.as_posix(),
        "policy_count": len(policies),
        "policies": policies,
    }


def write_policy_effectiveness_report(run_dir: Path | str) -> Path:
    target = Path(run_dir)
    report = build_policy_effectiveness_report(target)
    path = target / "policy_effectiveness_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _empty_policy_stats() -> dict[str, Any]:
    return {
        "diagnosis_type": "unknown",
        "budget_actions": set(),
        "decision_count": 0,
        "simulations_spent": 0,
        "submit_ready_count": 0,
        "near_pass_count": 0,
        "low_value_count": 0,
    }


def _finalize(item: dict[str, Any]) -> dict[str, Any]:
    spent = max(int(item["simulations_spent"]), 1)
    submit_ready = int(item["submit_ready_count"])
    near_pass = int(item["near_pass_count"])
    low_value = int(item["low_value_count"])
    return {
        "diagnosis_type": item["diagnosis_type"],
        "budget_actions": sorted(item["budget_actions"]),
        "decision_count": int(item["decision_count"]),
        "simulations_spent": int(item["simulations_spent"]),
        "submit_ready_count": submit_ready,
        "near_pass_count": near_pass,
        "low_value_count": low_value,
        "submit_ready_rate": round(submit_ready / spent, 4),
        "near_pass_rate": round(near_pass / spent, 4),
        "low_value_rate": round(low_value / spent, 4),
        "roi_per_1000": round((submit_ready / spent) * 1000.0, 3),
    }


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
