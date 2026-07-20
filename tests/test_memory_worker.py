from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from scripts.workers.memory import MemoryWorker, MemoryWorkerLock, memory_state_path


@dataclass(frozen=True)
class FakeSyncResult:
    report_path: Path
    nodes_written: int
    edges_written: int
    events_recorded: int


class MemoryWorkerTests(unittest.TestCase):
    def test_lock_reclaims_dead_worker_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            lock_path = run_dir / "memory_worker.lock"
            lock_path.write_text(json.dumps({"pid": 999999}), encoding="utf-8")

            with MemoryWorkerLock(run_dir, pid_checker=lambda _pid: False):
                self.assertTrue(lock_path.exists())

            self.assertFalse(lock_path.exists())

    def test_run_once_writes_state_from_sync_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "daily-run"
            report_path = run_dir / "memory_sync_report.json"
            calls: list[tuple[Path, Path, Path | None]] = []

            def fake_sync(workspace: Path, target: Path, db_path: Path | None = None) -> FakeSyncResult:
                calls.append((workspace, target, db_path))
                target.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps({"daily_run_tag": "daily-run"}), encoding="utf-8")
                return FakeSyncResult(report_path=report_path, nodes_written=3, edges_written=2, events_recorded=5)

            worker = MemoryWorker(root, run_dir, db_path=root / ".local" / "data" / "memory" / "alpha_memory.db", sync_runner=fake_sync)
            result = worker.run_once()

            self.assertEqual(result["status"], "ok")
            self.assertEqual(calls[0][0], root)
            self.assertEqual(calls[0][1], run_dir)
            state = json.loads(memory_state_path(run_dir).read_text(encoding="utf-8"))
            self.assertEqual(state["nodes_written"], 3)
            self.assertEqual(state["report_path"], ".local/data/runs/continuous-alpha/daily-run/memory_sync_report.json")


if __name__ == "__main__":
    unittest.main()
