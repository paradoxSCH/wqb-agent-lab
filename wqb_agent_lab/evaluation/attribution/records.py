from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


ATTRIBUTION_BASENAME = "decision_attribution.json"


def record_scan_decision(
    root: Path | str,
    run_dir: Path | str,
    *,
    stage: str,
    stage_budget: int,
    remaining_stage_budget: int,
    remaining_daily_budget: int,
    source_config: Path | str,
    sliced_config: Path | str,
    output_path: Path | str,
    candidates: Sequence[Mapping[str, Any]],
    proxy_map_path: Path | str | None = None,
    memory_nodes_used: Sequence[str] | None = None,
    graph_edges_used: Sequence[str] | None = None,
    llm_output_ref: str | None = None,
    policy_feedback_governance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    workspace_root = Path(root)
    target_run_dir = Path(run_dir)
    families = sorted({_candidate_family(candidate) for candidate in candidates if _candidate_family(candidate)})
    decision_id = _decision_id(stage, sliced_config, output_path)
    observed_policy_actions = _policy_actions_from_candidates(candidates)
    feedback_governance = dict(policy_feedback_governance or {})
    effective_feedback_mode = str(
        feedback_governance.get("effective_mode") or "control"
    )
    record = {
        "decision_id": decision_id,
        "stage": stage,
        "decision_type": "stage_scan_budget",
        "budget_delta": len(candidates),
        "candidate_count": len(candidates),
        "stage_budget": int(stage_budget),
        "remaining_stage_budget": int(remaining_stage_budget),
        "remaining_daily_budget": int(remaining_daily_budget),
        "families_affected": families,
        "proxy_signals_used": _proxy_signals_for_families(Path(proxy_map_path) if proxy_map_path else None, families),
        "policy_actions_used": (
            observed_policy_actions if effective_feedback_mode == "control" else []
        ),
        "policy_actions_observed": observed_policy_actions,
        "required_experiments_used": (
            _required_experiments_from_candidates(candidates)
            if effective_feedback_mode == "control"
            else []
        ),
        "policy_action_lanes": (
            _policy_lanes_from_candidates(candidates)
            if effective_feedback_mode == "control"
            else []
        ),
        "policy_feedback_governance": feedback_governance,
        "memory_nodes_used": list(memory_nodes_used or []),
        "graph_edges_used": list(graph_edges_used or []),
        "llm_output_ref": llm_output_ref or "",
        "deterministic_validation_result": "passed",
        "source_config": _relative_path(Path(source_config), workspace_root),
        "sliced_config": _relative_path(Path(sliced_config), workspace_root),
        "output_path": _relative_path(Path(output_path), workspace_root),
        "outcome_ref": _relative_path(Path(output_path), workspace_root),
        "outcome": {},
    }
    path = target_run_dir / ATTRIBUTION_BASENAME
    records = _read_json(path, [])
    if not isinstance(records, list):
        records = []
    records = [item for item in records if not (isinstance(item, dict) and item.get("decision_id") == decision_id)]
    records.append(record)
    records.sort(key=lambda item: str(item.get("decision_id", "")))
    _write_json(path, records)
    return record


def score_decision_outcomes(run_dir: Path | str) -> list[dict[str, Any]]:
    target_run_dir = Path(run_dir)
    path = target_run_dir / ATTRIBUTION_BASENAME
    records = _read_json(path, [])
    if not isinstance(records, list):
        return []
    updated = []
    for record in records:
        if not isinstance(record, dict):
            continue
        output_path = _resolve_record_path(target_run_dir, str(record.get("outcome_ref") or record.get("output_path") or ""))
        rows = _read_json(output_path, [])
        if not isinstance(rows, list):
            rows = []
        outcome = _outcome_metrics(rows)
        record = dict(record)
        record["outcome"] = outcome
        updated.append(record)
    _write_json(path, updated)
    return updated


def _outcome_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    simulations_spent = len(rows)
    submit_ready_count = sum(1 for row in rows if _all_checks_pass(row) and _metric_pass(row))
    near_pass_count = sum(1 for row in rows if _metric_pass(row))
    low_value_count = sum(1 for row in rows if not _metric_pass(row))
    self_corr_fail_count = sum(1 for row in rows if "SELF_CORRELATION" in _failed_checks(row))
    return {
        "simulations_spent": simulations_spent,
        "submit_ready_count": submit_ready_count,
        "near_pass_count": near_pass_count,
        "low_value_count": low_value_count,
        "self_corr_fail_count": self_corr_fail_count,
        "roi_per_1000": round((submit_ready_count / max(simulations_spent, 1)) * 1000.0, 3),
    }


def _candidate_family(candidate: Mapping[str, Any]) -> str:
    value = candidate.get("behavior_family") or candidate.get("family")
    if value:
        return str(value)
    note = str(candidate.get("note") or "")
    if ":" in note:
        return note.split(":", 1)[0].strip()
    return ""


def _proxy_signals_for_families(proxy_map_path: Path | None, families: Sequence[str]) -> list[dict[str, Any]]:
    if proxy_map_path is None or not proxy_map_path.exists():
        return []
    payload = _read_json(proxy_map_path, {})
    mechanisms = payload.get("mechanisms") if isinstance(payload, dict) else []
    if not isinstance(mechanisms, list):
        return []
    family_set = set(families)
    signals = []
    for item in mechanisms:
        if not isinstance(item, dict):
            continue
        mechanism = str(item.get("mechanism") or "")
        if mechanism not in family_set:
            continue
        signals.append({
            "mechanism": mechanism,
            "budget_policy": item.get("budget_policy"),
            "proxy_strength": item.get("proxy_strength"),
            "result_strength": item.get("result_strength"),
        })
    return signals


def _policy_actions_from_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    actions: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        raw_feedback = candidate.get("policy_feedback")
        feedback: Mapping[str, Any] = (
            raw_feedback if isinstance(raw_feedback, Mapping) else {}
        )
        raw_actions = feedback.get("budget_actions")
        budget_actions: Mapping[str, Any] = (
            raw_actions if isinstance(raw_actions, Mapping) else {}
        )
        for key, action in budget_actions.items():
            if not isinstance(action, Mapping):
                continue
            diagnosis_type = str(action.get("diagnosis_type") or key)
            actions[diagnosis_type] = {
                "diagnosis_type": diagnosis_type,
                "budget_action": action.get("budget_action"),
                "max_budget_share": action.get("max_budget_share"),
            }
    return [actions[key] for key in sorted(actions)]


def _required_experiments_from_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    experiments: set[str] = set()
    for candidate in candidates:
        feedback = candidate.get("policy_feedback") if isinstance(candidate.get("policy_feedback"), Mapping) else {}
        for experiment in feedback.get("required_experiments") or [] if isinstance(feedback, Mapping) else []:
            experiments.add(str(experiment))
    return sorted(experiments)


def _policy_lanes_from_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    lanes = {str(candidate.get("wqb_action_lane")) for candidate in candidates if candidate.get("wqb_action_lane")}
    return sorted(lanes)


def _metric_pass(row: Mapping[str, Any]) -> bool:
    metrics = row.get("metrics") or {}
    return _float(metrics.get("sharpe")) >= 1.25 and _float(metrics.get("fitness")) >= 1.0


def _all_checks_pass(row: Mapping[str, Any]) -> bool:
    checks = row.get("checks") or []
    return bool(checks) and all(check.get("result") == "PASS" for check in checks)


def _failed_checks(row: Mapping[str, Any]) -> set[str]:
    return {
        str(check.get("name") or "")
        for check in row.get("checks") or []
        if check.get("result") == "FAIL"
    }


def _float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _decision_id(stage: str, sliced_config: Path | str, output_path: Path | str) -> str:
    material = f"{stage}\x1f{sliced_config}\x1f{output_path}"
    return "decision-" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


def _resolve_record_path(run_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    candidate = run_dir.parents[2] / path if len(run_dir.parents) >= 3 else path
    if candidate.exists():
        return candidate
    return run_dir / path.name


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
