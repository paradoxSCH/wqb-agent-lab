from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as day_time, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.agent_callbacks import emit_agent_callback
from src.contracts import list_schema_names, schema_digest
from src.diagnosis_policy import evaluate_diagnosis_policies
from src.failure_diagnosis import diagnose_failure_objects, primary_diagnosis_type
from src.llm_provider import LLMProvider
from src.llm_planning import LLMPlanAdapter
from src.output_evaluation.evaluator import write_run_output_evaluation
from src.output_evaluation.types import OutputEvaluationRecord
from src.output_evaluation.validators import validate_expression_candidates
from src.research_policy import (
    ResearchPolicy,
    evaluate_candidate_boundaries,
    load_research_policy,
    policy_digest,
)
from src.self_corr_policy import SELF_CORR_NEAR_REPAIR_MAX, self_corr_bucket as _policy_self_corr_bucket
from wqb_agent_lab.runtime import (
    OperationJournal,
    RunManifest,
    collect_artifact_provenance,
    payload_fingerprint,
)
from wqb_agent_lab.workflow.stages import StageCheckpointStore, StageOutcome, StageRunner


DEFAULT_WORKFLOW_CONFIG = Path(".local/research/workflows/production.json")
RUNS_ROOT = Path(".local/data/runs/continuous-alpha")
CONFIGS_ROOT = Path(".local/research/scans/continuous-alpha")
SUBMITTED_REGISTRY_PATH = Path(".local/data/registry/submitted_alphas.json")
DAY_START_TIME = day_time(0, 0)
REPORT_BASENAME = "submit_summary_budget_complete"
MILD_SELF_CORR_REPAIR_MAX = SELF_CORR_NEAR_REPAIR_MAX
LLM_RETRY_BASE_SECONDS = 30
LLM_RETRY_CAP_SECONDS = 15 * 60
LLM_PROCESS_INSTANCE_ID = uuid.uuid4().hex
LLM_PLAN_POLICY_FIELDS = frozenset(
    {
        "status",
        "pause_reason",
        "code",
        "retryable",
        "attempt_count",
        "last_attempt_at",
        "next_retry_at",
        "config_digest",
        "process_instance_id",
    }
)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload: Any) -> None:
    from src.atomic_json import atomic_write_json

    atomic_write_json(path, payload)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_file_fresh(path: Path, max_age_seconds: int) -> bool:
    if not path.exists() or max_age_seconds <= 0:
        return False
    age_seconds = max(0.0, time.time() - path.stat().st_mtime)
    return age_seconds <= max_age_seconds


def yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def daily_run_tag(value: date, prefix: str = "wqb-agent-research") -> str:
    return f"{prefix}-{yyyymmdd(value)}"


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


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
    if not isinstance(payload, dict):
        return set()
    blocking_statuses = {"submitted_confirmed", "pending_confirmation", "post_accepted"}
    alpha_ids: set[str] = set()
    for job in payload.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        status = str(job.get("status") or "")
        alpha_id = str(job.get("alpha_id") or "").strip()
        if alpha_id and status in blocking_statuses:
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


@dataclass
class StagePlan:
    stage: str
    budget: int
    remaining_stage_budget: int
    remaining_daily_budget: int
    source_config: Path | None = None
    sliced_config: Path | None = None
    output_path: Path | None = None
    candidate_count: int = 0
    action: str = "none"


