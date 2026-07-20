from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .artifacts import _json_file_fresh, read_json, relative_path
from .candidates import (
    confirmed_submission_state_alpha_ids,
    failed_submit_attempt_alpha_ids,
    submitted_registry_entries,
)
from .stages import StageCheckpointStore, StageOutcome, StageRunner


RUNS_ROOT = Path(".local/data/runs/continuous-alpha")
SUBMITTED_REGISTRY_PATH = Path(".local/data/registry/submitted_alphas.json")


class RegistryWorkflow(Protocol):
    root: Path
    run_dir: Path
    run_tag: str
    config: dict[str, Any]
    dry_run: bool
    stage_checkpoint_store: StageCheckpointStore
    _active_registry_snapshot: tuple[set[str], set[str]] | None

    def _local_stage_input_digest(
        self, payload: dict[str, Any], paths: list[Path]
    ) -> str: ...
    def sync_submitted_registry(self) -> str: ...


class SubmittedRegistryService:
    """Own submitted-alpha snapshots and their read-only refresh lifecycle."""

    def __init__(self, workflow: RegistryWorkflow) -> None:
        self.workflow = workflow

    def sync(self) -> str:
        workflow = self.workflow
        if workflow.dry_run:
            return "skipped_dry_run"
        if workflow.config.get("submitted_registry_sync_enabled") is False:
            return "skipped_disabled"
        if str(os.getenv("WQB_SKIP_SUBMITTED_REGISTRY_SYNC", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return "skipped_env"
        if not os.getenv("WQB_EMAIL") or not os.getenv("WQB_PASSWORD"):
            return "skipped_missing_credentials"
        state_path = (
            workflow.root / ".local" / "data" / "registry" / "registry_state.json"
        )
        max_age_seconds = int(
            workflow.config.get("submitted_registry_cache_max_age_seconds") or 1800
        )
        if _json_file_fresh(state_path, max_age_seconds):
            state = read_json(state_path, {})
            if isinstance(state, dict) and state.get("status") == "ok":
                return "cache_ok"
        command = [
            sys.executable,
            "-m",
            "scripts.workers.registry",
            "--workspace-root",
            str(workflow.root),
            "--once",
        ]
        log_path = (
            workflow.root / ".local" / "data" / "registry" / "registry_worker.log"
        )
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with open(log_path, "a", encoding="utf-8") as log_fh:
                subprocess.Popen(
                    command,
                    cwd=workflow.root,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
        except Exception as exc:
            return f"worker_launch_failed:{str(exc)[:120]}"
        return "worker_started"

    def run_stage(self, *, now: datetime | None = None) -> str:
        workflow = self.workflow
        now = now or datetime.now()
        workflow._active_registry_snapshot = None
        registry_root = workflow.root / ".local" / "data" / "registry"
        snapshot_paths = [
            path
            for path in (
                registry_root / "registry_state.json",
                registry_root / "submitted_alphas.json",
                registry_root / "submitted_expressions.txt",
                registry_root / "submitted_blocklist.json",
            )
            if path.is_file()
        ]
        status = "skipped_dry_run" if workflow.dry_run else ""

        def execute() -> StageOutcome:
            nonlocal status
            alpha_ids, expressions = self.read()
            workflow._active_registry_snapshot = (set(alpha_ids), set(expressions))
            status = workflow.sync_submitted_registry()
            artifacts = tuple(
                relative_path(path, workflow.root)
                for path in snapshot_paths
                if path.is_file()
            )
            return StageOutcome.create(
                artifacts=artifacts,
                output={
                    "status": status,
                    "submitted_alpha_count": len(alpha_ids),
                    "submitted_expression_count": len(expressions),
                },
                extensions={
                    "remote_side_effects": False,
                    "refresh_is_read_only": True,
                    "refresh_worker_is_lock_guarded": True,
                },
            )

        if workflow.dry_run:
            return status
        StageRunner(workflow.stage_checkpoint_store).run(
            run_id=workflow.run_tag,
            stage_id="registry",
            input_digest=workflow._local_stage_input_digest(
                {
                    "sync_enabled": workflow.config.get(
                        "submitted_registry_sync_enabled"
                    ),
                    "cache_max_age_seconds": workflow.config.get(
                        "submitted_registry_cache_max_age_seconds"
                    ),
                },
                snapshot_paths,
            ),
            execute=execute,
            replay_policy="safe",
            started_at=now,
        )
        return status

    def snapshot(self) -> tuple[set[str], set[str]]:
        snapshot = self.workflow._active_registry_snapshot
        if snapshot is not None:
            return set(snapshot[0]), set(snapshot[1])
        return self.read()

    def read(self) -> tuple[set[str], set[str]]:
        workflow = self.workflow
        payload = read_json(workflow.root / SUBMITTED_REGISTRY_PATH, {})
        if not isinstance(payload, dict):
            submitted_alpha_ids: set[str] = set()
            submitted_expressions: set[str] = set()
        else:
            submitted_alpha_ids, submitted_expressions = submitted_registry_entries(
                payload
            )
        submitted_alpha_ids.update(self.confirmed_submission_ids())
        return submitted_alpha_ids, submitted_expressions

    def confirmed_submission_ids(self) -> set[str]:
        alpha_ids: set[str] = set()
        runs_root = self.workflow.root / RUNS_ROOT
        if not runs_root.exists():
            return alpha_ids
        for path in runs_root.glob("*/submission_state.json"):
            alpha_ids.update(
                confirmed_submission_state_alpha_ids(read_json(path, {}))
            )
        return alpha_ids

    def failed_attempt_ids(self) -> set[str]:
        workflow = self.workflow
        data_roots = [workflow.root / RUNS_ROOT, workflow.root / ".local" / "data"]
        patterns = [
            "**/*submit*_results*.json",
            "**/*resubmit*.json",
            "**/submission_attempts*.json",
        ]
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

    def preferred_live_check_paths(self) -> list[Path]:
        workflow = self.workflow
        current_paths = sorted(workflow.run_dir.glob("live-check-final/*.json"))
        if current_paths:
            return current_paths
        return sorted((workflow.root / RUNS_ROOT).glob("*/live-check-final/*.json"))
