from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from src.agent_memory_sync import sync_run_memory
from src.behavioral_candidate_generation import write_candidate_generation_artifacts
from src.decision_attribution import record_scan_decision, score_decision_outcomes
from src.failure_diagnosis import diagnose_failure_objects
from src.memory_governance import write_memory_governance_report
from src.output_evaluation.evaluator import evaluate_run_outputs, write_run_output_evaluation
from src.policy_effectiveness import write_policy_effectiveness_report


def run_dry_run_loop_validation(
    workspace_root: Path | str,
    *,
    run_tag: str = "dry-run-loop-validation",
) -> dict[str, Any]:
    root = Path(workspace_root)
    run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / run_tag
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        run_dir / "daily_budget_ledger.json",
        {
            "daily_run_tag": run_tag,
            "date": date.today().isoformat(),
            "daily_budget": 12,
            "spent_simulations": 6,
            "current_stage": "dry_run_validation_complete",
            "stage_order": ["candidate_generation", "direction_probe", "output_evaluation", "memory_sync"],
        },
    )

    scan_rows = _diagnosed_scan_rows()
    _write_json(run_dir / "scan_results_snapshot.json", scan_rows)

    initial_output_evaluation = evaluate_run_outputs(run_dir)
    candidate_paths = write_candidate_generation_artifacts(
        _synthetic_fields(),
        run_dir,
        policy_feedback=initial_output_evaluation,
    )
    output_report_path, output_summary_path = write_run_output_evaluation(run_dir)

    queue = _read_json(candidate_paths["candidate_hypothesis_queue"], {})
    candidates = _candidate_rows_from_queue(queue)
    source_config = run_dir / "source_scan_config.json"
    sliced_config = run_dir / "dry_run_sliced_scan_config.json"
    result_path = run_dir / "direction_probe_results.json"
    _write_json(source_config, {"candidates": candidates})
    _write_json(sliced_config, {"stage": "direction_probe", "budget": len(candidates), "candidates": candidates})
    _write_json(result_path, _synthetic_probe_results(candidates))

    record_scan_decision(
        root,
        run_dir,
        stage="direction_probe",
        stage_budget=len(candidates),
        remaining_stage_budget=0,
        remaining_daily_budget=6,
        source_config=source_config,
        sliced_config=sliced_config,
        output_path=result_path,
        candidates=candidates,
        memory_nodes_used=["dry_run:short_term:candidate_generation"],
        graph_edges_used=["dry_run:policy_feedback->candidate_queue"],
    )
    score_decision_outcomes(run_dir)

    policy_effectiveness_path = write_policy_effectiveness_report(run_dir)
    memory_governance_path = write_memory_governance_report(run_dir)

    _write_json(
        root / ".local" / "data" / "behavioral_proxy" / "behavioral_proxy_map.json",
        {
            "mechanisms": [
                {
                    "mechanism": "quality_value_mispricing",
                    "proxy_strength": "strong",
                    "result_strength": "dry_run",
                    "budget_policy": "policy_evaluator_required",
                }
            ]
        },
    )
    memory_sync = sync_run_memory(root, run_dir)

    report = {
        "status": "complete",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_tag": run_tag,
        "run_dir": str(run_dir),
        "artifacts": {
            "output_evaluation_report": str(output_report_path),
            "output_evaluation_summary": str(output_summary_path),
            "candidate_hypothesis_queue": str(candidate_paths["candidate_hypothesis_queue"]),
            "decision_attribution": str(run_dir / "decision_attribution.json"),
            "policy_effectiveness_report": str(policy_effectiveness_path),
            "memory_governance_report": str(memory_governance_path),
            "memory_sync_report": str(memory_sync.report_path),
        },
        "checks": {
            "candidate_count": len(candidates),
            "memory_events_recorded": memory_sync.events_recorded,
            "live_wqb_calls": 0,
            "submission_attempts": 0,
        },
    }
    report_path = run_dir / "dry_run_loop_validation_report.json"
    _write_json(report_path, report)
    report["artifacts"]["dry_run_loop_validation_report"] = str(report_path)
    _write_json(report_path, report)
    return report


