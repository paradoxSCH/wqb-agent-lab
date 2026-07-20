from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from wqb_agent_lab.workflow.callbacks import emit_agent_callback
from wqb_agent_lab.contracts import list_schema_names, schema_digest
from wqb_agent_lab.evaluation.diagnosis_policy import evaluate_diagnosis_policies
from wqb_agent_lab.evaluation.failure_diagnosis import primary_diagnosis_type
from wqb_agent_lab.llm.provider import LLMProvider
from wqb_agent_lab.workflow.llm_planning import LLMPlanAdapter
from wqb_agent_lab.governance.policy_feedback import (
    aggregate_shadow_evidence,
    cap_recommended_candidates,
    record_shadow_decision,
    resolve_feedback_mode,
    score_shadow_decisions,
)
from wqb_agent_lab.evaluation.output.types import OutputEvaluationRecord
from wqb_agent_lab.evaluation.output.validators import validate_expression_candidates
from wqb_agent_lab.research.policy import (
    ResearchPolicy,
    evaluate_candidate_boundaries,
    load_research_policy,
    policy_digest,
)
from wqb_agent_lab.research.self_corr_policy import SELF_CORR_NEAR_REPAIR_MAX
from wqb_agent_lab.runtime import (
    OperationJournal,
    RunManifest,
    collect_artifact_provenance,
    payload_fingerprint,
)
from wqb_agent_lab.platform import load_operator_names
from wqb_agent_lab.workflow.artifacts import (
    _file_sha256,
    _git_provenance,
    _workflow_artifact_schema,
    daily_run_tag,
    read_json,
    relative_path,
    write_json,
    write_text,
)
from wqb_agent_lab.workflow.candidates import (
    candidate_identity,
    choose_budgeted_candidates,
    completed_candidate_count,
    metric_value,
    normalize_expression,
)
from wqb_agent_lab.workflow.config_selection import pick_scan_config
from wqb_agent_lab.workflow import diagnosis as workflow_diagnosis
from wqb_agent_lab.workflow.models import StagePlan
from wqb_agent_lab.workflow.postprocessing import WorkflowPostprocessor
from wqb_agent_lab.workflow.reporting import WorkflowReporter
from wqb_agent_lab.workflow.runner import WorkflowRunner
from wqb_agent_lab.workflow.stages import StageCheckpointStore, StageOutcome, StageRunner
from wqb_agent_lab.workflow.submitted_registry import SubmittedRegistryService


DEFAULT_WORKFLOW_CONFIG = Path(".local/research/workflows/production.json")
RUNS_ROOT = Path(".local/data/runs/continuous-alpha")
CONFIGS_ROOT = Path(".local/research/scans/continuous-alpha")
SUBMITTED_REGISTRY_PATH = Path(".local/data/registry/submitted_alphas.json")
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


