from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


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
            "scripts.fetch_submitted",
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
        path = registry_state_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _run_command(self, command: list[str]) -> int:
        completed = subprocess.run(command, cwd=self.root, check=False)
        return int(completed.returncode)


class RegistryWorkerLock:
    def __init__(self, root: Path | str) -> None:
        self.path = Path(root) / REGISTRY_LOCK

    def __enter__(self) -> "RegistryWorkerLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"registry worker already running: {self.path}") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "created_at": datetime.now().isoformat(timespec="seconds")}, handle, ensure_ascii=False)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return


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
