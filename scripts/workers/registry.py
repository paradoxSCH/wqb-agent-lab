"""Asynchronous submitted-alpha registry worker implementation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from wqb_agent_lab.runtime.atomic_json import atomic_write_json
from wqb_agent_lab.runtime.process_lock import PidFileLock

REGISTRY_STATE = Path(".local/data/registry/registry_state.json")
REGISTRY_LOCK = Path(".local/data/registry/registry_worker.lock")


Runner = Callable[[list[str]], int]


def registry_state_path(root: Path | str) -> Path:
    return Path(root) / REGISTRY_STATE


class RegistryWorker:
    def __init__(self, root: Path | str, *, runner: Runner | None = None) -> None:
        self.root = Path(root)
        self.runner = runner or self._run_command

    def run_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now()
        command = [
            sys.executable,
            "-m",
            "scripts.registry.fetch_submitted",
            "--output",
            ".local/data/registry/submitted_alphas.json",
            "--expressions",
            ".local/data/registry/submitted_expressions.txt",
            "--blocklist",
            ".local/data/registry/submitted_blocklist.json",
        ]
        exit_code = self.runner(command)
        status = "ok" if exit_code == 0 else f"failed_exit_{exit_code}"
        payload = {
            "status": status,
            "updated_at": now.isoformat(timespec="seconds"),
            "last_exit_code": int(exit_code),
            "command": command,
        }
        self._write_state(payload)
        return payload

    def _write_state(self, payload: dict[str, Any]) -> None:
        atomic_write_json(registry_state_path(self.root), payload)

    def _run_command(self, command: list[str]) -> int:
        completed = subprocess.run(command, cwd=self.root, check=False)
        return int(completed.returncode)


class RegistryWorkerLock(PidFileLock):
    def __init__(self, root: Path | str, *, pid_checker: Callable[[int], bool] | None = None) -> None:
        super().__init__(Path(root) / REGISTRY_LOCK, owner="registry worker", pid_checker=pid_checker)


def main() -> int:
    args = parse_args()
    root = Path(args.workspace_root).resolve()
    try:
        with RegistryWorkerLock(root):
            worker = RegistryWorker(root)
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
    parser = argparse.ArgumentParser(description="Asynchronous submitted registry sync worker.")
    parser.add_argument("--workspace-root", default=".", help="Workspace root.")
    parser.add_argument("--once", action="store_true", help="Sync once and exit.")
    parser.add_argument("--daemon", action="store_true", help="Keep syncing periodically.")
    parser.add_argument("--poll-seconds", type=float, default=1800.0, help="Daemon polling interval.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