class KimiDailyWorkflow:
    def __init__(
        self,
        workspace_root: Path,
        *,
        workflow_config: Path = DEFAULT_WORKFLOW_CONFIG,
        run_date: date | None = None,
        budget_mode: str | None = None,
        execute_scans: bool = False,
        dry_run: bool = False,
        llm_provider: LLMProvider | None = None,
        process_instance_id: str | None = None,
    ) -> None:
        self.root = workspace_root.resolve()
        load_dotenv(self.root / ".env")
        self.workflow_config_path = (self.root / workflow_config).resolve() if not workflow_config.is_absolute() else workflow_config
        if not self.workflow_config_path.exists():
            raise FileNotFoundError(f"Workflow config does not exist: {self.workflow_config_path}")
        self.config = read_json(self.workflow_config_path, {})
        self.research_policy: ResearchPolicy | None = (
            load_research_policy(self.config) if "research_policy" in self.config else None
        )
        self._active_ledger: dict[str, Any] | None = None
        self.llm_adapter = LLMPlanAdapter.from_config(
            self.config,
            workspace_root=self.root,
            llm_provider=llm_provider,
        )
        self.process_instance_id = process_instance_id or LLM_PROCESS_INSTANCE_ID
        self.run_tag_prefix = str(self.config.get("daily_run_tag_prefix") or "wqb-agent-research")
        self._set_run_date(run_date or date.today())
        self.budget_mode = budget_mode or self.config.get("capacity_estimate", {}).get("recommended_mode") or "standard"
        self.execute_scans = execute_scans
        self.dry_run = dry_run

    def sync_submitted_registry(self) -> str:
        if self.dry_run:
            return "skipped_dry_run"
        if self.config.get("submitted_registry_sync_enabled") is False:
            return "skipped_disabled"
        if str(os.getenv("WQB_SKIP_SUBMITTED_REGISTRY_SYNC", "")).strip().lower() in {"1", "true", "yes", "on"}:
            return "skipped_env"
        if not os.getenv("WQB_EMAIL") or not os.getenv("WQB_PASSWORD"):
            return "skipped_missing_credentials"
        state_path = self.root / ".local" / "data" / "registry" / "registry_state.json"
        max_age_seconds = int(self.config.get("submitted_registry_cache_max_age_seconds") or 1800)
        if _json_file_fresh(state_path, max_age_seconds):
            state = read_json(state_path, {})
            if isinstance(state, dict) and state.get("status") == "ok":
                return "cache_ok"
        command = [
            sys.executable,
            "-m",
            "scripts.workers.registry",
            "--workspace-root",
            str(self.root),
            "--once",
        ]
        log_path = self.root / ".local" / "data" / "registry" / "registry_worker.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with open(log_path, "a", encoding="utf-8") as log_fh:
                subprocess.Popen(
                    command,
                    cwd=self.root,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
        except Exception as exc:
            return f"worker_launch_failed:{str(exc)[:120]}"
        return "worker_started"

    def _submitted_registry(self) -> tuple[set[str], set[str]]:
        payload = read_json(self.root / SUBMITTED_REGISTRY_PATH, {})
        if not isinstance(payload, dict):
            submitted_alpha_ids: set[str] = set()
            submitted_expressions: set[str] = set()
        else:
            submitted_alpha_ids, submitted_expressions = submitted_registry_entries(payload)
        submitted_alpha_ids.update(self._confirmed_submission_state_alpha_ids())
        return submitted_alpha_ids, submitted_expressions

    def _confirmed_submission_state_alpha_ids(self) -> set[str]:
        alpha_ids: set[str] = set()
        runs_root = self.root / RUNS_ROOT
        if not runs_root.exists():
            return alpha_ids
        for path in runs_root.glob("*/submission_state.json"):
            alpha_ids.update(confirmed_submission_state_alpha_ids(read_json(path, {})))
        return alpha_ids

    def _failed_submit_attempt_alpha_ids(self) -> set[str]:
        data_roots = [self.root / RUNS_ROOT, self.root / ".local" / "data"]
        patterns = ["**/*submit*_results*.json", "**/*resubmit*.json", "**/submission_attempts*.json"]
        paths: set[Path] = set()
        for data_root in data_roots:
            if not data_root.exists():
                continue
            for pattern in patterns:
                paths.update(path for path in data_root.glob(pattern) if path.is_file())
        failed: set[str] = set()
        for path in sorted(paths):
            failed.update(failed_submit_attempt_alpha_ids(read_json(path, {})))
        return failed

    def _preferred_live_check_paths(self) -> list[Path]:
        current_paths = sorted(self.run_dir.glob("live-check-final/*.json"))
        if current_paths:
            return current_paths
        return sorted((self.root / RUNS_ROOT).glob("*/live-check-final/*.json"))

    def _current_scan_result_paths(self) -> list[Path]:
        return sorted(self.run_dir.glob("*_results.json"))

    def _row_family(self, row: dict[str, Any]) -> str:
        for key in ("behavior_family", "family", "route_family"):
            value = str(row.get(key) or "").strip()
            if value:
                return value
        note = str(row.get("note") or "")
        if ":" in note:
            return note.split(":", 1)[0].strip() or "unknown"
        return "unknown"

    def _row_skeleton(self, row: dict[str, Any]) -> str:
        for key in ("skeleton", "chassis"):
            value = str(row.get(key) or "").strip()
            if value:
                return value
        family = self._row_family(row)
        field = candidate_field_hint(str(row.get("expression") or ""))
        return f"{family}:{field}" if field != "unknown" else family

    def _current_scan_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self._current_scan_result_paths():
            payload = read_json(path, [])
            if not isinstance(payload, list):
                continue
            for result in payload:
                if not isinstance(result, dict):
                    continue
                row = dict(result)
                row["source_path"] = relative_path(path, self.root)
                rows.append(row)
        return rows

    def _classify_scan_row(
        self,
        row: dict[str, Any],
        submitted_alpha_ids: set[str],
        submitted_expressions: set[str],
        failed_submit_alpha_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        checks = row.get("checks") or []
        expression = normalize_expression(str(row.get("expression") or ""))
        alpha_id = str(row.get("alpha_id") or "").strip()
        failed_submit_alpha_ids = failed_submit_alpha_ids or set()
        already_submitted = bool(
            (alpha_id and alpha_id in submitted_alpha_ids)
            or (expression and expression in submitted_expressions)
        )
        previous_submit_failed = bool(alpha_id and alpha_id in failed_submit_alpha_ids)
        enriched = dict(row)
        enriched.update({
            "alpha_id": alpha_id,
            "expression": expression,
            "family": self._row_family(row),
            "skeleton": self._row_skeleton(row),
            "failed_checks": failed_check_names(checks),
            "pending_checks": pending_check_names(checks),
            "warning_checks": warning_check_names(checks),
            "units_warning": units_warning_from_check_list(checks),
            "self_corr": check_value(checks, "SELF_CORRELATION"),
            "sub_universe_sharpe": check_value(checks, "LOW_SUB_UNIVERSE_SHARPE"),
            "already_submitted": already_submitted,
            "previous_submit_failed": previous_submit_failed,
            "score": round(candidate_score(row), 4),
        })
        enriched["failure_diagnoses"] = diagnose_failure_objects(enriched)
        failures = set(enriched["failed_checks"])
        self_corr_bucket = self_corr_bucket_from_checks(checks)
        if already_submitted:
            bucket = "already_submitted"
            route = "skip_already_submitted"
        elif previous_submit_failed:
            bucket = "low_value"
            route = "skip_previous_submit_unconfirmed"
        elif row_metric_pass(row) and not failed_checks_from_check_list(checks):
            bucket = "direct_submit"
            route = "live_recheck_then_submit"
        elif "SELF_CORRELATION" in failures and self_corr_bucket == "extreme":
            bucket = "low_value"
            route = "replace_overcrowded_signal"
        elif "SELF_CORRELATION" in failures and self_corr_bucket == "mild" and row_near_pass(row):
            bucket = "optimize_next"
            route = "self_corr_light_repair"
        elif "SELF_CORRELATION" in failures and row_near_pass(row):
            bucket = "low_value"
            route = "self_corr_escape"
        elif "CONCENTRATED_WEIGHT" in failures and weight_concentration_bucket_from_checks(checks) == "severe":
            bucket = "low_value"
            route = "replace_concentrated_expression_structure"
        elif "CONCENTRATED_WEIGHT" in failures:
            bucket = "optimize_next" if row_near_pass(row) else "low_value"
            route = "smooth_or_truncate_weight_concentration"
        elif "LOW_SUB_UNIVERSE_SHARPE" in failures and sub_universe_bucket_from_checks(checks) == "severe":
            bucket = "low_value"
            route = "replace_unstable_universe_proxy"
        elif "LOW_SUB_UNIVERSE_SHARPE" in failures and sub_universe_bucket_from_checks(checks) == "moderate":
            bucket = "optimize_next" if row_near_pass(row) else "low_value"
            route = "controlled_sub_universe_repair"
        elif ("LOW_SHARPE" in failures or "LOW_FITNESS" in failures) and weak_signal_bucket_from_row(row) == "deep_fail":
            bucket = "low_value"
            route = "replace_weak_behavior_proxy"
        elif ("LOW_SHARPE" in failures or "LOW_FITNESS" in failures) and weak_signal_bucket_from_row(row) == "medium_gap":
            bucket = "low_value"
            route = "rewrite_weak_signal_chassis"
        elif row_near_pass(row):
            bucket = "optimize_next"
            route = "structural_repair_or_parameter_sweep"
        else:
            bucket = "low_value"
            route = "avoid_unchanged"
        enriched["triage_bucket"] = bucket
        enriched["route_decision"] = route
        return enriched

    def _classified_scan_rows(self) -> list[dict[str, Any]]:
        submitted_alpha_ids, submitted_expressions = self._submitted_registry()
        failed_submit_alpha_ids = self._failed_submit_attempt_alpha_ids()
        return [
            self._classify_scan_row(row, submitted_alpha_ids, submitted_expressions, failed_submit_alpha_ids)
            for row in self._current_scan_rows()
        ]

    def _low_value_avoid_entries(self, low_value_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in low_value_rows:
            grouped.setdefault(str(row.get("skeleton") or "unknown"), []).append(row)
        entries: list[dict[str, Any]] = []
        for skeleton, rows in sorted(grouped.items()):
            best = max(rows, key=lambda item: float(item.get("score") or 0.0))
            blockers = best.get("failed_checks") or best.get("pending_checks") or ["weak_signal_quality"]
            entries.append({
                "family": best.get("family") or "unknown",
                "skeleton": skeleton,
                "reason": ", ".join(str(item) for item in blockers),
                "primary_diagnosis_type": primary_diagnosis_type(best),
                "failure_diagnoses": best.get("failure_diagnoses") or diagnose_failure_objects(best),
                "avoid_mode": "do_not_regenerate_unchanged",
                "representative_alphas": [row.get("alpha_id") for row in rows if row.get("alpha_id")],
                "best_sharpe": metric_value(best, "sharpe"),
                "best_fitness": metric_value(best, "fitness"),
                "source_paths": sorted({str(row.get("source_path") or "") for row in rows if row.get("source_path")}),
            })
        return entries

    def _dedupe_triage_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        best_by_key: dict[str, dict[str, Any]] = {}
        for row in rows:
            alpha_id = str(row.get("alpha_id") or "").strip()
            expression = normalize_expression(str(row.get("expression") or ""))
            settings = json.dumps(row.get("settings") or {}, sort_keys=True, ensure_ascii=False)
            key = alpha_id or f"{expression}|{settings}"
            if not key.strip("|"):
                continue
            existing = best_by_key.get(key)
            if existing is None or float(row.get("score") or 0.0) > float(existing.get("score") or 0.0):
                best_by_key[key] = row
        return sorted(best_by_key.values(), key=lambda row: float(row.get("score") or 0.0), reverse=True)

    def _family_efficiency(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        families: dict[str, dict[str, Any]] = {}
        for row in rows:
            family = str(row.get("family") or "unknown")
            entry = families.setdefault(
                family,
                {
                    "family": family,
                    "tested_count": 0,
                    "direct_submit_count": 0,
                    "optimize_next_count": 0,
                    "low_value_count": 0,
                    "already_submitted_count": 0,
                    "local_pass_count": 0,
                    "best_alpha_id": None,
                    "best_score": None,
                    "best_sharpe": None,
                    "best_fitness": None,
                },
            )
            entry["tested_count"] += 1
            bucket = str(row.get("triage_bucket") or "low_value")
            count_key = f"{bucket}_count"
            if count_key in entry:
                entry[count_key] += 1
            if row_metric_pass(row):
                entry["local_pass_count"] += 1
            score = float(row.get("score") or 0.0)
            if entry["best_score"] is None or score > float(entry["best_score"]):
                entry["best_alpha_id"] = row.get("alpha_id")
                entry["best_score"] = score
                entry["best_sharpe"] = metric_value(row, "sharpe")
                entry["best_fitness"] = metric_value(row, "fitness")
        ordered = sorted(
            families.values(),
            key=lambda item: (
                -int(item.get("direct_submit_count") or 0),
                -int(item.get("optimize_next_count") or 0),
                -float(item.get("best_score") or 0.0),
                str(item.get("family") or ""),
            ),
        )
        return {
            "family_count": len(ordered),
            "families": ordered,
        }

    def write_closed_loop_artifacts(
        self,
        ledger: dict[str, Any],
        *,
        ready: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now()
        ready_rows = ready if ready is not None else self.collect_submit_ready()
        scan_rows = self._classified_scan_rows()
        direct_submit = self._dedupe_triage_rows([row for row in scan_rows if row.get("triage_bucket") == "direct_submit"])
        optimize_next = self._dedupe_triage_rows([row for row in scan_rows if row.get("triage_bucket") == "optimize_next"])
        low_value_rows = [row for row in scan_rows if row.get("triage_bucket") == "low_value"]
        low_value_avoid = self._low_value_avoid_entries(low_value_rows)
        family_efficiency = self._family_efficiency(scan_rows)
        submit_backlog = []
        for row in ready_rows:
            action = "live_recheck_then_submit" if row.get("requires_live_recheck") else "submit"
            submit_backlog.append({**row, "recommended_action": action})

        paths = {
            "scan_results_snapshot": self.run_dir / "scan_results_snapshot.json",
            "direct_submit": self.run_dir / "direct_submit.json",
            "submit_ready": self.run_dir / "submit_ready.json",
            "submission_backlog": self.run_dir / "submission_backlog.json",
            "optimize_next": self.run_dir / "optimize_next.json",
            "low_value_avoid": self.run_dir / "low_value_avoid.json",
            "alpha_skeleton_blocklist": self.run_dir / "alpha_skeleton_blocklist.json",
            "family_efficiency": self.run_dir / "family_efficiency.json",
            "diagnosis_policy_evaluation": self.run_dir / "diagnosis_policy_evaluation.json",
            "diagnosis_policy_summary": self.run_dir / "diagnosis_policy_evaluation.md",
            "output_evaluation_report": self.run_dir / "output_evaluation_report.json",
            "output_evaluation_summary": self.run_dir / "output_evaluation_summary.md",
            "iteration_state": self.run_dir / "iteration_state.json",
            "triage_summary": self.run_dir / "triage_summary.md",
        }
        blocklist = [
            {
                "skeleton": entry.get("skeleton"),
                "family": entry.get("family"),
                "reason": entry.get("reason"),
                "primary_diagnosis_type": entry.get("primary_diagnosis_type"),
                "failure_diagnoses": entry.get("failure_diagnoses") or [],
                "status": "blocked_unchanged",
            }
            for entry in low_value_avoid
        ]
        artifacts = {key: relative_path(path, self.root) for key, path in paths.items()}
        diagnosis_policy = evaluate_diagnosis_policies(scan_rows)
        state = {
            "daily_run_tag": self.run_tag,
            "generated_at": now.isoformat(timespec="seconds"),
            "workflow_config_path": self._workflow_config_reference(),
            "current_stage": ledger.get("current_stage"),
            "counts": {
                "scan_rows": len(scan_rows),
                "direct_submit": len(direct_submit),
                "submit_ready": len(ready_rows),
                "submission_backlog": len(submit_backlog),
                "optimize_next": len(optimize_next),
                "low_value": len(low_value_rows),
                "already_submitted": sum(1 for row in scan_rows if row.get("triage_bucket") == "already_submitted"),
            },
            "artifacts": artifacts,
            "next_actions": [
                "Run live checks for submission_backlog rows where requires_live_recheck is true.",
                "Submit live-check-clean rows in score order.",
                "Use optimize_next rows for structural repair or parameter sweeps.",
                "Do not regenerate low_value_avoid skeletons unchanged.",
            ],
        }
        summary_lines = [
            "# Daily Closed Loop Triage",
            "",
            f"Daily run: `{self.run_tag}`",
            f"Generated at: `{state['generated_at']}`",
            f"Scan rows: `{len(scan_rows)}`",
            f"Direct-submit local PASS: `{len(direct_submit)}`",
            f"Submit-ready backlog: `{len(submit_backlog)}`",
            f"Optimize-next: `{len(optimize_next)}`",
            f"Low-value rows: `{len(low_value_rows)}`",
            "",
            "## Best Direct Submit",
        ]
        if direct_submit:
            for row in direct_submit[:10]:
                summary_lines.append(
                    f"- `{row.get('alpha_id')}` S={metric_value(row, 'sharpe')} "
                    f"F={metric_value(row, 'fitness')} T={metric_value(row, 'turnover', 1.0)} "
                    f"family={row.get('family')} score={row.get('score')}"
                )
        else:
            summary_lines.append("- None.")
        summary_lines.extend(["", "## Best Optimize Next"])
        if optimize_next:
            for row in optimize_next[:10]:
                blockers = ",".join(row.get("failed_checks") or row.get("pending_checks") or []) or "near_metric_threshold"
                diagnosis = primary_diagnosis_type(row)
                summary_lines.append(
                    f"- `{row.get('alpha_id')}` blockers={blockers} "
                    f"diagnosis={diagnosis} "
                    f"S={metric_value(row, 'sharpe')} F={metric_value(row, 'fitness')} "
                    f"family={row.get('family')}"
                )
        else:
            summary_lines.append("- None.")
        summary_lines.extend(["", "## Low Value Skeletons"])
        if low_value_avoid:
            summary_lines.extend(
                f"- `{entry.get('skeleton')}` diagnosis={entry.get('primary_diagnosis_type')} reason={entry.get('reason')}"
                for entry in low_value_avoid[:20]
            )
        else:
            summary_lines.append("- None.")

        if not self.dry_run:
            write_json(paths["scan_results_snapshot"], scan_rows)
            write_json(paths["direct_submit"], direct_submit)
            write_json(paths["submit_ready"], ready_rows)
            write_json(paths["submission_backlog"], submit_backlog)
            write_json(paths["optimize_next"], optimize_next)
            write_json(paths["low_value_avoid"], low_value_avoid)
            write_json(paths["alpha_skeleton_blocklist"], blocklist)
            write_json(paths["family_efficiency"], {**family_efficiency, "generated_at": state["generated_at"]})
            write_json(paths["diagnosis_policy_evaluation"], diagnosis_policy)
            write_text(paths["diagnosis_policy_summary"], self._diagnosis_policy_summary(diagnosis_policy))
            write_json(paths["iteration_state"], state)
            write_text(paths["triage_summary"], "\n".join(summary_lines) + "\n")
            memory_sync_report = self._post_stage_memory_sync()
            if memory_sync_report:
                state["artifacts"]["memory_sync_state"] = memory_sync_report
            output_report_path, output_summary_path = write_run_output_evaluation(self.run_dir)
            state["artifacts"]["output_evaluation_report"] = relative_path(output_report_path, self.root)
            state["artifacts"]["output_evaluation_summary"] = relative_path(output_summary_path, self.root)
            write_json(paths["iteration_state"], state)
        ledger["closed_loop"] = state
        return state

    def _workflow_config_reference(self) -> str:
        try:
            return relative_path(self.workflow_config_path, self.root)
        except ValueError:
            return self.workflow_config_path.as_posix()

    def _diagnosis_policy_summary(self, report: dict[str, Any]) -> str:
        lines = [
            "# Diagnosis Policy Evaluation",
            "",
            f"Daily run: `{self.run_tag}`",
            f"Generated at: `{report.get('generated_at')}`",
            f"Rows: `{report.get('total_rows')}`",
            f"Diagnoses: `{report.get('total_diagnoses')}`",
            f"Budget saved estimate: `{report.get('budget_saved_estimate')}`",
            "",
            "## Policies",
        ]
        for policy in report.get("policies", []):
            lines.extend(
                [
                    "",
                    f"### `{policy.get('diagnosis_type')}`",
                    f"- Recommended policy: `{policy.get('recommended_policy')}`",
                    f"- Budget policy: `{policy.get('budget_policy')}`",
                    f"- Observed: `{policy.get('observed_count')}`",
                    f"- Repair rate: `{policy.get('repair_candidate_rate')}`",
                    f"- Blocked rate: `{policy.get('blocked_rate')}`",
                    f"- Confidence: `{policy.get('policy_confidence')}`",
                    f"- Next action: {policy.get('next_action')}",
                ]
            )
        return "\n".join(lines) + "\n"

    def _post_stage_memory_sync(self) -> str | None:
        config = self.config.get("post_stage_memory_sync") or {}
        if not isinstance(config, dict) or not config.get("enabled"):
            return None
        db_path = self.root / str(config.get("db_path") or ".local/data/memory/alpha_memory.db")
        state_path = self.run_dir / "memory_sync_state.json"
        log_path = self.run_dir / "memory_worker.log"
        command = [
            sys.executable,
            "-m",
            "scripts.workers.memory",
            "--workspace-root",
            str(self.root),
            "--run-dir",
            str(self.run_dir),
            "--db",
            str(db_path),
            "--once",
        ]
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with open(log_path, "a", encoding="utf-8") as log_fh:
                subprocess.Popen(
                    command,
                    cwd=self.root,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
        except Exception as exc:
            write_json(
                state_path,
                {
                    "status": "launch_failed",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "error": str(exc)[:500],
                },
            )
        return relative_path(state_path, self.root)

    def _candidate_row_paths(self) -> list[Path]:
        current_paths = sorted(self.run_dir.glob("direct_submit*.json"))
        current_paths.extend(self._current_scan_result_paths())
        snapshot = self.run_dir / "current_submit_candidate_snapshot.json"
        if snapshot.exists():
            current_paths.append(snapshot)

        seen = {path.resolve() for path in current_paths}
        historical_paths: list[Path] = []
        patterns = ["*/direct_submit*.json", "*/direct_submit_pre_corr.json", "*/current_submit_candidate_snapshot.json"]
        for pattern in patterns:
            for path in sorted((self.root / RUNS_ROOT).glob(pattern)):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                historical_paths.append(path)
                seen.add(resolved)
        return current_paths + historical_paths

    def _set_run_date(self, run_date: date) -> None:
        self.run_date = run_date
        self.run_tag = daily_run_tag(self.run_date, self.run_tag_prefix)
        self.run_dir = self.root / RUNS_ROOT / self.run_tag
        self.config_dir = self.root / CONFIGS_ROOT / self.run_tag
        self.ledger_path = self.run_dir / "daily_budget_ledger.json"
        self.manifest_path = self.run_dir / "run_manifest.json"
        self.operation_journal = OperationJournal(self.run_dir / "operations.db")
        self.stage_checkpoint_store = StageCheckpointStore(self.run_dir)

    def _run_manifest(
        self,
        *,
        now: datetime,
        status: str,
        error_type: str = "",
    ) -> RunManifest:
        existing = read_json(self.manifest_path, {})
        if not isinstance(existing, dict):
            existing = {}
        created_at = str(existing.get("created_at") or now.isoformat(timespec="seconds"))
        llm_settings = self.config.get("llm_provider") or {}
        if not isinstance(llm_settings, dict):
            llm_settings = {}
        ledger = read_json(self.ledger_path, {})
        if not isinstance(ledger, dict):
            ledger = {}
        try:
            config_path = self.workflow_config_path.relative_to(self.root).as_posix()
        except ValueError:
            config_path = self.workflow_config_path.name
        manifest = RunManifest.create(
            run_id=self.run_tag,
            created_at=created_at,
            code={
                "component": "src.kimi_daily_workflow.KimiDailyWorkflow",
                "revision": str(os.getenv("GITHUB_SHA") or ""),
            },
            runtime={
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.system().lower(),
                "dependency_lock_sha256": _file_sha256(self.root / "uv.lock"),
                "execute_scans": self.execute_scans,
                "dry_run": self.dry_run,
            },
            configuration={
                "path": config_path,
                "sha256": hashlib.sha256(self.workflow_config_path.read_bytes()).hexdigest(),
            },
            llm={
                "provider": self.llm_adapter.provider,
                "model": self.llm_adapter.model,
                "output_contract": str(llm_settings.get("output_contract") or "legacy"),
            },
            research={
                "run_date": self.run_date.isoformat(),
                "budget_mode": self.budget_mode,
                "current_stage": str(ledger.get("current_stage") or ""),
                "schema_digests": {name: schema_digest(name) for name in list_schema_names()},
            },
            extensions={
                "checkpoint_status": status,
                "checkpointed_at": now.isoformat(timespec="seconds"),
                "error_type": error_type,
            },
        )
        artifacts = collect_artifact_provenance(
            self.root,
            self.run_dir,
            exclude=(self.manifest_path,),
            producer="kimi_daily_workflow",
        )
        artifacts += collect_artifact_provenance(
            self.root,
            self.config_dir,
            producer="kimi_daily_workflow",
        )
        return manifest.with_artifacts(artifacts)

    def write_run_manifest(
        self,
        *,
        now: datetime,
        status: str,
        error_type: str = "",
    ) -> Path:
        manifest = self._run_manifest(now=now, status=status, error_type=error_type)
        write_json(self.manifest_path, manifest.to_dict())
        return self.manifest_path

    def _enqueue_stage_event(
        self,
        event_type: str,
        ledger: dict[str, Any],
        *,
        stage: str,
        extra: dict[str, Any],
    ) -> None:
        payload = {
            "ledger": ledger,
            "stage": stage,
            "callback_event": event_type,
            "extra": extra,
        }
        identity = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:20]
        self.operation_journal.enqueue(
            "workflow.stage_finalized",
            payload,
            idempotency_key=f"{self.run_tag}:{stage}:{identity}",
        )

    def drain_workflow_outbox(self) -> int:
        delivered = 0
        for event in self.operation_journal.pending_events():
            if event["event_type"] != "workflow.stage_finalized":
                continue
            payload = event["payload"]
            try:
                ledger = dict(payload["ledger"])
                self._active_ledger = ledger
                self._score_decision_attribution()
                self.write_closed_loop_artifacts(ledger)
                self._emit_progress_callback(
                    str(payload["callback_event"]),
                    ledger,
                    stage=str(payload["stage"]),
                    extra=dict(payload.get("extra") or {}),
                )
                write_json(self.ledger_path, ledger)
                self.operation_journal.mark_delivered(event["event_id"])
                delivered += 1
            except Exception as exc:
                self.operation_journal.mark_failed(event["event_id"], str(exc))
        return delivered

    def advance_to_next_day(self) -> None:
        self._set_run_date(self.run_date + timedelta(days=1))

    def _pick_scan_config(self) -> str | None:
        """自动选择最佳 scan config：优先从未使用或久未使用、且历史产出高的 config。"""
        configs = sorted((self.root / CONFIGS_ROOT).glob("*/scan_config_round*.json"))
        if not configs:
            return None

        config_scores: dict[str, tuple[float, int]] = {}
        for config_path in configs:
            rel_path = relative_path(config_path, self.root)
            last_used: date | None = None
            total_yield = 0
            run_count = 0

            for ledger_path in (self.root / RUNS_ROOT).glob("*/daily_budget_ledger.json"):
                ledger_data = read_json(ledger_path, {})
                queued = ledger_data.get("queued_scan_configs") or []
                if rel_path in queued:
                    run_date_str = ledger_data.get("date", "")
                    try:
                        run_date = datetime.strptime(run_date_str, "%Y-%m-%d").date()
                    except (ValueError, TypeError):
                        continue
                    if last_used is None or run_date > last_used:
                        last_used = run_date
                        closed_loop = ledger_data.get("closed_loop", {})
                    run_count += 1
                    total_yield += closed_loop.get("counts", {}).get("submit_ready", 0)

            if last_used is None:
                score = 1000.0
            else:
                days_since = (self.run_date - last_used).days
                # 越久未用分数越高；历史产出也会加分；但最近 2 天内用过大幅降权
                if days_since <= 1:
                    score = -500.0
                else:
                    score = days_since * 50.0 + total_yield * 20.0

            # 用 config 文件大小（候选数代理）做 tie-breaker
            try:
                file_size = config_path.stat().st_size
            except OSError:
                file_size = 0
            config_scores[rel_path] = (score, file_size)

        if not config_scores:
            return None

        best = max(config_scores, key=lambda k: config_scores[k])
        best_score = config_scores[best][0]
        if best_score < 0:
            # 所有 config 都最近用过，回退到 default
            return None
        return best

    def load_or_create_ledger(self) -> dict[str, Any]:
        modes = self.config.get("daily_budget_modes") or {}
        mode_config = modes.get(self.budget_mode) or modes.get("standard") or {}
        if self.research_policy is not None:
            daily_budget = self.research_policy.budget.daily_simulation_limit
            stage_budgets = dict(self.research_policy.budget.stage_allocations)
            stage_order = list(stage_budgets)
        else:
            daily_budget = int(mode_config.get("daily_budget") or 1000)
            stage_budgets = mode_config.get("stage_budgets") or {}
            stage_order = self.config.get("stage_order") or []
        capacity = self.config.get("capacity_estimate") or {}
        default_queued = list(self.config.get("default_queued_scan_configs") or [])
        existing = read_json(self.ledger_path, {})

        # 如果是新的一天（ledger 不存在或日期不匹配），自动选择最佳 scan config
        # 但优先尊重 workflow config 中显式配置的 default_queued_scan_configs
        is_new_day = not existing or existing.get("date") != self.run_date.isoformat()
        if is_new_day:
            workflow_default = list(self.config.get("default_queued_scan_configs") or [])
            if workflow_default:
                default_queued = workflow_default
                print(f"[workflow] using workflow-default scan config for {self.run_tag}: {workflow_default}")
            else:
                picked = self._pick_scan_config()
                if picked:
                    default_queued = [picked]
                    print(f"[workflow] auto-picked scan config for {self.run_tag}: {picked}")

        default_ledger = {
            "daily_run_tag": self.run_tag,
            "date": self.run_date.isoformat(),
            "budget_mode": self.budget_mode,
            "daily_budget": daily_budget,
            "spent_simulations": 0,
            "committed_simulations": 0,
            "remaining_uncommitted_simulations": daily_budget,
            "remaining_simulations_after_commitments": daily_budget,
            "max_scan_concurrency": int(capacity.get("max_scan_concurrency") or 3),
            "stage_order": stage_order,
            "stage_budgets": stage_budgets,
            "stage_spend": {},
            "stage_commitments": {},
            "current_stage": "initialized",
            "queued_scan_configs": default_queued,
            "running_terminal_ids": [],
            "capacity_basis": {
                "measured_simulations": capacity.get("measured_simulations"),
                "measured_proxy_wall_hours": capacity.get("measured_proxy_wall_hours"),
                "measured_simulations_per_hour": capacity.get("measured_simulations_per_hour"),
                "full_day_projection": capacity.get("full_day_projection"),
            },
            "llm_provider": self.llm_adapter.metadata(),
            "notes": [
                f"{self.llm_adapter.display_name} planning does not consume WQB simulation budget.",
                "Only BRAIN simulate calls increment spent_simulations.",
                "Scan stages are sliced to the smaller of stage budget and remaining daily budget.",
            ],
        }
        ledger = {**default_ledger, **existing}
        ledger["llm_provider"] = self.llm_adapter.metadata()
        if self.research_policy is not None:
            ledger["daily_budget"] = daily_budget
            ledger["stage_order"] = stage_order
            ledger["stage_budgets"] = stage_budgets
            fresh_policy_metadata = self._research_policy_metadata()
            existing_policy_metadata = existing.get("research_policy") if isinstance(existing, dict) else None
            if (
                isinstance(existing_policy_metadata, dict)
                and existing_policy_metadata.get("digest") == fresh_policy_metadata["digest"]
            ):
                fresh_policy_metadata.update(
                    {
                        key: existing_policy_metadata.get(key, fresh_policy_metadata[key])
                        for key in ("evaluated_candidates", "allowed_candidates", "blocked_candidates", "block_counts")
                    }
                )
            ledger["research_policy"] = fresh_policy_metadata
        ledger.setdefault("stage_spend", {})
        ledger.setdefault("stage_commitments", {})
        ledger.setdefault("queued_scan_configs", [])
        if not ledger.get("queued_scan_configs") and default_queued:
            ledger["queued_scan_configs"] = default_queued
        self._refresh_remaining(ledger)
        self._active_ledger = ledger
        if not self.dry_run:
            write_json(self.ledger_path, ledger)
        return ledger

    def _research_policy_metadata(self) -> dict[str, Any]:
        if self.research_policy is None:
            return {}
        return {
            "version": self.research_policy.version,
            "digest": policy_digest(self.research_policy),
            "exploration_share_limit": self.research_policy.budget.exploration_share_limit,
            "exploration_stages": list(self.research_policy.budget.exploration_stages),
            "enabled_mechanisms": list(self.research_policy.enabled_mechanism_ids),
            "evaluated_candidates": 0,
            "allowed_candidates": 0,
            "blocked_candidates": 0,
            "block_counts": {},
        }

    def _refresh_remaining(self, ledger: dict[str, Any]) -> None:
        daily_budget = int(ledger.get("daily_budget") or 0)
        spent = int(ledger.get("spent_simulations") or 0)
        committed = int(ledger.get("committed_simulations") or 0)
        ledger["remaining_uncommitted_simulations"] = max(0, daily_budget - spent)
        ledger["remaining_simulations_after_commitments"] = max(0, daily_budget - spent - committed)

    def _emit_progress_callback(
        self,
        event_type: str,
        ledger: dict[str, Any],
        *,
        stage: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self.dry_run:
            return
        payload = {
            "run_tag": self.run_tag,
            "run_dir": relative_path(self.run_dir, self.root),
            "ledger_path": relative_path(self.ledger_path, self.root),
            "stage": stage or ledger.get("current_stage"),
            "current_stage": ledger.get("current_stage"),
            "spent_simulations": int(ledger.get("spent_simulations") or 0),
            "daily_budget": int(ledger.get("daily_budget") or 0),
            "remaining_simulations_after_commitments": int(ledger.get("remaining_simulations_after_commitments") or 0),
            "closed_loop_counts": self._callback_closed_loop_counts(ledger),
            "recommended_control_action": self._recommended_control_action(ledger),
        }
        if extra:
            payload.update(extra)
        result = emit_agent_callback(self.root, event_type, payload)
        if result.event_path is not None:
            ledger["last_agent_callback_event"] = relative_path(result.event_path, self.root)
        if result.webhook_status:
            ledger["last_agent_callback_webhook_status"] = result.webhook_status
        if result.error:
            ledger["last_agent_callback_error"] = result.error

    def _callback_closed_loop_counts(self, ledger: dict[str, Any]) -> dict[str, int]:
        counts = ((ledger.get("closed_loop") or {}).get("counts") or {}) if isinstance(ledger.get("closed_loop"), dict) else {}
        return {
            "scan_rows": int(counts.get("scan_rows") or 0),
            "direct_submit": int(counts.get("direct_submit") or 0),
            "submit_ready": int(counts.get("submit_ready") or 0),
            "submission_backlog": int(counts.get("submission_backlog") or 0),
            "optimize_next": int(counts.get("optimize_next") or 0),
            "low_value": int(counts.get("low_value") or 0),
        }

    def _recommended_control_action(self, ledger: dict[str, Any]) -> str:
        counts = self._callback_closed_loop_counts(ledger)
        scan_rows = counts["scan_rows"]
        low_value_rate = counts["low_value"] / scan_rows if scan_rows else 0.0
        useful = counts["direct_submit"] + counts["submit_ready"]
        if scan_rows >= 50 and useful == 0 and low_value_rate >= 0.9:
            return "pause_agent_optimization"
        if str(ledger.get("current_stage") or "").endswith("_partial"):
            return "watch_execution_health"
        return "continue_mining"

    def run_llm_plan(
        self,
        ledger: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> Path | None:
        now = now or datetime.now()
        if self.dry_run:
            return self._run_llm_plan_uncheckpointed(ledger, now=now)
        prompt = self._build_llm_prompt(ledger) if self.llm_adapter.is_configured() else "planner-disabled"
        planning_input = json.dumps(
            {
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "provider_config_digest": self.llm_adapter.metadata().get("config_digest", ""),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        input_digest = hashlib.sha256(planning_input.encode("utf-8")).hexdigest()
        artifact_path: Path | None = None

        def execute() -> StageOutcome:
            nonlocal artifact_path
            artifact_path = self._run_llm_plan_uncheckpointed(ledger, now=now)
            if artifact_path is None:
                return StageOutcome.create(
                    status="skipped",
                    output={"reason": "planner_not_configured"},
                )
            artifacts = tuple(
                relative_path(path, self.root)
                for path in (
                    self.llm_adapter.prompt_path(self.root, self.run_dir, self.run_tag),
                    artifact_path,
                )
                if path.is_file()
            )
            payload = read_json(artifact_path, {})
            plan = payload.get("llm_plan") if isinstance(payload, dict) else None
            plan = plan if isinstance(plan, dict) else {}
            planner_status = str(plan.get("status") or "unknown")
            retryable = bool(plan.get("retryable"))
            status = "deferred" if planner_status == "error" and retryable else "completed"
            return StageOutcome.create(
                status=status,
                artifacts=artifacts,
                output={
                    "provider_stage": self.llm_adapter.stage,
                    "planner_status": planner_status,
                    "retryable": retryable,
                    "artifact": relative_path(artifact_path, self.root),
                },
                extensions={"research_payload_preserved_in_artifact": True},
            )

        StageRunner(self.stage_checkpoint_store).run(
            run_id=self.run_tag,
            stage_id="llm_planning",
            input_digest=input_digest,
            execute=execute,
            replay_policy="safe",
            started_at=now,
        )
        return artifact_path

    def _run_llm_plan_uncheckpointed(
        self,
        ledger: dict[str, Any],
        *,
        now: datetime,
    ) -> Path | None:
        if not self.llm_adapter.is_configured():
            return None
        prompt_path = self.llm_adapter.prompt_path(self.root, self.run_dir, self.run_tag)
        output_path = self.llm_adapter.output_path(self.root, self.run_dir, self.run_tag)
        credential_changed = self.llm_adapter.prepare_for_attempt(self.root)
        provider_metadata = self.llm_adapter.metadata()
        ledger["llm_provider"] = provider_metadata
        existing: dict[str, Any] = {}
        existing_plan: dict[str, Any] = {}
        if output_path.exists():
            loaded = read_json(output_path, {})
            existing = loaded if isinstance(loaded, dict) else {}
            plan_value = existing.get("llm_plan")
            existing_plan = plan_value if isinstance(plan_value, dict) else {}
            if self._reuse_llm_plan_artifact(
                existing_plan,
                config_digest=provider_metadata["config_digest"],
                now=now,
                credential_changed=credential_changed,
            ):
                if not self.dry_run:
                    write_json(self.ledger_path, ledger)
                return output_path
        prompt = self._build_llm_prompt(ledger)
        if self.dry_run:
            return prompt_path
        write_text(prompt_path, prompt)
        payload = self.llm_adapter.call_configured(self.root, prompt)
        provider_metadata = self.llm_adapter.metadata()
        ledger["llm_provider"] = provider_metadata
        status = "error" if payload.get("error") or payload.get("disabled") else "success"
        same_digest = (
            existing_plan.get("config_digest")
            == provider_metadata["config_digest"]
        )
        previous_attempt_value = existing_plan.get("attempt_count")
        previous_attempts = (
            previous_attempt_value
            if same_digest
            and isinstance(previous_attempt_value, int)
            and not isinstance(previous_attempt_value, bool)
            and previous_attempt_value >= 0
            else 0
        )
        attempt_count = max(0, previous_attempts) + 1
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = error.get("code") if status == "error" else None
        if status == "error" and not code:
            code = "unsupported_capability"
        retryable = (
            bool(error.get("retryable"))
            if status == "error" and code != "authentication_error"
            else False
        )
        next_retry_at: str | None = None
        pause_reason: str | None = None
        if status == "error" and retryable:
            exponent = min(max(0, attempt_count - 1), 30)
            delay_seconds = min(
                LLM_RETRY_BASE_SECONDS * (2**exponent),
                LLM_RETRY_CAP_SECONDS,
            )
            next_retry_at = (now + timedelta(seconds=delay_seconds)).isoformat(
                timespec="seconds"
            )
            pause_reason = "retry_backoff"
        elif status == "error":
            pause_reason = "terminal_error"
        payload["llm_plan"] = {
            "status": status,
            "pause_reason": pause_reason,
            "code": code,
            "retryable": retryable,
            "attempt_count": attempt_count,
            "last_attempt_at": now.isoformat(timespec="seconds"),
            "next_retry_at": next_retry_at,
            "config_digest": provider_metadata["config_digest"],
            "process_instance_id": self.process_instance_id,
        }
        write_json(self.ledger_path, ledger)
        write_json(output_path, payload)
        return output_path

    def _reuse_llm_plan_artifact(
        self,
        plan: dict[str, Any],
        *,
        config_digest: str,
        now: datetime,
        credential_changed: bool,
    ) -> bool:
        if not LLM_PLAN_POLICY_FIELDS.issubset(plan):
            return False
        if plan.get("config_digest") != config_digest:
            return False
        if credential_changed:
            return False
        if plan.get("status") == "success":
            return True
        if plan.get("status") != "error":
            return False
        if bool(plan.get("retryable")):
            next_retry_at = plan.get("next_retry_at")
            if not isinstance(next_retry_at, str) or not next_retry_at:
                return False
            try:
                return now < datetime.fromisoformat(next_retry_at)
            except (TypeError, ValueError):
                return False
        return plan.get("process_instance_id") == self.process_instance_id

    def run_kimi_plan(self, ledger: dict[str, Any]) -> Path | None:
        return self.run_llm_plan(ledger)

    def _build_llm_prompt(self, ledger: dict[str, Any]) -> str:
        priors = self.config.get("current_direction_priors") or {}
        objective = self.config.get("objective") or {}
        proxy_context = self._behavioral_proxy_prompt_section()
        return (
            f"你是 WorldQuant BRAIN 行为经济学 alpha 挖掘方向规划器，当前规划器是 {self.llm_adapter.display_name}。"
            "目标是最大化今天最终可提交且低相关的 alpha 数量，不是 raw PASS 数。\n\n"
            f"今日预算: {ledger.get('daily_budget')} simulations; "
            f"已花: {ledger.get('spent_simulations')}; "
            f"剩余: {ledger.get('remaining_simulations_after_commitments')}.\n"
            f"Primary objective: {objective.get('primary', 'maximize_final_submit_ready_count')}\n"
            f"Promote directions: {priors.get('promote', [])}\n"
            f"Controlled directions: {priors.get('controlled', [])}\n"
            f"Downweight directions: {priors.get('downweight', [])}\n\n"
            f"{proxy_context}"
            "请只输出 JSON，字段包括 families, candidate_rules, avoid, budget_suggestion, submission_order_rules, rationale。"
        )

    def _behavioral_proxy_prompt_section(self) -> str:
        config = self.config.get("behavioral_proxy_map") or {}
        path_value = config.get("path") if isinstance(config, dict) else None
        if not path_value:
            return ""
        payload = read_json(self.root / str(path_value), {})
        mechanisms = payload.get("mechanisms") if isinstance(payload, dict) else None
        if not isinstance(mechanisms, list) or not mechanisms:
            return ""
        max_mechanisms = int(config.get("max_mechanisms") or 8)
        ordered = sorted(
            (row for row in mechanisms if isinstance(row, dict)),
            key=lambda row: (
                {"promote": 0, "controlled": 1, "downweight": 2, "block": 3}.get(str(row.get("budget_policy")), 9),
                str(row.get("mechanism") or ""),
            ),
        )[:max_mechanisms]
        lines = [
            "Behavioral proxy map (field-first WQB代理约束):",
            "Use promote mechanisms first; keep downweight/block mechanisms small unless fresh results contradict the map.",
        ]
        for row in ordered:
            field_evidence = row.get("field_evidence") or {}
            feedback = row.get("result_feedback") or {}
            lines.append(
                "- "
                f"{row.get('mechanism')}: "
                f"label_zh={row.get('label_zh')}; "
                f"proxy_strength={row.get('proxy_strength')}; "
                f"result_strength={row.get('result_strength')}; "
                f"budget_policy={row.get('budget_policy')}; "
                f"fields={field_evidence.get('matched_field_count', 0)}; "
                f"tested={feedback.get('tested_count', 0)}; "
                f"pass={feedback.get('all_pass_count', 0)}; "
                f"near={feedback.get('near_pass_count', 0)}; "
                f"rationale_zh={row.get('rationale_zh', '')}"
            )
        return "\n".join(lines) + "\n\n"

    def _build_kimi_prompt(self, ledger: dict[str, Any]) -> str:
        return self._build_llm_prompt(ledger)

    def _scan_preflight_input_digest(self, ledger: dict[str, Any], *, planned_stage: str) -> str:
        paths: set[Path] = {
            self.workflow_config_path,
            self.root / ".local" / "data" / "all_wqb_fields.json",
            self.run_dir / "family_efficiency.json",
        }
        for value in ledger.get("queued_scan_configs") or []:
            path = Path(str(value))
            paths.add(path if path.is_absolute() else self.root / path)
        stage_order = [str(stage) for stage in ledger.get("stage_order") or []]
        try:
            planned_index = stage_order.index(planned_stage)
        except ValueError:
            planned_index = 0
        earlier_stages = set(stage_order[:planned_index])
        if self.config_dir.exists():
            for config_path in self.config_dir.glob("*.json"):
                payload = read_json(config_path, {})
                context = payload.get("daily_budget_context") if isinstance(payload, dict) else None
                config_stage = str(context.get("stage") or "") if isinstance(context, dict) else ""
                output_value = payload.get("output") if isinstance(payload, dict) else None
                output_exists = False
                if output_value:
                    output_path = Path(str(output_value))
                    output_path = output_path if output_path.is_absolute() else self.root / output_path
                    output_exists = output_path.is_file()
                    if output_exists:
                        paths.add(output_path)
                if config_stage in earlier_stages or output_exists:
                    paths.add(config_path)
        proxy_config = self.config.get("behavioral_proxy_map") or {}
        if isinstance(proxy_config, dict) and proxy_config.get("path"):
            paths.add(self.root / str(proxy_config["path"]))
        fingerprints = [
            {
                "path": relative_path(path, self.root),
                "sha256": _file_sha256(path),
            }
            for path in sorted(paths, key=lambda item: item.as_posix())
        ]
        material = json.dumps(
            {
                "ledger": {
                    key: ledger.get(key)
                    for key in (
                        "stage_order",
                        "stage_budgets",
                        "stage_spend",
                        "remaining_simulations_after_commitments",
                        "queued_scan_configs",
                    )
                },
                "run_tag": self.run_tag,
                "workflow_config_sha256": _file_sha256(self.workflow_config_path),
                "files": fingerprints,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def run_scan_preflight(
        self,
        ledger: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> tuple[StagePlan, str]:
        now = now or datetime.now()
        preview = self.plan_next_scan(ledger)
        plan: StagePlan | None = None
        initial_action = ""

        def execute() -> StageOutcome:
            nonlocal plan, initial_action
            plan = self.plan_next_scan(ledger)
            initial_action = plan.action
            if initial_action == "slice_scan_config":
                plan = self.prepare_budgeted_scan(plan)
            status = (
                "deferred"
                if initial_action == "waiting_for_scan_config"
                else "skipped"
                if initial_action == "no_budgeted_stage_ready"
                else "completed"
            )
            artifacts = tuple(
                relative_path(path, self.root)
                for path in (
                    plan.source_config,
                    plan.sliced_config,
                    self.run_dir / "preflight_evaluation_report.json",
                    self.run_dir / "research_policy_evaluation.json",
                    self.run_dir / "decision_attribution.json",
                )
                if path is not None and path.is_file()
            )
            return StageOutcome.create(
                status=status,
                artifacts=artifacts,
                output={
                    "stage": plan.stage,
                    "initial_action": initial_action,
                    "final_action": plan.action,
                    "budget": plan.budget,
                    "remaining_stage_budget": plan.remaining_stage_budget,
                    "remaining_daily_budget": plan.remaining_daily_budget,
                    "source_config": (
                        relative_path(plan.source_config, self.root) if plan.source_config is not None else ""
                    ),
                    "sliced_config": (
                        relative_path(plan.sliced_config, self.root) if plan.sliced_config is not None else ""
                    ),
                    "output_path": (
                        relative_path(plan.output_path, self.root) if plan.output_path is not None else ""
                    ),
                    "candidate_count": plan.candidate_count,
                },
                extensions={"remote_side_effects": False},
            )

        if self.dry_run:
            execute()
        else:
            StageRunner(self.stage_checkpoint_store).run(
                run_id=self.run_tag,
                stage_id="scan_preflight",
                input_digest=self._scan_preflight_input_digest(ledger, planned_stage=preview.stage),
                execute=execute,
                replay_policy="safe",
                started_at=now,
            )
        if plan is None:
            raise RuntimeError("scan preflight stage completed without a plan")
        return plan, initial_action

    def plan_next_scan(self, ledger: dict[str, Any]) -> StagePlan:
        stage_order = ledger.get("stage_order") or []
        stage_budgets = ledger.get("stage_budgets") or {}
        stage_spend = ledger.get("stage_spend") or {}
        remaining_daily = int(ledger.get("remaining_simulations_after_commitments") or 0)
        queued = [self.root / path for path in ledger.get("queued_scan_configs") or []]

        for stage in stage_order:
            budget = int(stage_budgets.get(stage) or 0)
            if budget <= 0:
                continue
            spent = int(stage_spend.get(stage) or 0)
            remaining_stage = max(0, budget - spent)
            if remaining_stage <= 0 or remaining_daily <= 0:
                continue
            if self.research_policy is not None or stage in {"direction_probe", "scale_winners", "pass_corr_repair_optimization", "late_rescue_or_exploration", "end_of_day_holdout"}:
                source = next((path for path in queued if path.exists()), None)
                if source is None:
                    return StagePlan(stage, budget, remaining_stage, remaining_daily, action="waiting_for_scan_config")
                return StagePlan(stage, budget, remaining_stage, remaining_daily, source_config=source, action="slice_scan_config")
        return StagePlan("none", 0, 0, remaining_daily, action="no_budgeted_stage_ready")

    def _used_candidate_identities_before_stage(self, stage: str) -> set[tuple[str, str]]:
        stage_order = (
            list(self.research_policy.budget.stage_allocations)
            if self.research_policy is not None
            else self.config.get("stage_order") or []
        )
        try:
            stage_index = list(stage_order).index(stage)
        except ValueError:
            return set()
        earlier_stages = set(stage_order[:stage_index])
        used: set[tuple[str, str]] = set()
        for path in sorted(self.config_dir.glob("*.json")):
            payload = read_json(path, {})
            if not isinstance(payload, dict):
                continue
            context = payload.get("daily_budget_context") or {}
            if context.get("stage") not in earlier_stages:
                continue
            for candidate in payload.get("candidates") or []:
                if isinstance(candidate, dict) and normalize_expression(str(candidate.get("expression") or "")):
                    used.add(candidate_identity(candidate))
        return used

    def _completed_candidate_identities_for_stage(self, stage: str) -> set[tuple[str, str]]:
        completed: set[tuple[str, str]] = set()
        if not self.config_dir.exists():
            return completed
        for path in sorted(self.config_dir.glob("*.json")):
            payload = read_json(path, {})
            if not isinstance(payload, dict):
                continue
            context = payload.get("daily_budget_context") or {}
            if context.get("stage") != stage:
                continue
            output_value = payload.get("output")
            if not output_value:
                continue
            output_path = Path(str(output_value))
            if not output_path.is_absolute():
                output_path = self.root / output_path
            rows = read_json(output_path, [])
            if not isinstance(rows, list):
                continue
            target_keys = {
                candidate_identity(candidate)
                for candidate in payload.get("candidates") or []
                if isinstance(candidate, dict) and normalize_expression(str(candidate.get("expression") or ""))
            }
            if not target_keys:
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                key = candidate_identity(row)
                if key in target_keys:
                    completed.add(key)
        return completed

    def _result_paths_for_stage(self, stage: str) -> list[Path]:
        paths: list[Path] = []
        if not self.config_dir.exists():
            return paths
        for path in sorted(self.config_dir.glob("*.json")):
            payload = read_json(path, {})
            if not isinstance(payload, dict):
                continue
            context = payload.get("daily_budget_context") or {}
            if context.get("stage") != stage:
                continue
            output_value = payload.get("output")
            if not output_value:
                continue
            output_path = Path(str(output_value))
            if not output_path.is_absolute():
                output_path = self.root / output_path
            if output_path.exists():
                paths.append(output_path)
        return paths

    def reconcile_existing_stage_progress(self, ledger: dict[str, Any]) -> bool:
        stage_order = ledger.get("stage_order") or []
        stage_budgets = ledger.get("stage_budgets") or {}
        stage_spend = ledger.setdefault("stage_spend", {})
        changed = False
        latest_result_path: Path | None = None

        for stage in stage_order:
            budget = int(stage_budgets.get(stage) or 0)
            if budget <= 0:
                continue
            completed_count = len(self._completed_candidate_identities_for_stage(str(stage)))
            if completed_count <= 0:
                continue
            stage_paths = self._result_paths_for_stage(str(stage))
            if stage_paths:
                latest_result_path = max(stage_paths, key=lambda path: path.stat().st_mtime)
            credited_before = int(stage_spend.get(stage) or 0)
            if completed_count > credited_before:
                stage_spend[stage] = completed_count
                changed = True

        credited_total = sum(int(value or 0) for value in stage_spend.values())
        daily_budget = int(ledger.get("daily_budget") or credited_total)
        reconciled_spent = min(daily_budget, max(int(ledger.get("spent_simulations") or 0), credited_total))
        if reconciled_spent != int(ledger.get("spent_simulations") or 0):
            ledger["spent_simulations"] = reconciled_spent
            changed = True

        reconciled_stage = ""
        for stage in stage_order:
            budget = int(stage_budgets.get(stage) or 0)
            if budget <= 0:
                continue
            spent = int(stage_spend.get(stage) or 0)
            if spent >= budget:
                reconciled_stage = f"{stage}_complete"
                continue
            if spent > 0:
                reconciled_stage = f"{stage}_partial"
            break
        if reconciled_stage and ledger.get("current_stage") != reconciled_stage:
            ledger["current_stage"] = reconciled_stage
            changed = True
        if latest_result_path is not None:
            latest_result_value = relative_path(latest_result_path, self.root)
            if ledger.get("last_completed_scan") != latest_result_value:
                ledger["last_completed_scan"] = latest_result_value
                changed = True

        self._refresh_remaining(ledger)
        if changed and not self.dry_run:
            ledger["last_stage_progress_reconciled_at"] = datetime.now().isoformat(timespec="seconds")
            write_json(self.ledger_path, ledger)
        return changed

    def prepare_budgeted_scan(self, plan: StagePlan) -> StagePlan:
        if plan.source_config is None:
            return plan
        config = read_json(plan.source_config, {})
        candidates = config.get("candidates") or []
        used_identities = self._used_candidate_identities_before_stage(plan.stage)
        used_identities.update(self._completed_candidate_identities_for_stage(plan.stage))
        available_rows = [
            (row_index, candidate)
            for row_index, candidate in enumerate(candidates)
            if isinstance(candidate, dict) and candidate_identity(candidate) not in used_identities
        ]
        available_candidates, research_policy_context = self._apply_research_policy(
            plan.source_config,
            available_rows,
        )
        max_count = min(plan.remaining_stage_budget, plan.remaining_daily_budget, len(available_candidates))
        caps = self.config.get("diversity_caps") or {}
        downweighted_families = self._downweighted_behavior_families()
        selected = choose_budgeted_candidates(
            available_candidates,
            max_count,
            single_base_share=float(caps.get("single_base_alpha_daily_budget_max_share") or 0.12),
            single_field_share=float(caps.get("single_field_daily_budget_max_share") or 0.12),
            single_family_share=(
                float(caps["single_family_daily_budget_max_share"])
                if "single_family_daily_budget_max_share" in caps
                else None
            ),
            single_skeleton_share=(
                float(caps["single_skeleton_daily_budget_max_share"])
                if "single_skeleton_daily_budget_max_share" in caps
                else None
            ),
            pure_price_volume_share=(
                float(caps["pure_price_volume_standalone_daily_budget_max_share"])
                if "pure_price_volume_standalone_daily_budget_max_share" in caps
                else None
            ),
            downweighted_families=downweighted_families,
            downweighted_family_share=(
                float(caps["downweighted_family_daily_budget_max_share"])
                if "downweighted_family_daily_budget_max_share" in caps
                else None
            ),
        )
        selected, preflight_record = self._preflight_selected_candidates(plan.source_config, selected, config)
        selected, policy_context = self._apply_policy_feedback_controls(selected, max_count)
        source_stem = plan.source_config.parent.name
        sliced_config = self.config_dir / f"{plan.stage}_{source_stem}_{len(selected)}.json"
        output_path = self.run_dir / f"{plan.stage}_{source_stem}_results.json"
        sliced = dict(config)
        sliced["output"] = relative_path(output_path, self.root)
        sliced["continue_on_pass"] = True
        sliced["max_concurrency"] = min(int(sliced.get("max_concurrency") or 3), 3)
        sliced["candidates"] = selected
        sliced["daily_budget_context"] = {
            "daily_run_tag": self.run_tag,
            "stage": plan.stage,
            "source_config": relative_path(plan.source_config, self.root),
            "stage_budget": plan.budget,
            "remaining_stage_budget": plan.remaining_stage_budget,
            "remaining_daily_budget": plan.remaining_daily_budget,
            "selected_candidates": len(selected),
            "previous_stage_candidates_skipped": len(used_identities),
            "preflight_blocked_candidates": int(preflight_record.metrics.get("invalid_count") or 0),
            "required_policy_experiments": policy_context["required_policy_experiments"],
            "policy_action_lanes": policy_context["policy_action_lanes"],
            "policy_budget_caps_applied": policy_context["policy_budget_caps_applied"],
            "candidate_diversity_gate": {
                "enabled": bool(caps),
                "available_candidates": len(available_candidates),
                "selected_after_diversity": len(selected),
                "downweighted_families": sorted(downweighted_families),
                "single_family_max_share": caps.get("single_family_daily_budget_max_share"),
                "single_skeleton_max_share": caps.get("single_skeleton_daily_budget_max_share"),
                "downweighted_family_max_share": caps.get("downweighted_family_daily_budget_max_share"),
            },
        }
        if research_policy_context is not None:
            sliced["daily_budget_context"]["research_policy"] = research_policy_context
        if not self.dry_run:
            write_json(sliced_config, sliced)
            self._write_preflight_evaluation_report(plan.source_config, preflight_record)
        plan.sliced_config = sliced_config
        plan.output_path = output_path
        plan.candidate_count = len(selected)
        plan.action = "prepared_scan_config"
        if not self.dry_run:
            self._record_decision_attribution(plan, selected)
        return plan

    def _apply_research_policy(
        self,
        source_config: Path,
        candidate_rows: list[tuple[int, dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if self.research_policy is None:
            return [candidate for _, candidate in candidate_rows], None

        allowed: list[dict[str, Any]] = []
        evaluations: list[dict[str, Any]] = []
        current_digest = policy_digest(self.research_policy)
        for row_index, candidate in candidate_rows:
            identity_payload = {
                "expression": normalize_expression(str(candidate.get("expression") or "")),
                "settings": candidate.get("settings") or {},
            }
            identity = hashlib.sha256(
                json.dumps(identity_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            candidate_id = str(candidate.get("candidate_id") or f"row-{row_index:06d}-{identity[:12]}")
            evaluation = evaluate_candidate_boundaries(
                {**candidate, "candidate_id": candidate_id},
                self.research_policy,
            )
            record = evaluation.to_dict()
            record["row_index"] = row_index
            record["identity"] = identity
            record["source_config"] = relative_path(source_config, self.root)
            record["policy_digest"] = current_digest
            record["evaluation_key"] = hashlib.sha256(
                f"{current_digest}:{record['source_config']}:{row_index}:{identity}".encode("utf-8")
            ).hexdigest()
            evaluations.append(record)
            if evaluation.allowed:
                allowed.append(candidate)

        report_path = self.run_dir / "research_policy_evaluation.json"
        previous_report = read_json(report_path, {}) if report_path.exists() else {}
        previous_evaluations = (
            previous_report.get("evaluations")
            if isinstance(previous_report, dict) and previous_report.get("digest") == current_digest
            else []
        )
        merged_by_key = {
            str(item.get("evaluation_key")): item
            for item in previous_evaluations or []
            if isinstance(item, dict) and item.get("evaluation_key")
        }
        merged_by_key.update({str(item["evaluation_key"]): item for item in evaluations})
        cumulative_evaluations = list(merged_by_key.values())
        cumulative_block_counts: dict[str, int] = {}
        cumulative_allowed = 0
        for item in cumulative_evaluations:
            if item.get("allowed"):
                cumulative_allowed += 1
            for error in item.get("errors") or []:
                if isinstance(error, dict) and error.get("code"):
                    code = str(error["code"])
                    cumulative_block_counts[code] = cumulative_block_counts.get(code, 0) + 1

        summary = self._research_policy_metadata()
        summary.update(
            {
                "evaluated_candidates": len(cumulative_evaluations),
                "allowed_candidates": cumulative_allowed,
                "blocked_candidates": len(cumulative_evaluations) - cumulative_allowed,
                "block_counts": dict(sorted(cumulative_block_counts.items())),
            }
        )
        report = {
            **summary,
            "source_config": relative_path(source_config, self.root),
            "evaluations": cumulative_evaluations,
        }
        if self._active_ledger is not None:
            self._active_ledger["research_policy"] = dict(summary)
            if not self.dry_run:
                write_json(self.ledger_path, self._active_ledger)
        if not self.dry_run:
            write_json(report_path, report)
        return allowed, summary

    def _downweighted_behavior_families(self) -> set[str]:
        families: set[str] = set()
        explicit = self.config.get("downweighted_behavior_families") or []
        if isinstance(explicit, list):
            families.update(str(item) for item in explicit if str(item))
        proxy_config = self.config.get("behavioral_proxy_map") or {}
        path_value = proxy_config.get("path") if isinstance(proxy_config, dict) else None
        if path_value:
            payload = read_json(self.root / str(path_value), {})
            mechanisms = payload.get("mechanisms") if isinstance(payload, dict) else []
            for row in mechanisms if isinstance(mechanisms, list) else []:
                if not isinstance(row, dict):
                    continue
                policy = str(row.get("budget_policy") or "").lower()
                strength = str(row.get("result_strength") or "").lower()
                if policy in {"downweight", "block"} or strength == "weak":
                    mechanism = str(row.get("mechanism") or "").strip()
                    if mechanism:
                        families.add(mechanism)
        family_efficiency_path = self.run_dir / "family_efficiency.json"
        family_efficiency = read_json(family_efficiency_path, {})
        for row in family_efficiency.get("families", []) if isinstance(family_efficiency, dict) else []:
            if not isinstance(row, dict):
                continue
            tested = int(row.get("tested_count") or 0)
            low_value = int(row.get("low_value_count") or 0)
            productive = int(row.get("direct_submit_count") or 0) + int(row.get("optimize_next_count") or 0)
            if tested >= 8 and low_value / max(tested, 1) >= 0.9 and productive == 0:
                family = str(row.get("family") or "").strip()
                if family:
                    families.add(family)
        return families

    def _apply_policy_feedback_controls(
        self,
        candidates: list[dict[str, Any]],
        budget: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        capped_counts: dict[str, int] = {}
        caps_applied: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            feedback = candidate.get("policy_feedback") if isinstance(candidate.get("policy_feedback"), dict) else {}
            max_share = feedback.get("max_budget_share") if isinstance(feedback, dict) else None
            cap_key = self._policy_cap_key(feedback)
            if max_share is not None and cap_key:
                cap_count = max(0, int(float(max_share) * max(budget, 1)))
                caps_applied[cap_key] = {"max_budget_share": float(max_share), "max_candidate_count": cap_count}
                if capped_counts.get(cap_key, 0) >= cap_count:
                    continue
                capped_counts[cap_key] = capped_counts.get(cap_key, 0) + 1
            selected.append(candidate)
        return selected, self._policy_feedback_context(selected, caps_applied)

    def _policy_cap_key(self, feedback: Any) -> str:
        if not isinstance(feedback, dict):
            return ""
        actions = feedback.get("budget_actions") if isinstance(feedback.get("budget_actions"), dict) else {}
        diagnosis_types = sorted(str(action.get("diagnosis_type") or key) for key, action in actions.items() if isinstance(action, dict))
        return "+".join(diagnosis_types)

    def _policy_feedback_context(
        self,
        candidates: list[dict[str, Any]],
        caps_applied: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        experiments: set[str] = set()
        lanes: set[str] = set()
        for candidate in candidates:
            lane = candidate.get("wqb_action_lane")
            if lane:
                lanes.add(str(lane))
            feedback = candidate.get("policy_feedback") if isinstance(candidate.get("policy_feedback"), dict) else {}
            for experiment in feedback.get("required_experiments") or [] if isinstance(feedback, dict) else []:
                experiments.add(str(experiment))
        return {
            "required_policy_experiments": sorted(experiments),
            "policy_action_lanes": sorted(lanes),
            "policy_budget_caps_applied": caps_applied,
        }

    def _preflight_selected_candidates(
        self,
        source_config: Path,
        selected: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], OutputEvaluationRecord]:
        field_types = self._preflight_field_types(config)
        if not field_types:
            return selected, OutputEvaluationRecord(
                artifact=relative_path(source_config, self.root),
                stage="scan_config_expression",
                validation_status="pass",
                diagnoses=tuple(),
                metrics={"row_count": len(selected), "invalid_count": 0, "budget_saved_estimate": 0},
            )

        record = validate_expression_candidates(relative_path(source_config, self.root), selected, field_types=field_types)
        blocked_indices = {
            int(diagnosis.evidence["row_index"])
            for diagnosis in record.diagnoses
            if isinstance(diagnosis.evidence.get("row_index"), int)
        }
        if not blocked_indices:
            return selected, record
        return [candidate for index, candidate in enumerate(selected) if index not in blocked_indices], record

    def _preflight_field_types(self, config: dict[str, Any]) -> dict[str, str]:
        field_types: dict[str, str] = {}
        has_strict_inventory = False
        explicit = config.get("field_types") or {}
        if isinstance(explicit, dict):
            field_types.update({str(field): str(field_type).lower() for field, field_type in explicit.items()})
            has_strict_inventory = bool(field_types)
        metadata = config.get("field_metadata") or []
        if isinstance(metadata, list):
            for item in metadata:
                if isinstance(item, dict):
                    field_id = item.get("id") or item.get("field_id")
                    field_type = item.get("type")
                    if field_id and field_type:
                        field_types[str(field_id)] = str(field_type).lower()
                        has_strict_inventory = True

        fields_path = self.root / ".local" / "data" / "all_wqb_fields.json"
        fields_payload = read_json(fields_path, {}) if fields_path.exists() else {}
        for field in fields_payload.get("fields") or [] if isinstance(fields_payload, dict) else []:
            if isinstance(field, dict) and field.get("id") and field.get("type"):
                field_types.setdefault(str(field["id"]), str(field["type"]).lower())
                has_strict_inventory = True
        if not has_strict_inventory:
            return {}
        for price_field in ("open", "close", "high", "low", "returns", "volume", "vwap", "adv20", "cap"):
            field_types.setdefault(price_field, "matrix")
        return field_types

    def _write_preflight_evaluation_report(self, source_config: Path, record: OutputEvaluationRecord) -> None:
        report_path = self.run_dir / "preflight_evaluation_report.json"
        payload = asdict(record)
        payload["source_config"] = relative_path(source_config, self.root)
        write_json(report_path, payload)

    def _decision_attribution_enabled(self) -> bool:
        config = self.config.get("decision_attribution") or {}
        return isinstance(config, dict) and bool(config.get("enabled"))

    def _record_decision_attribution(self, plan: StagePlan, candidates: list[dict[str, Any]]) -> None:
        if not self._decision_attribution_enabled() or plan.source_config is None or plan.sliced_config is None or plan.output_path is None:
            return
        from src.decision_attribution import record_scan_decision

        proxy_config = self.config.get("behavioral_proxy_map") or {}
        proxy_path = (
            self.root / str(proxy_config.get("path"))
            if isinstance(proxy_config, dict) and proxy_config.get("path")
            else self.root / ".local" / "data" / "behavioral_proxy" / "behavioral_proxy_map.json"
        )
        record_scan_decision(
            self.root,
            self.run_dir,
            stage=plan.stage,
            stage_budget=plan.budget,
            remaining_stage_budget=plan.remaining_stage_budget,
            remaining_daily_budget=plan.remaining_daily_budget,
            source_config=plan.source_config,
            sliced_config=plan.sliced_config,
            output_path=plan.output_path,
            candidates=candidates,
            proxy_map_path=proxy_path,
        )

    def _score_decision_attribution(self) -> None:
        if not self._decision_attribution_enabled():
            return
        from src.decision_attribution import score_decision_outcomes

        score_decision_outcomes(self.run_dir)

    def _simulation_reconciliation_configs(self, fingerprints: set[str]) -> list[Path]:
        matches: list[Path] = []
        if not fingerprints or not self.config_dir.is_dir():
            return matches
        for config_path in sorted(self.config_dir.glob("*.json")):
            payload = read_json(config_path, {})
            if not isinstance(payload, dict):
                continue
            base_settings = payload.get("settings") or {}
            if not isinstance(base_settings, dict):
                base_settings = {}
            config_fingerprints: set[str] = set()
            for candidate in payload.get("candidates") or []:
                if not isinstance(candidate, dict) or not candidate.get("expression"):
                    continue
                settings = dict(base_settings)
                candidate_settings = candidate.get("settings") or {}
                if isinstance(candidate_settings, dict):
                    settings.update(candidate_settings)
                config_fingerprints.add(
                    payload_fingerprint(
                        {
                            "type": "REGULAR",
                            "settings": settings,
                            "regular": str(candidate["expression"]),
                        }
                    )
                )
            if config_fingerprints.intersection(fingerprints):
                matches.append(config_path)
        return matches

    def reconcile_simulation_side_effects(self) -> dict[str, Any]:
        records = self.operation_journal.records(
            "simulation.create",
            run_id=self.run_tag,
            outcomes=(
                "started",
                "unknown_commit",
                "accepted",
                "reconciliation_pending",
                "manual_review",
            ),
        )
        checkpoint = self.stage_checkpoint_store.load("simulation")
        needs_accepted_recovery = checkpoint is not None and checkpoint.status == "running"
        candidates = [
            record
            for record in records
            if record.outcome in {
                "started",
                "unknown_commit",
                "reconciliation_pending",
                "manual_review",
            }
            or needs_accepted_recovery
        ]
        configs = self._simulation_reconciliation_configs(
            {record.fingerprint for record in candidates}
        )
        return_codes: list[int] = []
        for config_path in configs:
            command = [
                sys.executable,
                "-m",
                "scripts.run.scan",
                "--config",
                relative_path(config_path, self.root),
                "--reconcile-only",
            ]
            scan_env = os.environ.copy()
            scan_env["WQB_RUN_ID"] = self.run_tag
            scan_env["WQB_OPERATION_JOURNAL"] = str(self.run_dir / "operations.db")
            result = subprocess.run(command, cwd=self.root, check=False, env=scan_env)
            return_codes.append(int(result.returncode))

        remaining = self.operation_journal.records(
            "simulation.create",
            run_id=self.run_tag,
            outcomes=("started", "unknown_commit", "reconciliation_pending", "manual_review"),
        )
        report = {
            "status": "blocked" if remaining else "clear",
            "inspected_operation_ids": [record.operation_id for record in candidates],
            "matched_configs": [relative_path(path, self.root) for path in configs],
            "return_codes": return_codes,
            "remaining": [
                {
                    "operation_id": record.operation_id,
                    "fingerprint": record.fingerprint,
                    "outcome": record.outcome,
                    "reason": record.reason,
                    "reconciliation_reason": record.reconciliation_reason,
                    "reconcile_attempts": record.reconcile_attempts,
                    "next_reconcile_at": record.next_reconcile_at,
                }
                for record in remaining
            ],
        }
        if candidates and not self.dry_run:
            write_json(self.run_dir / "simulation_reconciliation.json", report)
        return report

    def execute_scan(self, plan: StagePlan, ledger: dict[str, Any]) -> int:
        if not self.execute_scans or plan.sliced_config is None or plan.candidate_count <= 0:
            return self._execute_scan_uncheckpointed(plan, ledger)

        reconciliation = self.reconcile_simulation_side_effects()
        if reconciliation["status"] == "blocked":
            print(
                "WARNING: unresolved simulation outcome blocks new simulation POSTs; "
                "see simulation_reconciliation.json",
                flush=True,
            )
            return 0

        input_material = json.dumps(
            {
                "run_tag": self.run_tag,
                "stage": plan.stage,
                "budget": plan.budget,
                "remaining_stage_budget": plan.remaining_stage_budget,
                "remaining_daily_budget": plan.remaining_daily_budget,
                "candidate_count": plan.candidate_count,
                "sliced_config_sha256": _file_sha256(plan.sliced_config),
                "output_path": relative_path(plan.output_path or Path(), self.root),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        input_digest = hashlib.sha256(input_material.encode("utf-8")).hexdigest()
        newly_credited = 0

        def execute() -> StageOutcome:
            nonlocal newly_credited
            newly_credited = self._execute_scan_uncheckpointed(plan, ledger)
            unresolved = self.operation_journal.unresolved("simulation.create")
            artifacts = tuple(
                relative_path(path, self.root)
                for path in (
                    plan.sliced_config,
                    plan.output_path,
                    self.run_dir / "operations.db",
                    self.run_dir / "simulation_reconciliation.json",
                )
                if path is not None and path.is_file()
            )
            return StageOutcome.create(
                artifacts=artifacts,
                output={
                    "stage": plan.stage,
                    "candidate_count": plan.candidate_count,
                    "newly_credited": newly_credited,
                    "unresolved_operation_ids": [record.operation_id for record in unresolved],
                },
                extensions={
                    "remote_side_effects": True,
                    "reconciliation_required_before_replay": True,
                },
            )

        if self.dry_run:
            execute()
        else:
            StageRunner(self.stage_checkpoint_store).run(
                run_id=self.run_tag,
                stage_id="simulation",
                input_digest=input_digest,
                execute=execute,
                replay_policy="reconcile",
                reconcile=lambda _previous: reconciliation["status"] == "clear",
            )
        return newly_credited

    def _execute_scan_uncheckpointed(self, plan: StagePlan, ledger: dict[str, Any]) -> int:
        stage_spend = ledger.setdefault("stage_spend", {})
        credited_before = int(stage_spend.get(plan.stage) or 0)

        if not self.execute_scans or plan.sliced_config is None or plan.candidate_count <= 0:
            # No candidates to run: exhaust stage budget so plan_next_scan moves on
            if plan.candidate_count <= 0 and plan.budget > 0 and credited_before < plan.budget:
                stage_spend[plan.stage] = plan.budget
                ledger["current_stage"] = f"{plan.stage}_complete"
                self._refresh_remaining(ledger)
                if not self.dry_run:
                    self._enqueue_stage_event(
                        "stage_skipped",
                        ledger,
                        stage=plan.stage,
                        extra={
                            "reason": "no_available_candidates",
                            "candidate_count": int(plan.candidate_count),
                            "stage_budget": int(plan.budget),
                        },
                    )
                    self.drain_workflow_outbox()
                print(f"INFO: stage {plan.stage} has no available candidates; marked complete", flush=True)
            return 0

        sliced_payload = read_json(plan.sliced_config, {})
        candidates = sliced_payload.get("candidates") or []
        command = [
            sys.executable,
            "-m",
            "scripts.run.scan",
            "--config",
            relative_path(plan.sliced_config, self.root),
            "--continue-on-pass",
            "--max-concurrency",
            str(min(int(ledger.get("max_scan_concurrency") or 3), 3)),
        ]
        scan_env = os.environ.copy()
        scan_env["WQB_RUN_ID"] = self.run_tag
        scan_env["WQB_OPERATION_JOURNAL"] = str(self.run_dir / "operations.db")
        result = subprocess.run(command, cwd=self.root, check=False, env=scan_env)
        if result.returncode != 0:
            print(f"WARNING: scan stage {plan.stage} exited with code {result.returncode}; using partial results", flush=True)
        completed_count = completed_candidate_count(plan.output_path or Path(), candidates)
        target_stage_spend = min(int(plan.budget), credited_before + completed_count)
        newly_credited = max(0, target_stage_spend - credited_before)
        if newly_credited:
            ledger["spent_simulations"] = int(ledger.get("spent_simulations") or 0) + newly_credited
        stage_spend[plan.stage] = max(credited_before, target_stage_spend)
        if completed_count >= plan.candidate_count or target_stage_spend >= int(plan.budget):
            ledger["last_completed_scan"] = relative_path(plan.output_path or Path(), self.root)
            ledger["current_stage"] = f"{plan.stage}_complete"
        else:
            ledger["current_stage"] = f"{plan.stage}_partial"

        # Detect empty loop: all selected candidates already tested, nothing new credited
        if newly_credited == 0 and plan.candidate_count > 0 and completed_count >= plan.candidate_count:
            stage_spend[plan.stage] = max(credited_before, plan.budget)
            ledger["current_stage"] = f"{plan.stage}_complete"
            print(f"INFO: stage {plan.stage} exhausted (all {plan.candidate_count} candidates already tested); advancing budget", flush=True)

        self._refresh_remaining(ledger)
        if not self.dry_run:
            self._enqueue_stage_event(
                "stage_scan_complete" if completed_count >= plan.candidate_count else "stage_scan_partial",
                ledger,
                stage=plan.stage,
                extra={
                    "newly_credited": int(newly_credited),
                    "completed_count": int(completed_count),
                    "candidate_count": int(plan.candidate_count),
                    "result_path": relative_path(plan.output_path or Path(), self.root),
                },
            )
            self.drain_workflow_outbox()
        return newly_credited

    def _auto_submit_direct(self) -> str | None:
        """Queue submit-ready candidates and let the independent worker handle WQB state."""
        config = self.config.get("auto_submit_direct") or {}
        if not isinstance(config, dict) or not config.get("enabled"):
            return None
        backlog_path = self.run_dir / "submission_backlog.json"
        if not backlog_path.exists():
            return None
        payload = read_json(backlog_path, [])
        if not payload:
            return None
        from scripts.submit.submission_worker import enqueue_submission_jobs

        state = enqueue_submission_jobs(self.run_dir)
        queued = int((state.get("summary") or {}).get("queued") or 0)
        pending = int((state.get("summary") or {}).get("pending_confirmation_count") or 0)
        throttled = int((state.get("summary") or {}).get("throttled_count") or 0)
        active_count = queued + pending + throttled
        if active_count <= 0:
            return "submission worker queue has no active jobs"
        from src.side_effect_governance import evaluate_side_effect_capability

        capability = evaluate_side_effect_capability("submission")
        if not capability.enabled:
            return (
                "submission worker not launched: capability_disabled "
                f"({capability.environment_variable}=1 required); {active_count} jobs remain queued"
            )
        log_path = self.run_dir / "submission_worker.log"
        command = [
            sys.executable,
            "-m",
            "scripts.submit.submission_worker",
            "--run-dir",
            str(self.run_dir),
            "--daemon",
            "--poll-seconds",
            "300",
        ]
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with open(log_path, "a", encoding="utf-8") as log_fh:
                process = subprocess.Popen(
                    command,
                    cwd=self.root,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            return (
                "submission worker queued "
                f"{active_count} jobs -> {relative_path(self.run_dir / 'submission_state.json', self.root)} "
                f"pid={process.pid}"
            )
        except Exception as exc:
            log_path.write_text(str(exc), encoding="utf-8")
            return f"submission worker launch failed: {exc}"

    def collect_submit_ready(self) -> list[dict[str, Any]]:
        submitted_alpha_ids, submitted_expressions = self._submitted_registry()
        failed_submit_alpha_ids = self._failed_submit_attempt_alpha_ids()
        candidate_rows = self._load_candidate_rows_by_alpha()
        ready: dict[str, dict[str, Any]] = {}
        for live_path in self._preferred_live_check_paths():
            payload = read_json(live_path, [])
            results = payload if isinstance(payload, list) else [payload]
            for result in results:
                alpha_id = str(result.get("alpha_id") or "")
                if not alpha_id:
                    continue
                if alpha_id in submitted_alpha_ids or alpha_id in failed_submit_alpha_ids:
                    continue
                checks = live_checks_from_result(result)
                if not checks or failed_checks_from_check_list(checks):
                    continue
                row = dict(candidate_rows.get(alpha_id) or {})
                expression = normalize_expression(str(row.get("expression") or result.get("expression") or ""))
                if expression and expression in submitted_expressions:
                    continue
                row.update({
                    "alpha_id": alpha_id,
                    "live_check_path": relative_path(live_path, self.root),
                    "live_checks": checks,
                    "validation_source": "live_check",
                    "requires_live_recheck": False,
                    "pending_checks": [check.get("name") for check in pending_checks_from_check_list(checks)],
                    "self_corr": check_value(checks, "SELF_CORRELATION"),
                    "sub_universe_sharpe": check_value(checks, "LOW_SUB_UNIVERSE_SHARPE"),
                    "units_warning": units_warning_from_check_list(checks),
                })
                row["score"] = round(candidate_score(row), 4)
                self._upsert_ready_candidate(ready, row)

        for row in self._collect_current_scan_pass_rows(submitted_alpha_ids | failed_submit_alpha_ids, submitted_expressions):
            self._upsert_ready_candidate(ready, row)
        return sorted(ready.values(), key=lambda row: row.get("score", 0.0), reverse=True)

    def _collect_current_scan_pass_rows(
        self,
        submitted_alpha_ids: set[str],
        submitted_expressions: set[str],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self._current_scan_result_paths():
            payload = read_json(path, [])
            if not isinstance(payload, list):
                continue
            for result in payload:
                if not isinstance(result, dict):
                    continue
                alpha_id = str(result.get("alpha_id") or "")
                if not alpha_id or alpha_id in submitted_alpha_ids:
                    continue
                expression = normalize_expression(str(result.get("expression") or ""))
                if expression and expression in submitted_expressions:
                    continue
                checks = result.get("checks") or []
                if not row_metric_pass(result) or failed_checks_from_check_list(checks):
                    continue
                row = dict(result)
                row.update({
                    "alpha_id": alpha_id,
                    "expression": expression,
                    "source_path": relative_path(path, self.root),
                    "validation_source": "scan_result",
                    "requires_live_recheck": True,
                    "pending_checks": [check.get("name") for check in pending_checks_from_check_list(checks)],
                    "self_corr": check_value(checks, "SELF_CORRELATION"),
                    "sub_universe_sharpe": check_value(checks, "LOW_SUB_UNIVERSE_SHARPE"),
                    "units_warning": units_warning_from_check_list(checks),
                })
                row["score"] = round(candidate_score(row), 4)
                rows.append(row)
        return rows

    def _upsert_ready_candidate(self, ready: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
        alpha_id = str(row.get("alpha_id") or "")
        if not alpha_id:
            return
        existing = ready.get(alpha_id)
        if existing is None:
            ready[alpha_id] = row
            return
        existing_live = existing.get("validation_source") == "live_check"
        row_live = row.get("validation_source") == "live_check"
        if row_live and not existing_live:
            ready[alpha_id] = row
            return
        if row_live == existing_live and float(row.get("score") or 0.0) > float(existing.get("score") or 0.0):
            ready[alpha_id] = row

    def _load_candidate_rows_by_alpha(self) -> dict[str, dict[str, Any]]:
        rows_by_alpha: dict[str, dict[str, Any]] = {}
        for path in reversed(self._candidate_row_paths()):
            payload = read_json(path, [])
            if not isinstance(payload, list):
                continue
            for row in payload:
                if not isinstance(row, dict) or not row.get("alpha_id"):
                    continue
                merged = dict(row)
                merged["source_path"] = relative_path(path, self.root)
                rows_by_alpha[str(row["alpha_id"])] = merged
        return rows_by_alpha

    def write_daily_report(
        self,
        ledger: dict[str, Any],
        *,
        now: datetime | None = None,
        reason: str = "budget_complete",
        force: bool = False,
    ) -> tuple[Path, Path]:
        now = now or datetime.now()
        existing_report = ledger.get("last_budget_complete_report") or ledger.get("last_daily_report")
        summary_json = self.run_dir / f"{REPORT_BASENAME}.json"
        summary_md = self.run_dir / f"{REPORT_BASENAME}.md"
        if existing_report and not force and not self.dry_run:
            ready = self.collect_submit_ready()
            self.write_closed_loop_artifacts(ledger, ready=ready, now=now)
            write_json(self.ledger_path, ledger)
            return summary_json, self.root / existing_report
        ready = self.collect_submit_ready()
        closed_loop = self.write_closed_loop_artifacts(ledger, ready=ready, now=now)
        payload = {
            "daily_run_tag": self.run_tag,
            "generated_at": now.isoformat(timespec="seconds"),
            "report_reason": reason,
            "budget": {
                "daily_budget": ledger.get("daily_budget"),
                "spent_simulations": ledger.get("spent_simulations"),
                "remaining_simulations_after_commitments": ledger.get("remaining_simulations_after_commitments"),
                "stage_spend": ledger.get("stage_spend", {}),
            },
            "closed_loop": closed_loop,
            "submit_ready_count": len(ready),
            "submit_ready": ready,
            "recommendation": ready[0]["alpha_id"] if ready else None,
        }
        if not self.dry_run:
            write_json(summary_json, payload)
            snapshot = self.run_dir / "current_submit_candidate_snapshot.json"
            write_json(snapshot, ready)
            lines = [
            "# Daily Budget Complete Report",
                "",
                f"Daily run: `{self.run_tag}`",
                f"Generated at: `{payload['generated_at']}`",
                f"Reason: `{reason}`",
                f"Budget: `{ledger.get('spent_simulations')}` / `{ledger.get('daily_budget')}` spent",
                f"Submit-ready count: `{len(ready)}`",
                "",
            ]
            if ready:
                best = ready[0]
                lines.extend([
                    "## Best Candidate",
                    "",
                    f"- Alpha: `{best.get('alpha_id')}`",
                    f"- Score: `{best.get('score')}`",
                    f"- Sharpe: `{(best.get('metrics') or {}).get('sharpe')}`",
                    f"- Fitness: `{(best.get('metrics') or {}).get('fitness')}`",
                    f"- Turnover: `{(best.get('metrics') or {}).get('turnover')}`",
                    f"- Self-corr: `{best.get('self_corr')}`",
                    f"- Validation: `{best.get('validation_source')}`",
                    f"- Requires live re-check: `{best.get('requires_live_recheck')}`",
                    f"- Source: `{best.get('source_path')}`",
                    "",
                    "Expression:",
                    "",
                    "```text",
                    str(best.get("expression") or ""),
                    "```",
                    "",
                    "## Shortlist",
                    "",
                ])
                for row in ready[:10]:
                    metrics = row.get("metrics") or {}
                    lines.append(
                        f"- `{row.get('alpha_id')}` S={metrics.get('sharpe')} F={metrics.get('fitness')} "
                        f"T={metrics.get('turnover')} self_corr={row.get('self_corr')} "
                        f"source={row.get('validation_source')} recheck={row.get('requires_live_recheck')} "
                        f"score={row.get('score')}"
                    )
            else:
                lines.append("No scan-result or live-check PASS candidates were found when the budget completed.")
            write_text(summary_md, "\n".join(lines) + "\n")
            ledger["last_daily_report"] = relative_path(summary_md, self.root)
            ledger["last_budget_complete_report"] = relative_path(summary_md, self.root)
            ledger["current_stage"] = "budget_complete_report_written"
            ledger.pop("completion_email_sent_at", None)
            ledger.pop("completion_email_error", None)
            write_json(self.ledger_path, ledger)
            if reason == "budget_complete":
                submit_msg = self._auto_submit_direct()
                if submit_msg:
                    print(submit_msg, flush=True)
                self._emit_progress_callback(
                    "budget_complete",
                    ledger,
                    stage="budget_complete_report_written",
                    extra={
                        "summary_json": relative_path(summary_json, self.root),
                        "summary_md": relative_path(summary_md, self.root),
                        "submit_ready_count": len(ready),
                        "auto_submit_result": submit_msg,
                    },
                )
                write_json(self.ledger_path, ledger)
        return summary_json, summary_md

    def write_17_summary(self, ledger: dict[str, Any], *, now: datetime | None = None) -> tuple[Path, Path]:
        return self.write_daily_report(ledger, now=now, reason="manual_summary", force=True)

    def _run_once_tick(self, *, now: datetime, summary_only: bool = False) -> list[str]:
        replayed = self.drain_workflow_outbox() if not self.dry_run else 0
        ledger = self.load_or_create_ledger()
        messages = [f"ledger: {relative_path(self.ledger_path, self.root)}"]
        if replayed:
            messages.append(f"replayed workflow outbox events={replayed}")
        if self.reconcile_existing_stage_progress(ledger):
            messages.append("reconciled existing stage progress")
            if not self.dry_run:
                self.write_closed_loop_artifacts(ledger)
                self._emit_progress_callback(
                    "stage_progress_reconciled",
                    ledger,
                    stage=str(ledger.get("current_stage") or "reconciled"),
                    extra={"reason": "existing_stage_results_detected"},
                )
                write_json(self.ledger_path, ledger)
                messages.append("refreshed closed-loop artifacts after reconcile")
        if now.date() < self.run_date:
            messages.append(f"waiting for daily start: {self.run_date.isoformat()}T{DAY_START_TIME.isoformat()}")
            return messages
        sync_status = self.sync_submitted_registry()
        if sync_status not in {"ok", "skipped_dry_run", "skipped_disabled", "skipped_missing_credentials", "skipped_env"}:
            messages.append(f"submitted registry sync: {sync_status}")
        if summary_only:
            _, summary_md = self.write_daily_report(ledger, now=now, reason="manual_summary", force=True)
            messages.append(f"daily report: {relative_path(summary_md, self.root)}")
            return messages
        if budget_exhausted(ledger):
            _, summary_md = self.write_daily_report(ledger, now=now, reason="budget_complete")
            messages.append(f"budget complete report: {relative_path(summary_md, self.root)}")
            return messages
        self.run_llm_plan(ledger, now=now)
        plan, initial_action = self.run_scan_preflight(ledger, now=now)
        messages.append(f"stage action: {plan.stage} -> {initial_action}")
        if initial_action == "slice_scan_config":
            messages.append(
                f"prepared {plan.candidate_count} candidates: {relative_path(plan.sliced_config or Path(), self.root)}"
            )
            spent = self.execute_scan(plan, ledger)
            if spent:
                messages.append(f"executed scan spend={spent}")
                if budget_exhausted(ledger):
                    _, summary_md = self.write_daily_report(ledger, now=now, reason="budget_complete")
                    messages.append(f"budget complete report: {relative_path(summary_md, self.root)}")
            elif not self.execute_scans:
                messages.append("scan not executed; pass --execute-scans to consume budget")
        return messages

    def run_once(self, *, now: datetime | None = None, summary_only: bool = False) -> list[str]:
        now = now or datetime.now()
        try:
            messages = self._run_once_tick(now=now, summary_only=summary_only)
        except Exception as exc:
            if not self.dry_run:
                try:
                    self.write_run_manifest(now=now, status="failed", error_type=type(exc).__name__)
                except Exception as manifest_exc:
                    exc.add_note(f"run manifest checkpoint also failed: {type(manifest_exc).__name__}")
            raise
        if not self.dry_run:
            try:
                manifest_path = self.write_run_manifest(now=now, status="checkpointed")
                messages.append(f"run manifest: {relative_path(manifest_path, self.root)}")
            except Exception as exc:
                messages.append(f"run manifest unavailable: {type(exc).__name__}")
        return messages

    def run_daemon(self, *, poll_seconds: int = 900, continue_next_day: bool = True) -> None:
        while True:
            try:
                now = datetime.now()
                if now.date() < self.run_date:
                    time.sleep(max(60, poll_seconds))
                    continue
                existing_ledger = read_json(self.ledger_path, {})
                if now.date() > self.run_date and budget_exhausted(existing_ledger):
                    if not continue_next_day:
                        break
                    # Advance one day at a time; do not jump over stalled/backfill days
                    next_date = self.run_date + timedelta(days=1)
                    self._set_run_date(min(next_date, now.date()))
                    existing_ledger = read_json(self.ledger_path, {})
                if budget_exhausted(existing_ledger) and existing_ledger.get("last_budget_complete_report"):
                    if not continue_next_day:
                        break
                    time.sleep(max(60, poll_seconds))
                    continue
                messages = self.run_once(now=now)
                for message in messages:
                    print(message)
                ledger = read_json(self.ledger_path, {})
                if budget_exhausted(ledger) and ledger.get("last_budget_complete_report"):
                    if not continue_next_day:
                        break
                    time.sleep(max(60, poll_seconds))
                    continue
                if any(message.startswith("executed scan spend=") for message in messages):
                    continue
                time.sleep(max(60, poll_seconds))
            except Exception as exc:
                print(f"ERROR: daemon tick failed: {exc}", flush=True)
                import traceback
                traceback.print_exc()
                time.sleep(max(60, poll_seconds))

    def run_until_budget_complete(self, *, poll_seconds: int = 900) -> None:
        while True:
            now = datetime.now()
            existing_ledger = read_json(self.ledger_path, {})
            if budget_exhausted(existing_ledger) and existing_ledger.get("last_budget_complete_report"):
                break
            messages = self.run_once(now=now)
            for message in messages:
                print(message)
            ledger = read_json(self.ledger_path, {})
            if budget_exhausted(ledger) and ledger.get("last_budget_complete_report"):
                break
            if any(message.startswith("executed scan spend=") for message in messages):
                continue
            time.sleep(max(60, poll_seconds))


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Kimi daily alpha budget workflow.")
    parser.add_argument("--workspace-root", default=".", help="Workspace root containing configs/, .local/data/, scripts/.")
    parser.add_argument("--workflow-config", default=str(DEFAULT_WORKFLOW_CONFIG), help="Workflow budget config JSON.")
    parser.add_argument("--date", help="Daily run date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--budget-mode", choices=["conservative", "standard", "aggressive", "expanded_1500"], help="Daily budget mode.")
    parser.add_argument("--run-once", action="store_true", help="Advance one daily workflow tick and exit.")
    parser.add_argument("--daemon", action="store_true", help="Keep running daily budget ledgers; each new local date starts at 00:00 and runs until budget completion.")
    parser.add_argument("--stop-after-summary", action="store_true", help="In daemon mode, stop after the budget-complete report instead of waiting for the next day.")
    parser.add_argument("--summary-only", action="store_true", help="Only write the current daily submit report.")
    parser.add_argument("--execute-scans", action="store_true", help="Actually run budgeted BRAIN scans. Omit for planning only.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write files or run scans.")
    parser.add_argument("--poll-seconds", type=int, default=900, help="Daemon polling interval.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workflow = KimiDailyWorkflow(
        Path(args.workspace_root),
        workflow_config=Path(args.workflow_config),
        run_date=parse_date(args.date),
        budget_mode=args.budget_mode,
        execute_scans=args.execute_scans,
        dry_run=args.dry_run,
    )
    if args.daemon:
        workflow.run_daemon(poll_seconds=args.poll_seconds, continue_next_day=not args.stop_after_summary)
        return 0
    if not args.run_once and not args.summary_only:
        workflow.run_until_budget_complete(poll_seconds=args.poll_seconds)
        return 0
    messages = workflow.run_once(summary_only=args.summary_only)
    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
