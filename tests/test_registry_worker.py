from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scripts.registry_worker import RegistryWorker, RegistryWorkerLock, registry_state_path


class RegistryWorkerTests(unittest.TestCase):
    def test_lock_reclaims_dead_worker_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_path = root / ".local/data/registry/registry_worker.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text(json.dumps({"pid": 999999}), encoding="utf-8")

            with RegistryWorkerLock(root, pid_checker=lambda _pid: False):
                self.assertTrue(lock_path.exists())

            self.assertFalse(lock_path.exists())

    def test_run_once_writes_success_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: list[list[str]] = []

            worker = RegistryWorker(root, runner=lambda command: calls.append(command) or 0)
            result = worker.run_once(now=datetime(2026, 7, 5, 13, 40, 0))

            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(calls), 1)
            self.assertIn("scripts.fetch_submitted", calls[0])
            state = json.loads(registry_state_path(root).read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "ok")
            self.assertEqual(state["last_exit_code"], 0)

    def test_run_once_records_failure_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            worker = RegistryWorker(root, runner=lambda _command: 7)
            result = worker.run_once(now=datetime(2026, 7, 5, 13, 40, 0))

            self.assertEqual(result["status"], "failed_exit_7")
            state = json.loads(registry_state_path(root).read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "failed_exit_7")


if __name__ == "__main__":
    unittest.main()
