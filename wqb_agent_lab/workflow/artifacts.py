from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload: Any) -> None:
    from wqb_agent_lab.runtime.atomic_json import atomic_write_json

    atomic_write_json(path, payload)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_provenance(root: Path) -> dict[str, Any]:
    revision = str(os.getenv("GITHUB_SHA") or "").strip()
    revision_source = "environment" if revision else "unavailable"
    try:
        if not revision:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
            if completed.returncode == 0:
                revision = completed.stdout.strip()
                revision_source = "git"
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        dirty: bool | None = bool(status.stdout.strip()) if status.returncode == 0 else None
    except OSError:
        dirty = None
    return {
        "component": "wqb_agent_lab.workflow.engine.ResearchWorkflow",
        "revision": revision,
        "revision_source": revision_source,
        "tracked_files_dirty": dirty,
    }


def _workflow_artifact_schema(path: Path) -> str:
    if path.parent.name == "stage_checkpoints" and path.suffix.lower() == ".json":
        return "workflow_stage_result"
    return ""


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


