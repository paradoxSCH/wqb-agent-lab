from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from src.atomic_json import atomic_write_json
from src.process_lock import PidFileLock
from src.workflow_daemon import CompletionHookResult, run_completion_hooks


EVALUATION_STATE = Path(".local/data/evaluations/evaluation_worker_state.json")
EVALUATION_LOCK = Path(".local/data/evaluations/evaluation_worker.lock")


HookRunner = Callable[[Path], CompletionHookResult]


def evaluation_state_path(root: Path | str) -> Path:
    return Path(root) / EVALUATION_STATE


class EvaluationWorker:
    def __init__(self, root: Path | str, *, hook_runner: HookRunner | None = None) -> None:
        self.root = Path(root)
        self.hook_runner = hook_runner or (lambda path: run_completion_hooks(path, now=datetime.now()))

    def run_once(self) -> dict[str, object]:
        result = self.hook_runner(self.root)
        status = "evaluation_complete" if result.evaluation_ran else "no_completed_evaluation"
        payload = {
            "status": status,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "evaluation_ran": bool(result.evaluation_ran),
            "run_tag": result.run_tag,
            "report_path": result.report_path,
            "message": result.message,
        }
        path = evaluation_state_path(self.root)
        atomic_write_json(path, payload)
        return payload


class EvaluationWorkerLock(PidFileLock):
    def __init__(self, root: Path | str, *, pid_checker: Callable[[int], bool] | None = None) -> None:
        super().__init__(Path(root) / EVALUATION_LOCK, owner="evaluation worker", pid_checker=pid_checker)


def main() -> int:
    args = parse_args()
    root = Path(args.workspace_root).resolve()
    try:
        with EvaluationWorkerLock(root):
            worker = EvaluationWorker(root)
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
    parser = argparse.ArgumentParser(description="Asynchronous completion evaluation worker.")
    parser.add_argument("--workspace-root", default=".", help="Workspace root.")
    parser.add_argument("--once", action="store_true", help="Run one completion evaluation check and exit.")
    parser.add_argument("--daemon", action="store_true", help="Keep checking periodically.")
    parser.add_argument("--poll-seconds", type=float, default=900.0, help="Daemon polling interval.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
