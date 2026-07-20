from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from wqb_agent_lab.memory.core.ingest import ingest_runs
from wqb_agent_lab.memory.core.store import SQLiteMemoryStore


TRIAGE_ARTIFACTS = (
    "direct_submit",
    "submit_ready",
    "submission_backlog",
    "optimize_next",
    "low_value_avoid",
    "alpha_skeleton_blocklist",
    "family_efficiency",
    "iteration_state",
    "scan_results_snapshot",
)


@dataclass(frozen=True)
class SyncResult:
    report_path: Path
    nodes_written: int
    edges_written: int
    events_recorded: int


def sync_run_memory(root: Path | str, run_dir: Path | str, *, db_path: Path | str | None = None) -> SyncResult:
    workspace_root = Path(root)
    target_run_dir = Path(run_dir)
    store = SQLiteMemoryStore(Path(db_path) if db_path is not None else workspace_root / ".local" / "data" / "memory" / "alpha_memory.db")
    store.initialize()

    before_events = store.count_events()
    ingest_result = ingest_runs(
        store,
        workspace_root / ".local" / "data" / "runs" / "continuous-alpha",
        run_dirs=[target_run_dir],
    )
    artifact_counts = _record_run_artifact_events(store, workspace_root, target_run_dir)
    repair_effect_summary = _build_repair_effect_summary(target_run_dir / "self_corr_repair_effect_summary.json")
    events_recorded = store.count_events() - before_events

    ledger = _read_json(target_run_dir / "daily_budget_ledger.json", {})
    report = {
        "daily_run_tag": str(ledger.get("daily_run_tag") or target_run_dir.name),
        "run_dir": _relative_path(target_run_dir, workspace_root),
        "db_path": _relative_path(store.db_path, workspace_root),
        "nodes_written": ingest_result.nodes_written,
        "edges_written": ingest_result.edges_written,
        "events_recorded": events_recorded,
        "artifact_counts": artifact_counts,
        "repair_effect_summary": repair_effect_summary,
        "current_stage": ledger.get("current_stage"),
        "spent_simulations": ledger.get("spent_simulations", 0),
    }
    report_path = target_run_dir / "memory_sync_report.json"
    _write_json(report_path, report)
    return SyncResult(
        report_path=report_path,
        nodes_written=ingest_result.nodes_written,
        edges_written=ingest_result.edges_written,
        events_recorded=events_recorded,
    )


def _record_run_artifact_events(store: SQLiteMemoryStore, root: Path, run_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    ledger_path = run_dir / "daily_budget_ledger.json"
    if ledger_path.exists():
        payload = _read_json(ledger_path, {})
        counts["ledger"] = 1 if payload else 0
        store.record_event(
            "memory_sync_ledger",
            _relative_path(run_dir, root),
            {
                "artifact": _relative_path(ledger_path, root),
                "daily_run_tag": payload.get("daily_run_tag") if isinstance(payload, dict) else run_dir.name,
                "current_stage": payload.get("current_stage") if isinstance(payload, dict) else None,
            },
        )

    result_paths = sorted(run_dir.glob("*_results.json"))
    counts["stage_results"] = len(result_paths)
    counts["stage_result_rows"] = sum(_payload_count(_read_json(path, [])) for path in result_paths)
    if result_paths:
        store.record_event(
            "memory_sync_stage_results",
            _relative_path(run_dir, root),
            {
                "artifacts": [_relative_path(path, root) for path in result_paths],
                "file_count": counts["stage_results"],
                "row_count": counts["stage_result_rows"],
            },
        )

    for artifact_name in TRIAGE_ARTIFACTS:
        artifact_path = run_dir / f"{artifact_name}.json"
        if not artifact_path.exists():
            counts[artifact_name] = 0
            continue
        payload = _read_json(artifact_path, [])
        count = _payload_count(payload)
        counts[artifact_name] = count
        store.record_event(
            "memory_sync_triage_artifact",
            f"{_relative_path(run_dir, root)}:{artifact_name}",
            {
                "artifact_name": artifact_name,
                "artifact": _relative_path(artifact_path, root),
                "row_count": count,
            },
        )

    proxy_path = root / ".local" / "data" / "behavioral_proxy" / "behavioral_proxy_map.json"
    if proxy_path.exists():
        payload = _read_json(proxy_path, {})
        mechanisms = payload.get("mechanisms") if isinstance(payload, dict) else []
        counts["behavioral_proxy_map"] = len(mechanisms) if isinstance(mechanisms, list) else 0
        store.record_event(
            "memory_sync_behavioral_proxy_map",
            _relative_path(proxy_path, root),
            {
                "artifact": _relative_path(proxy_path, root),
                "mechanism_count": counts["behavioral_proxy_map"],
            },
        )

    repair_effect_path = run_dir / "self_corr_repair_effect_summary.json"
    if repair_effect_path.exists():
        payload = _read_json(repair_effect_path, {})
        counts["self_corr_repair_effect_summary"] = 1 if isinstance(payload, dict) and payload else 0
        store.record_event(
            "memory_sync_repair_effect_summary",
            _relative_path(repair_effect_path, root),
            {
                "artifact": _relative_path(repair_effect_path, root),
                **_build_repair_effect_summary(repair_effect_path),
            },
        )
    else:
        counts["self_corr_repair_effect_summary"] = 0

    return counts


def _build_repair_effect_summary(path: Path) -> dict[str, Any]:
    payload = _read_json(path, {})
    if not isinstance(payload, dict) or not payload:
        return {}
    accepted_but_unconfirmed = _payload_count(payload.get("accepted_but_unconfirmed", []))
    if not accepted_but_unconfirmed:
        accepted_but_unconfirmed = _payload_count(payload.get("pending_confirmation", []))
    return {
        "repair_simulations": _coerce_int(payload.get("repair_simulations")),
        "repair_metric_pass_clean_or_pending_count": _coerce_int(payload.get("repair_metric_pass_clean_or_pending_count")),
        "submitted_confirmed_count": _payload_count(payload.get("submitted_confirmed", [])),
        "accepted_but_unconfirmed_count": accepted_but_unconfirmed,
        "manual_review_or_platform_lag_count": _payload_count(payload.get("manual_review_or_platform_lag", [])),
        "rejected_count": _payload_count(payload.get("rejected", [])),
        "repair_fail_counts": payload.get("repair_fail_counts") if isinstance(payload.get("repair_fail_counts"), dict) else {},
        "self_corr_bucket_counts": payload.get("self_corr_bucket_counts") if isinstance(payload.get("self_corr_bucket_counts"), dict) else {},
    }


def _payload_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        if "families" in payload and isinstance(payload["families"], list):
            return len(payload["families"])
        if "counts" in payload and isinstance(payload["counts"], dict):
            return int(sum(_coerce_int(value) for value in payload["counts"].values()))
        return 1
    return 0


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