def _diagnosed_scan_rows() -> list[dict[str, Any]]:
    rows = [
        {
            "alpha_id": "DRY_WEAK_1",
            "family": "quality_value_mispricing",
            "skeleton": "quality_cashflow_rank",
            "triage_bucket": "low_value",
            "expression": "group_rank(rank(ts_mean(cashflow_quality_score, 60)) - rank(ts_std_dev(returns, 20)), subindustry)",
            "metrics": {"sharpe": 0.42, "fitness": 0.24, "turnover": 0.08},
            "checks": [
                {"name": "LOW_SHARPE", "result": "FAIL"},
                {"name": "LOW_FITNESS", "result": "FAIL"},
            ],
        },
        {
            "alpha_id": "DRY_CORR_1",
            "family": "quality_value_mispricing",
            "skeleton": "quality_cashflow_rank",
            "triage_bucket": "optimize_next",
            "expression": "group_rank(rank(ts_mean(roe_margin_quality, 120)), industry)",
            "metrics": {"sharpe": 1.48, "fitness": 1.12, "turnover": 0.05},
            "checks": [
                {"name": "LOW_SHARPE", "result": "PASS"},
                {"name": "LOW_FITNESS", "result": "PASS"},
                {"name": "SELF_CORRELATION", "result": "FAIL", "value": 0.82},
            ],
        },
    ]
    for row in rows:
        row["failure_diagnoses"] = diagnose_failure_objects(row)
    return rows


def _candidate_rows_from_queue(queue: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in queue.get("hypotheses") or []:
        if not isinstance(item, Mapping):
            continue
        if not (item.get("policy_feedback") or {}).get("budget_actions"):
            continue
        candidates.append(
            {
                "expression": str(item.get("skeleton_template") or "rank(close)"),
                "behavior_family": str(item.get("mechanism") or "unknown"),
                "wqb_action_lane": str(item.get("wqb_action_lane") or "probe"),
                "policy_feedback": item.get("policy_feedback") if isinstance(item.get("policy_feedback"), Mapping) else {},
            }
        )
        if len(candidates) >= 3:
            break
    if candidates:
        return candidates
    for item in queue.get("hypotheses") or []:
        if isinstance(item, Mapping):
            return [
                {
                    "expression": str(item.get("skeleton_template") or "rank(close)"),
                    "behavior_family": str(item.get("mechanism") or "unknown"),
                    "wqb_action_lane": str(item.get("wqb_action_lane") or "probe"),
                    "policy_feedback": item.get("policy_feedback") if isinstance(item.get("policy_feedback"), Mapping) else {},
                }
            ]
    return []


def _synthetic_probe_results(candidates: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        passed = index == 0
        rows.append(
            {
                "alpha_id": f"DRY_PROBE_{index + 1}",
                "expression": candidate.get("expression"),
                "behavior_thesis": candidate.get("behavior_family"),
                "metrics": {
                    "sharpe": 1.36 if passed else 0.62,
                    "fitness": 1.08 if passed else 0.38,
                    "turnover": 0.12,
                },
                "checks": [
                    {"name": "LOW_SHARPE", "result": "PASS" if passed else "FAIL"},
                    {"name": "LOW_FITNESS", "result": "PASS" if passed else "FAIL"},
                    {"name": "SELF_CORRELATION", "result": "PASS"},
                ],
            }
        )
    return rows


def _synthetic_fields() -> list[dict[str, Any]]:
    return [
        {
            "id": "cashflow_quality_score",
            "dataset_id": "fundamental6",
            "description": "cashflow quality and accrual quality score",
            "coverage": 0.93,
            "userCount": 12,
            "alphaCount": 31,
        },
        {
            "id": "roe_margin_quality",
            "dataset_id": "fundamental6",
            "description": "roe margin profitability quality",
            "coverage": 0.89,
            "userCount": 10,
            "alphaCount": 22,
        },
        {
            "id": "accrual_quality_value",
            "dataset_id": "fundamental6",
            "description": "accrual and valuation quality",
            "coverage": 0.86,
            "userCount": 8,
            "alphaCount": 18,
        },
        {
            "id": "analyst_eps_revision",
            "dataset_id": "analyst4",
            "description": "analyst eps estimate revision",
            "coverage": 0.82,
            "userCount": 9,
            "alphaCount": 20,
        },
        {
            "id": "news_sentiment_score",
            "dataset_id": "news",
            "description": "news media sentiment buzz signal",
            "coverage": 0.78,
            "userCount": 6,
            "alphaCount": 16,
        },
    ]


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
