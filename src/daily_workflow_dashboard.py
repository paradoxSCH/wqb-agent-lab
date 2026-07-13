from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


RUNS_ROOT = Path(".local/data/runs/continuous-alpha")
EVALUATIONS_ROOT = Path(".local/data/evaluations")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _stage_from_result_name(file_name: str, stage_order: list[str]) -> str | None:
    for stage in sorted(stage_order, key=len, reverse=True):
        prefix = f"{stage}_"
        if file_name.startswith(prefix) and file_name.endswith("_results.json"):
            return stage
    return None


def _result_row_count(path: Path) -> int:
    payload = _read_json(path, [])
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        return 1
    return 0


def _iso_from_timestamp(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def _last_activity(paths: list[Path]) -> tuple[float | None, Path | None]:
    latest_timestamp: float | None = None
    latest_path: Path | None = None
    for path in paths:
        try:
            timestamp = path.stat().st_mtime
        except OSError:
            continue
        if latest_timestamp is None or timestamp > latest_timestamp:
            latest_timestamp = timestamp
            latest_path = path
    return latest_timestamp, latest_path


def _first_activity(paths: list[Path]) -> tuple[float | None, Path | None]:
    earliest_timestamp: float | None = None
    earliest_path: Path | None = None
    for path in paths:
        try:
            timestamp = path.stat().st_mtime
        except OSError:
            continue
        if earliest_timestamp is None or timestamp < earliest_timestamp:
            earliest_timestamp = timestamp
            earliest_path = path
    return earliest_timestamp, earliest_path


def _stage_status(stage: str, budget: int, progress: int, current_stage: str) -> str:
    if current_stage == f"{stage}_partial":
        return "partial"
    if current_stage == f"{stage}_complete":
        return "complete"
    if budget > 0 and progress >= budget:
        return "complete"
    if progress > 0:
        return "partial"
    return "pending"


def build_run_snapshot(run_dir: Path, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    ledger_path = run_dir / "daily_budget_ledger.json"
    ledger = _read_json(ledger_path, {})
    stage_order = list(ledger.get("stage_order") or [])
    stage_budgets = dict(ledger.get("stage_budgets") or {})
    stage_spend = dict(ledger.get("stage_spend") or {})
    current_stage = str(ledger.get("current_stage") or "unknown")

    result_paths = sorted(run_dir.glob("*_results.json"))
    inferred_stage_spend: dict[str, int] = {}
    result_files: list[dict[str, Any]] = []
    for result_path in result_paths:
        stage = _stage_from_result_name(result_path.name, stage_order)
        rows = _result_row_count(result_path)
        if stage:
            inferred_stage_spend[stage] = max(inferred_stage_spend.get(stage, 0), rows)
        result_files.append({
            "name": result_path.name,
            "path": result_path.as_posix(),
            "stage": stage,
            "rows": rows,
            "updated_at": _iso_from_timestamp(result_path.stat().st_mtime),
        })

    tracked_paths = [ledger_path, *result_paths]
    report_path = run_dir / "submit_summary_budget_complete.md"
    if report_path.exists():
        tracked_paths.append(report_path)
    latest_timestamp, latest_path = _last_activity(tracked_paths)
    latest_activity_at = _iso_from_timestamp(latest_timestamp)
    latest_activity_age_minutes = None
    if latest_timestamp is not None:
        latest_activity_age_minutes = round((now.timestamp() - latest_timestamp) / 60.0, 1)

    stage_rows: list[dict[str, Any]] = []
    total_budget = int(ledger.get("daily_budget") or 0)
    total_spend = int(ledger.get("spent_simulations") or 0)
    inferred_total_spend = total_spend
    for stage in stage_order:
        budget = int(stage_budgets.get(stage) or 0)
        recorded = int(stage_spend.get(stage) or 0)
        inferred = int(inferred_stage_spend.get(stage) or 0)
        progress = max(recorded, inferred)
        inferred_total_spend = max(inferred_total_spend, total_spend - recorded + progress) if recorded else max(inferred_total_spend, total_spend + progress)
        stage_rows.append({
            "stage": stage,
            "budget": budget,
            "recorded_spend": recorded,
            "inferred_spend": inferred,
            "effective_spend": progress,
            "status": _stage_status(stage, budget, progress, current_stage),
            "percent": round((progress / budget) * 100.0, 1) if budget > 0 else (100.0 if progress > 0 else 0.0),
        })

    inferred_running = bool(result_paths) and current_stage in {"initialized", "unknown"}
    is_complete = current_stage == "budget_complete_report_written" or (
        total_budget > 0 and int(ledger.get("remaining_simulations_after_commitments") or 0) <= 0 and report_path.exists()
    )
    health = "complete" if is_complete else "active"
    if inferred_running:
        health = "inferred-active"
    if latest_activity_age_minutes is not None and latest_activity_age_minutes > 45 and not is_complete:
        health = "stalled"

    earliest_timestamp, _ = _first_activity(result_paths)
    if earliest_timestamp is None and ledger_path.exists():
        try:
            earliest_timestamp = ledger_path.stat().st_mtime
        except OSError:
            earliest_timestamp = None

    eta_info = None
    speed_per_hour = 0.0
    if earliest_timestamp is not None and total_budget > 0:
        elapsed_hours = (now.timestamp() - earliest_timestamp) / 3600.0
        if elapsed_hours > 0.01 and inferred_total_spend > 0:
            speed_per_hour = inferred_total_spend / elapsed_hours
            remaining = total_budget - inferred_total_spend
            if remaining > 0 and speed_per_hour > 0:
                eta_hours = remaining / speed_per_hour
                eta_at = now + timedelta(hours=eta_hours)
                eta_info = {
                    "eta_hours": round(eta_hours, 2),
                    "eta_at": eta_at.isoformat(timespec="minutes"),
                    "speed_per_hour": round(speed_per_hour, 1),
                    "elapsed_hours": round(elapsed_hours, 2),
                }

    for stage_row in stage_rows:
        if stage_row["status"] == "partial" and stage_row["budget"] > 0 and speed_per_hour > 0:
            stage_remaining = max(0, stage_row["budget"] - stage_row["effective_spend"])
            stage_row["eta_hours"] = round(stage_remaining / speed_per_hour, 2)
        else:
            stage_row["eta_hours"] = None

    return {
        "run_tag": str(ledger.get("daily_run_tag") or run_dir.name),
        "date": ledger.get("date"),
        "run_dir": run_dir.as_posix(),
        "ledger_path": ledger_path.as_posix(),
        "current_stage": current_stage,
        "daily_budget": total_budget,
        "spent_simulations": total_spend,
        "inferred_spent_simulations": inferred_total_spend,
        "remaining_simulations_after_commitments": int(ledger.get("remaining_simulations_after_commitments") or 0),
        "submit_report": report_path.as_posix() if report_path.exists() else None,
        "latest_activity_at": latest_activity_at,
        "latest_activity_path": latest_path.as_posix() if latest_path else None,
        "latest_activity_age_minutes": latest_activity_age_minutes,
        "health": health,
        "eta": eta_info,
        "stages": stage_rows,
        "result_files": result_files,
    }


def collect_run_snapshots(runs_root: Path = RUNS_ROOT, *, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now()
    snapshots: list[dict[str, Any]] = []
    if not runs_root.exists():
        return snapshots
    for run_dir in runs_root.iterdir():
        if not run_dir.is_dir():
            continue
        if not (run_dir / "daily_budget_ledger.json").exists():
            continue
        snapshots.append(build_run_snapshot(run_dir, now=now))
    snapshots.sort(key=lambda row: (row.get("date") or "", row.get("run_tag") or ""), reverse=True)
    return snapshots


def collect_evaluation_reports(evaluations_root: Path = EVALUATIONS_ROOT, *, limit: int = 10) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if not evaluations_root.exists():
        return reports
    for report_path in evaluations_root.glob("*/ablation_report.json"):
        payload = _read_json(report_path, {})
        if not isinstance(payload, dict):
            continue
        fairness = payload.get("fairness") if isinstance(payload.get("fairness"), dict) else {}
        reports.append({
            "run_tag": report_path.parent.name,
            "verdict": payload.get("verdict", "unknown"),
            "comparison_type": fairness.get("comparison_type", "unknown"),
            "missing_variants": fairness.get("missing_variants", []),
            "warnings": fairness.get("warnings", []),
            "metrics": payload.get("metrics", []),
            "variants": payload.get("variants", {}),
            "delta_vs_baseline": payload.get("delta_vs_baseline", {}),
            "report_path": report_path.as_posix(),
            "summary_path": (report_path.parent / "summary.md").as_posix() if (report_path.parent / "summary.md").exists() else None,
            "updated_at": _iso_from_timestamp(report_path.stat().st_mtime),
        })
    reports.sort(key=lambda row: (row.get("updated_at") or "", row.get("run_tag") or ""), reverse=True)
    return reports[:limit]


def build_dashboard_model(runs: list[dict[str, Any]], *, evaluation_reports: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    total_budget = sum(int(run.get("daily_budget") or 0) for run in runs)
    total_spend = sum(int(run.get("inferred_spent_simulations") or 0) for run in runs)
    active_count = sum(1 for run in runs if run.get("health") in {"active", "inferred-active"})
    stalled_count = sum(1 for run in runs if run.get("health") == "stalled")
    complete_count = sum(1 for run in runs if run.get("health") == "complete")
    latest = runs[0] if runs else {}

    summary = {
        "run_count": len(runs),
        "active_count": active_count,
        "stalled_count": stalled_count,
        "complete_count": complete_count,
        "total_budget": total_budget,
        "total_spend": total_spend,
        "budget_percent": round((total_spend / total_budget) * 100.0, 1) if total_budget else 0.0,
        "latest_run_tag": latest.get("run_tag"),
        "latest_stage": latest.get("current_stage"),
    }

    navigation = [
        {"id": "boundaries", "label": "Boundaries"},
        {"id": "behavior", "label": "Behavior"},
        {"id": "memory", "label": "Memory"},
        {"id": "evaluation", "label": "Evaluation"},
        {"id": "runs", "label": "Runs"},
        {"id": "system", "label": "Automation"},
    ]
    evaluation_reports = evaluation_reports or []
    latest_evaluation = evaluation_reports[0] if evaluation_reports else {}
    agent_evaluation = {
        "summary": {
            "report_count": len(evaluation_reports),
            "latest_run_tag": latest_evaluation.get("run_tag"),
            "latest_verdict": latest_evaluation.get("verdict"),
            "latest_comparison_type": latest_evaluation.get("comparison_type"),
        },
        "reports": evaluation_reports,
    }

    memory_layers = [
        {
            "id": "short_term",
            "label": "Short-term memory",
            "zh_label": "短期记忆",
            "scope": "current run evidence",
            "retention": "hours to 3 runs",
            "policy": "promote when evidence repeats or a candidate reaches pass/corr repair lanes; decay when newer runs contradict it.",
            "items": active_count + stalled_count,
        },
        {
            "id": "long_term",
            "label": "Long-term memory",
            "zh_label": "长期记忆",
            "scope": "stable alpha families and behavioral playbooks",
            "retention": "weeks to project lifetime",
            "policy": "store only reusable behavioral theses, operator-field patterns, and failure modes with reproducible evidence.",
            "items": complete_count + len(runs),
        },
        {
            "id": "knowledge_graph",
            "label": "Knowledge graph",
            "zh_label": "知识图谱",
            "scope": "relations among behavior theses, datasets, operators, candidates, and run outcomes",
            "retention": "versioned graph",
            "policy": "merge nodes by canonical behavior logic and keep typed edges for depends_on, contradicts, repairs, and supports.",
            "items": len(runs) * 2,
        },
    ]

    memory_edges = [
        {
            "from": "short_term",
            "to": "long_term",
            "relation": "promotes_to",
            "zh_relation": "晋升为",
            "rule": "repeat evidence, pass result, or reusable repair insight",
        },
        {
            "from": "long_term",
            "to": "knowledge_graph",
            "relation": "grounds",
            "zh_relation": "沉淀到",
            "rule": "canonicalize behavioral thesis, field family, and failure signal",
        },
        {
            "from": "knowledge_graph",
            "to": "short_term",
            "relation": "retrieves_for",
            "zh_relation": "检索增强",
            "rule": "query rewrite, vector retrieval, rerank, and multi-granularity fusion",
        },
    ]

    agent_panels = [
        {
            "title": "Memory briefing",
            "body": "Use recent ledgers, result files, and reflection artifacts to explain which families should scale, repair, decay, or block.",
            "status": "ready" if runs else "empty",
        },
        {
            "title": "Budget planner",
            "body": "Allocate simulation budget across probe, scale, repair, late rescue, and holdout stages with evidence-backed reasons.",
            "status": "active" if active_count else "idle",
        },
        {
            "title": "Submission governance",
            "body": "Rank candidates into champion, follower, repair, blocked, and archive lanes once check and self-correlation data are available.",
            "status": "pending",
        },
    ]

    retrieval_trace = {
        "query": "budget + behavioral boundary",
        "steps": [
            {"stage": "query_rewrite", "body": "Translate budget and behavior boundaries into retrieval intent."},
            {"stage": "fts_recall", "body": "Recall reports, failures, fields, operators, and hypothesis text."},
            {"stage": "graph_expand", "body": "Expand behavior thesis to proxy, skeleton, family, and outcome nodes."},
            {"stage": "rerank", "body": "Penalize non-actionable memory and prefer scale, repair, block, submit actions."},
        ],
    }

    governance_queues = {
        "promotion": ["low-corr near-pass evidence", "submit-ready proxy mapping"],
        "decay": ["budget sink without near-pass", "high self-corr family"],
        "forgetting": ["non-actionable retrieved text", "decorative thesis without proxy"],
        "merge": ["duplicate operator skeleton", "same behavior thesis"],
    }

    hypothesis_ledger = [
        {
            "thesis": "Quality-value mispricing",
            "proxy": "cashflow quality + valuation compression",
            "kill_condition": "high self-corr or LOW_FITNESS",
            "success": "low-corr near-pass or submit-ready candidate",
        },
    ]

    wqb_action_lanes = [
        {"id": "probe", "label": "Probe"},
        {"id": "scale", "label": "Scale"},
        {"id": "repair", "label": "Repair"},
        {"id": "block", "label": "Block"},
        {"id": "submit", "label": "Submit"},
        {"id": "holdout", "label": "Holdout"},
    ]

    adversarial_review = [
        "Reject pure price/volume standalone unless it has a new proxy or repair purpose.",
        "Block duplicate skeletons before spending simulation budget.",
        "Require a kill condition for each budget allocation.",
    ]

    return {
        "summary": summary,
        "navigation": navigation,
        "agent_panels": agent_panels,
        "memory_layers": memory_layers,
        "memory_edges": memory_edges,
        "retrieval_trace": retrieval_trace,
        "governance_queues": governance_queues,
        "hypothesis_ledger": hypothesis_ledger,
        "wqb_action_lanes": wqb_action_lanes,
        "adversarial_review": adversarial_review,
        "agent_evaluation": agent_evaluation,
    }