class ResearchWorkflow:
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
        self._active_registry_snapshot: tuple[set[str], set[str]] | None = None
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
        return SubmittedRegistryService(self).sync()

    def run_registry_stage(self, *, now: datetime | None = None) -> str:
        return SubmittedRegistryService(self).run_stage(now=now)

    def _submitted_registry(self) -> tuple[set[str], set[str]]:
        return SubmittedRegistryService(self).snapshot()

    def _read_submitted_registry(self) -> tuple[set[str], set[str]]:
        return SubmittedRegistryService(self).read()

    def _confirmed_submission_state_alpha_ids(self) -> set[str]:
        return SubmittedRegistryService(self).confirmed_submission_ids()

    def _failed_submit_attempt_alpha_ids(self) -> set[str]:
        return SubmittedRegistryService(self).failed_attempt_ids()

    def _preferred_live_check_paths(self) -> list[Path]:
        return SubmittedRegistryService(self).preferred_live_check_paths()

    def _current_scan_result_paths(self) -> list[Path]:
        return sorted(self.run_dir.glob("*_results.json"))

    def _row_family(self, row: dict[str, Any]) -> str:
        return workflow_diagnosis.row_family(row)

    def _row_skeleton(self, row: dict[str, Any]) -> str:
        return workflow_diagnosis.row_skeleton(row)

    def _current_scan_rows(self) -> list[dict[str, Any]]:
        return workflow_diagnosis.load_scan_rows(
            self.root, self._current_scan_result_paths()
        )

    def _diagnose_scan_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return workflow_diagnosis.diagnose_scan_row(row)

    def _route_diagnosed_row(
        self,
        diagnosed: dict[str, Any],
        submitted_alpha_ids: set[str],
        submitted_expressions: set[str],
        failed_submit_alpha_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        return workflow_diagnosis.route_diagnosed_row(
            diagnosed,
            submitted_alpha_ids,
            submitted_expressions,
            failed_submit_alpha_ids,
        )

    def _classify_scan_row(
        self,
        row: dict[str, Any],
        submitted_alpha_ids: set[str],
        submitted_expressions: set[str],
        failed_submit_alpha_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        return workflow_diagnosis.classify_scan_row(
            row,
            submitted_alpha_ids,
            submitted_expressions,
            failed_submit_alpha_ids,
        )

    def _classified_scan_rows(self) -> list[dict[str, Any]]:
        submitted_alpha_ids, submitted_expressions = self._submitted_registry()
        failed_submit_alpha_ids = self._failed_submit_attempt_alpha_ids()
        return [
            self._classify_scan_row(row, submitted_alpha_ids, submitted_expressions, failed_submit_alpha_ids)
            for row in self._current_scan_rows()
        ]

    def _local_stage_input_digest(
        self,
        payload: dict[str, Any],
        paths: list[Path],
    ) -> str:
        material = {
            "payload": payload,
            "files": [
                {
                    "path": relative_path(path, self.root),
                    "sha256": _file_sha256(path),
                }
                for path in sorted({path.resolve() for path in paths}, key=lambda item: item.as_posix())
            ],
        }
        encoded = json.dumps(
            material,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def run_diagnosis_stage(
        self,
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        now = now or datetime.now()
        source_paths = self._current_scan_result_paths()
        diagnosed_rows: list[dict[str, Any]] | None = None
        output_path = self.run_dir / "diagnosis_results.json"

        def execute() -> StageOutcome:
            nonlocal diagnosed_rows
            diagnosed_rows = [self._diagnose_scan_row(row) for row in self._current_scan_rows()]
            if not self.dry_run:
                write_json(output_path, diagnosed_rows)
            return StageOutcome.create(
                artifacts=(relative_path(output_path, self.root),) if output_path.is_file() else (),
                output={
                    "row_count": len(diagnosed_rows),
                    "diagnosis_count": sum(
                        len(row.get("failure_diagnoses") or []) for row in diagnosed_rows
                    ),
                },
                extensions={
                    "remote_side_effects": False,
                    "preserves_open_candidate_fields": True,
                },
            )

        if self.dry_run:
            execute()
        else:
            StageRunner(self.stage_checkpoint_store).run(
                run_id=self.run_tag,
                stage_id="diagnosis",
                input_digest=self._local_stage_input_digest(
                    {"run_tag": self.run_tag, "source_count": len(source_paths)},
                    source_paths,
                ),
                execute=execute,
                replay_policy="safe",
                started_at=now,
            )
        if diagnosed_rows is None:
            raise RuntimeError("diagnosis stage completed without rows")
        return diagnosed_rows

    def run_triage_stage(
        self,
        ledger: dict[str, Any],
        diagnosed_rows: list[dict[str, Any]],
        *,
        ready: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now()
        ready_rows = ready if ready is not None else self.collect_submit_ready()
        submitted_alpha_ids, submitted_expressions = self._submitted_registry()
        failed_submit_alpha_ids = self._failed_submit_attempt_alpha_ids()
        classified_rows: list[dict[str, Any]] | None = None
        state: dict[str, Any] | None = None

        def execute() -> StageOutcome:
            nonlocal classified_rows, state
            classified_rows = [
                self._route_diagnosed_row(
                    row,
                    submitted_alpha_ids,
                    submitted_expressions,
                    failed_submit_alpha_ids,
                )
                for row in diagnosed_rows
            ]
            state = self.write_closed_loop_artifacts(
                ledger,
                ready=ready_rows,
                now=now,
                classified_rows=classified_rows,
                run_postprocessors=False,
            )
            artifacts = tuple(
                sorted(
                    str(path)
                    for path in (state.get("artifacts") or {}).values()
                    if (self.root / str(path)).is_file()
                )
            )
            return StageOutcome.create(
                artifacts=artifacts,
                output={
                    "counts": state.get("counts") or {},
                    "route_decisions": sorted(
                        {
                            str(row.get("route_decision") or "")
                            for row in classified_rows
                            if row.get("route_decision")
                        }
                    ),
                },
                extensions={
                    "remote_side_effects": False,
                    "research_routes_are_advisory": True,
                },
            )

        input_payload = {
            "run_tag": self.run_tag,
            "diagnosed_rows": diagnosed_rows,
            "ready_rows": ready_rows,
            "submitted_alpha_ids": sorted(submitted_alpha_ids),
            "submitted_expressions": sorted(submitted_expressions),
            "failed_submit_alpha_ids": sorted(failed_submit_alpha_ids),
        }
        if self.dry_run:
            execute()
        else:
            StageRunner(self.stage_checkpoint_store).run(
                run_id=self.run_tag,
                stage_id="triage",
                input_digest=self._local_stage_input_digest(input_payload, []),
                execute=execute,
                replay_policy="safe",
                started_at=now,
            )
        if state is None:
            raise RuntimeError("triage stage completed without state")
        if not self.dry_run:
            self._run_closed_loop_postprocessors(
                state,
                self.run_dir / "iteration_state.json",
                now=now,
            )
            ledger["closed_loop"] = state
        return state

    def run_diagnosis_triage(
        self,
        ledger: dict[str, Any],
        *,
        ready: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        diagnosed_rows = self.run_diagnosis_stage(now=now)
        return self.run_triage_stage(ledger, diagnosed_rows, ready=ready, now=now)

    def _low_value_avoid_entries(self, low_value_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return workflow_diagnosis.low_value_avoid_entries(low_value_rows)

    def _dedupe_triage_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return workflow_diagnosis.dedupe_triage_rows(rows)

    def _family_efficiency(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return workflow_diagnosis.family_efficiency(rows)

    def write_closed_loop_artifacts(
        self,
        ledger: dict[str, Any],
        *,
        ready: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
        classified_rows: list[dict[str, Any]] | None = None,
        run_postprocessors: bool = True,
    ) -> dict[str, Any]:
        now = now or datetime.now()
        ready_rows = ready if ready is not None else self.collect_submit_ready()
        scan_rows = classified_rows if classified_rows is not None else self._classified_scan_rows()
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
        diagnosis_policy = evaluate_diagnosis_policies(scan_rows, now=now)
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
            if run_postprocessors:
                self._run_closed_loop_postprocessors(
                    state,
                    paths["iteration_state"],
                    now=now,
                )
        ledger["closed_loop"] = state
        return state

    def _run_closed_loop_postprocessors(
        self,
        state: dict[str, Any],
        iteration_state_path: Path,
        *,
        now: datetime | None = None,
    ) -> None:
        WorkflowPostprocessor(self).run_closed_loop(
            state,
            iteration_state_path,
            now=now,
        )

    def _memory_stage_input_paths(self) -> list[Path]:
        return WorkflowPostprocessor(self).memory_input_paths()

    def run_memory_stage(self, *, now: datetime | None = None) -> Path | None:
        return WorkflowPostprocessor(self).run_memory(now=now)

    def _evaluation_stage_input_paths(self) -> list[Path]:
        return WorkflowPostprocessor(self).evaluation_input_paths()

    def run_evaluation_stage(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[Path, Path]:
        return WorkflowPostprocessor(self).run_evaluation(now=now)

    def _workflow_config_reference(self) -> str:
        try:
            return relative_path(self.workflow_config_path, self.root)
        except ValueError:
            return self.workflow_config_path.as_posix()

    def _diagnosis_policy_summary(self, report: dict[str, Any]) -> str:
        return workflow_diagnosis.diagnosis_policy_summary(self.run_tag, report)

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
        existing_manifest = RunManifest.from_dict(existing) if existing else None
        created_at = (
            existing_manifest.created_at
            if existing_manifest is not None
            else now.isoformat(timespec="seconds")
        )
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
        prompt_path = self.llm_adapter.prompt_path(self.root, self.run_dir, self.run_tag)
        prompt_sha256 = (
            _file_sha256(prompt_path)
            if prompt_path.is_file()
            else hashlib.sha256(b"planner-disabled").hexdigest()
            if not self.llm_adapter.is_configured()
            else ""
        )
        operator_names = sorted(load_operator_names())
        operator_catalog_sha256 = hashlib.sha256(
            json.dumps(
                operator_names,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        manifest = RunManifest.create(
            run_id=self.run_tag,
            created_at=created_at,
            code=_git_provenance(self.root),
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
                "provider_config_sha256": str(
                    self.llm_adapter.metadata().get("config_digest") or ""
                ),
                "prompt_sha256": prompt_sha256,
            },
            research={
                "run_date": self.run_date.isoformat(),
                "budget_mode": self.budget_mode,
                "current_stage": str(ledger.get("current_stage") or ""),
                "operator_catalog_sha256": operator_catalog_sha256,
                "operator_count": len(operator_names),
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
            producer="research_workflow",
            schema_resolver=_workflow_artifact_schema,
        )
        artifacts += collect_artifact_provenance(
            self.root,
            self.config_dir,
            producer="research_workflow",
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
                self.run_diagnosis_triage(ledger)
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
        return pick_scan_config(
            self.root,
            configs_root=CONFIGS_ROOT,
            runs_root=RUNS_ROOT,
            run_date=self.run_date,
        )

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
        raw_error = payload.get("error")
        error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
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
        paths.update((self.root / RUNS_ROOT).glob("*/policy_feedback_shadow_evaluation.json"))
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
                plan = self.prepare_budgeted_scan(plan, now=now)
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
                    self.run_dir / "policy_feedback_shadow.json",
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
                    "policy_feedback_governance": plan.policy_feedback_governance or {},
                },
                extensions={
                    "remote_side_effects": False,
                    "feedback_default_is_shadow": True,
                    "control_requires_promotion_gate": True,
                },
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

    def prepare_budgeted_scan(
        self,
        plan: StagePlan,
        *,
        now: datetime | None = None,
    ) -> StagePlan:
        now = now or datetime.now()
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
        baseline_selected = list(selected)
        selected, policy_context, recommended, overflow = self._apply_policy_feedback_controls(
            selected,
            max_count,
        )
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
            "policy_budget_caps_recommended": policy_context[
                "policy_budget_caps_recommended"
            ],
            "policy_feedback_governance": policy_context["governance"],
            "recommended_policy_experiments": policy_context[
                "recommended_policy_experiments"
            ],
            "recommended_policy_action_lanes": policy_context[
                "recommended_policy_action_lanes"
            ],
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
        plan.policy_feedback_governance = dict(policy_context["governance"])
        if not self.dry_run and policy_context["governance"]["effective_mode"] != "off":
            record_shadow_decision(
                self.run_dir,
                stage=plan.stage,
                output_path=relative_path(output_path, self.root),
                baseline_candidates=baseline_selected,
                recommended_candidates=recommended,
                governance=policy_context["governance"],
                caps_applied=policy_context["policy_budget_caps_recommended"],
                overflow_candidates=overflow,
                now=now,
            )
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
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        feedback_config = self.config.get("policy_feedback")
        feedback_config = feedback_config if isinstance(feedback_config, dict) else {}
        evidence = aggregate_shadow_evidence(self.root / RUNS_ROOT)
        governance = resolve_feedback_mode(feedback_config, evidence)
        recommended, caps_recommended, overflow = cap_recommended_candidates(
            candidates,
            budget,
        )
        min_exploration_share = max(
            0.1,
            min(1.0, float(feedback_config.get("control_min_exploration_share") or 0.2)),
        )
        exploration_slots = min(
            len(overflow),
            max(1, int(max(budget, 1) * min_exploration_share + 0.999999)),
        )
        if exploration_slots:
            recommended.extend(overflow[:exploration_slots])
        recommended = recommended[:budget]
        effective_mode = str(governance["effective_mode"])
        selected = recommended if effective_mode == "control" else list(candidates)
        recommended_context = self._policy_feedback_context(
            recommended,
            caps_recommended,
        )
        applied_context = (
            recommended_context
            if effective_mode == "control"
            else self._policy_feedback_context([], {})
        )
        context = {
            **applied_context,
            "recommended_policy_experiments": recommended_context[
                "required_policy_experiments"
            ],
            "recommended_policy_action_lanes": recommended_context[
                "policy_action_lanes"
            ],
            "policy_budget_caps_recommended": caps_recommended,
            "governance": {
                **governance,
                "baseline_candidate_count": len(candidates),
                "recommended_candidate_count": len(recommended),
                "effective_candidate_count": len(selected),
                "exploration_overflow_admitted": exploration_slots,
                "control_min_exploration_share": min_exploration_share,
            },
        }
        return selected, context, recommended, overflow

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
        from wqb_agent_lab.evaluation.attribution import record_scan_decision

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
            policy_feedback_governance=plan.policy_feedback_governance,
        )

    def _score_decision_attribution(self) -> None:
        score_shadow_decisions(self.root, self.run_dir)
        if self._decision_attribution_enabled():
            from wqb_agent_lab.evaluation.attribution import score_decision_outcomes

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
        state = self.run_submission_stage()
        summary = state.get("summary") or {}
        active_count = sum(
            int(summary.get(key) or 0)
            for key in (
                "queued",
                "live_checking_count",
                "waiting_for_checks_count",
                "pending_confirmation_count",
                "accepted_but_unconfirmed_count",
                "throttled_count",
                "submission_unknown_commit_count",
                "submission_reconciliation_pending_count",
            )
        )
        if active_count <= 0:
            return "submission worker queue has no active jobs"
        from wqb_agent_lab.governance.side_effects import evaluate_side_effect_capability

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

    def run_submission_stage(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Checkpoint durable submission intent before delegating remote writes."""
        from scripts.submit.submission_worker import enqueue_submission_jobs

        now = now or datetime.now()
        backlog_path = self.run_dir / "submission_backlog.json"
        state: dict[str, Any] | None = None

        def execute() -> StageOutcome:
            nonlocal state
            state = enqueue_submission_jobs(self.run_dir, now=now)
            summary = dict(state.get("summary") or {})
            return StageOutcome.create(
                artifacts=(relative_path(self.run_dir / "submission_state.json", self.root),),
                output={"summary": summary},
                extensions={
                    "remote_side_effects": False,
                    "remote_execution_delegated_to_journaled_worker": True,
                },
            )

        if self.dry_run:
            return {"jobs": [], "summary": {}}
        StageRunner(self.stage_checkpoint_store).run(
            run_id=self.run_tag,
            stage_id="submission",
            input_digest=self._local_stage_input_digest(
                {"run_tag": self.run_tag},
                [backlog_path] if backlog_path.is_file() else [],
            ),
            execute=execute,
            replay_policy="safe",
            started_at=now,
        )
        if state is None:
            raise RuntimeError("submission stage completed without queue state")
        return state

    def collect_submit_ready(self) -> list[dict[str, Any]]:
        return WorkflowReporter(self).collect_submit_ready()

    def write_daily_report(
        self,
        ledger: dict[str, Any],
        *,
        now: datetime | None = None,
        reason: str = "budget_complete",
        force: bool = False,
    ) -> tuple[Path, Path]:
        return WorkflowReporter(self).write_daily_report(
            ledger, now=now, reason=reason, force=force
        )

    def write_17_summary(
        self, ledger: dict[str, Any], *, now: datetime | None = None
    ) -> tuple[Path, Path]:
        return self.write_daily_report(
            ledger, now=now, reason="manual_summary", force=True
        )

    def _run_once_tick(self, *, now: datetime, summary_only: bool = False) -> list[str]:
        return WorkflowRunner(self).run_tick(now=now, summary_only=summary_only)

    def run_once(self, *, now: datetime | None = None, summary_only: bool = False) -> list[str]:
        return WorkflowRunner(self).run_once(now=now, summary_only=summary_only)

    def run_daemon(self, *, poll_seconds: int = 900, continue_next_day: bool = True) -> None:
        WorkflowRunner(self).run_daemon(
            poll_seconds=poll_seconds,
            continue_next_day=continue_next_day,
        )

    def run_until_budget_complete(self, *, poll_seconds: int = 900) -> None:
        WorkflowRunner(self).run_until_budget_complete(poll_seconds=poll_seconds)


def main() -> int:
    from .cli import main as cli_main

    return cli_main()
