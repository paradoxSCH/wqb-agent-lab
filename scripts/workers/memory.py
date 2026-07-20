"""Asynchronous alpha-memory synchronization worker implementation."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.agent_memory_sync import sync_run_memory
from src.atomic_json import atomic_write_json
from src.process_lock import PidFileLock


MEMORY_STATE = "memory_sync_state.json"
MEMORY_LOCK = "memory_worker.lock"


SyncRunner = Callable[[Path, Path, Path | None], Any]


def memory_state_path(run_dir: Path | str) -> Path:
    return Path(run_dir) / MEMORY_STATE


class MemoryWorker:
    def __init__(
        self,
        root: Path | str,
        run_dir: Path | str,
        *,
        db_path: Path | str | None = None,
        sync_runner: SyncRunner | None = None,
    ) -> None:
        self.root = Path(root)
        self.run_dir = Path(run_dir)
        self.db_path = Path(db_path) if db_path is not None else None
        self.sync_runner = sync_runner or (lambda workspace, target, db: sync_run_memory(workspace, target, db_path=db))

    def run_once(self) -> dict[str, Any]:
        try:
            result = self.sync_runner(self.root, self.run_dir, self.db_path)
            payload = {
                "status": "ok",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "run_dir": _relative(self.run_dir, self.root),
                "report_path": _relative(Path(result.report_path), self.root),
                "nodes_written": int(result.nodes_written),
                "edges_written": int(result.edges_written),
                "events_recorded": int(result.events_recorded),
            }
        except Exception as exc:
            payload = {
                "status": "failed",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "run_dir": _relative(self.run_dir, self.root),
                "error": str(exc)[:500],
            }
        atomic_write_json(memory_state_path(self.run_dir), payload)
        return payload


class MemoryWorkerLock(PidFileLock):
    def __init__(self, run_dir: Path | str, *, pid_checker: Callable[[int], bool] | None = None) -> None:
        super().__init__(Path(run_dir) / MEMORY_LOCK, owner="memory worker", pid_checker=pid_checker)


def main() -> int:
    args = parse_args()
    root = Path(args.workspace_root).resolve()
    run_dir = Path(args.run_dir).resolve()
    db_path = Path(args.db).resolve() if args.db else None
    try:
        with MemoryWorkerLock(run_dir):
            worker = MemoryWorker(root, run_dir, db_path=db_path)
            while True:
                result = worker.run_once()
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if args.once or not args.daemon:
                    return 0
                time.sleep(max(30.0, float(args.poll_seconds)))
    except RuntimeError as exc:
        print(json.dumps({"status": "already_running", "message": str(exc)}, ensure_ascii=False), flush=True)
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Asynchronous alpha memory sync worker.")
    parser.add_argument("--workspace-root", default=".", help="Workspace root.")
    parser.add_argument("--run-dir", required=True, help="Run directory containing workflow artifacts.")
    parser.add_argument("--db", default=None, help="Optional alpha memory SQLite db path.")
    parser.add_argument("--once", action="store_true", help="Sync once and exit.")
    parser.add_argument("--daemon", action="store_true", help="Keep syncing periodically.")
    parser.add_argument("--poll-seconds", type=float, default=900.0, help="Daemon polling interval.")
    return parser.parse_args()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
