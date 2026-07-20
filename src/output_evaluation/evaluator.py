from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from src.diagnosis_policy import evaluate_diagnosis_policies

from .budget_policy import build_budget_policy_actions
from .types import OutputDiagnosis, OutputEvaluationRecord
from .validators import (
    validate_candidate_hypothesis_queue,
    validate_memory_sync_report,
    validate_report_text,
)


def evaluate_run_outputs(
    run_dir: Path | str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now()
    run_path = Path(run_dir)
    records: list[OutputEvaluationRecord] = []
    diagnosis_policy: dict[str, Any] = {}

    candidate_queue = _read_json(run_path / "candidate_hypothesis_queue.json", None)
    if isinstance(candidate_queue, dict):
        records.append(validate_candidate_hypothesis_queue("candidate_hypothesis_queue.json", candidate_queue))

    preflight_report = _read_json(run_path / "preflight_evaluation_report.json", None)
    if isinstance(preflight_report, dict):
        records.append(_record_from_preflight_report(preflight_report))

    scan_rows = _read_json(run_path / "scan_results_snapshot.json", None)
    if isinstance(scan_rows, list):
        diagnosis_policy = evaluate_diagnosis_policies(scan_rows, now=now)
        records.append(
            OutputEvaluationRecord(
                artifact="scan_results_snapshot.json",
                stage="wqb_simulation",
                validation_status="warn" if diagnosis_policy.get("total_diagnoses") else "pass",
                diagnoses=tuple(),
                metrics={
                    "row_count": len(scan_rows),
                    "total_diagnoses": diagnosis_policy.get("total_diagnoses", 0),
                    "policy_count": diagnosis_policy.get("policy_count", 0),
                    "budget_saved_estimate": diagnosis_policy.get("budget_saved_estimate", 0),
                },
            )
        )

    memory_sync = _read_json(run_path / "memory_sync_report.json", None)
    if isinstance(memory_sync, dict):
        records.append(validate_memory_sync_report("memory_sync_report.json", memory_sync))

    shadow_feedback = _read_json(
        run_path / "policy_feedback_shadow_evaluation.json",
        None,
    )
    shadow_aggregate = (
        dict(shadow_feedback.get("aggregate") or {})
        if isinstance(shadow_feedback, dict)
        else {}
    )

    for report_name in ("triage_summary.md", "diagnosis_policy_evaluation.md", "wqb-agent-latest-workflow-uml.html"):
        report_path = run_path / report_name
        if report_path.exists():
            records.append(validate_report_text(report_name, report_path.read_text(encoding="utf-8", errors="replace")))

    serialized_records = [asdict(record) for record in records]
    status_counts = Counter(record.validation_status for record in records)
    budget_saved_estimate = sum(int(record.metrics.get("budget_saved_estimate") or 0) for record in records)
    policy_actions = build_budget_policy_actions(diagnosis_policy)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "run_dir": run_path.as_posix(),
        "record_count": len(records),
        "status_counts": dict(status_counts),
        "records": serialized_records,
        "diagnosis_policy": diagnosis_policy,
        "budget_policy_actions": policy_actions,
        "budget_saved_estimate": budget_saved_estimate,
        "policy_feedback_shadow": shadow_aggregate,
    }


def write_run_output_evaluation(
    run_dir: Path | str,
    *,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    run_path = Path(run_dir)
    report = evaluate_run_outputs(run_path, now=now)
    report_path = run_path / "output_evaluation_report.json"
    summary_path = run_path / "output_evaluation_summary.md"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    summary_path.write_text(_summary_markdown(report), encoding="utf-8")
    return report_path, summary_path


def _summary_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Output Evaluation Report",
        "",
        f"Generated at: `{report.get('generated_at')}`",
        f"Run dir: `{report.get('run_dir')}`",
        f"Records: `{report.get('record_count')}`",
        f"Budget saved estimate: `{report.get('budget_saved_estimate')}`",
        "",
        "## Status Counts",
    ]
    status_counts = report.get("status_counts") or {}
    if status_counts:
        for status, count in sorted(status_counts.items()):
            lines.append(f"- `{status}`: `{count}`")
    else:
        lines.append("- None.")

    lines.extend(["", "## Budget Policy Actions"])
    actions = report.get("budget_policy_actions") or []
    if actions:
        for action in actions:
            lines.append(
                f"- `{action.get('diagnosis_type')}` -> `{action.get('budget_action')}` "
                f"max_share=`{action.get('max_budget_share')}`"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Output Diagnoses"])
    any_diagnosis = False
    for record in report.get("records") or []:
        for diagnosis in record.get("diagnoses") or []:
            any_diagnosis = True
            lines.append(
                f"- `{record.get('artifact')}` `{diagnosis.get('diagnosis_type')}` "
                f"severity=`{diagnosis.get('severity')}` action=`{diagnosis.get('recommended_action')}`"
            )
    if not any_diagnosis:
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _record_from_preflight_report(report: dict[str, Any]) -> OutputEvaluationRecord:
    diagnoses = []
    for item in report.get("diagnoses") or []:
        if not isinstance(item, dict):
            continue
        diagnoses.append(
            OutputDiagnosis(
                diagnosis_type=str(item.get("diagnosis_type") or "unknown"),
                severity=str(item.get("severity") or "unknown"),
                evidence=item.get("evidence") if isinstance(item.get("evidence"), dict) else {},
                recommended_action=str(item.get("recommended_action") or "quarantine_unknown_diagnosis"),
                policy=str(item.get("policy") or "quarantine_unknown_diagnosis"),
                success_metric=str(item.get("success_metric") or "classification_resolution_rate"),
                failure_metric=str(item.get("failure_metric") or "repeat_failure_rate"),
            )
        )
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    return OutputEvaluationRecord(
        artifact=str(report.get("artifact") or "preflight_evaluation_report.json"),
        stage=str(report.get("stage") or "scan_config_expression"),
        validation_status=str(report.get("validation_status") or ("block" if diagnoses else "pass")),
        diagnoses=tuple(diagnoses),
        metrics=dict(metrics),
    )
