from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from wqb_agent_lab.runtime.process_lock import PidFileLock


class PidFileLockTests(unittest.TestCase):
    def test_reclaims_lock_owned_by_dead_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker.lock"
            path.write_text(json.dumps({"pid": 999999}), encoding="utf-8")

            with PidFileLock(path, owner="test worker", pid_checker=lambda _pid: False):
                payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(os.getpid(), payload["pid"])
            self.assertFalse(path.exists())

    def test_preserves_lock_owned_by_live_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker.lock"
            path.write_text(json.dumps({"pid": 42}), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "test worker already running"):
                with PidFileLock(path, owner="test worker", pid_checker=lambda _pid: True):
                    pass

            self.assertTrue(path.exists())

    def test_reclaims_reused_pid_when_process_identity_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker.lock"
            path.write_text(
                json.dumps({"pid": 123, "process_identity": "old", "nonce": "old-lock"}),
                encoding="utf-8",
            )
            with PidFileLock(
                path,
                owner="test worker",
                pid_checker=lambda pid: pid == 123,
                identity_reader=lambda pid: "new" if pid == 123 else "current",
            ):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotEqual("old-lock", payload["nonce"])

    def test_exit_does_not_remove_replacement_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker.lock"
            lock = PidFileLock(path, owner="test worker", identity_reader=lambda _pid: "current")
            lock.__enter__()
            path.write_text(json.dumps({"pid": 999, "nonce": "replacement"}), encoding="utf-8")
            lock.__exit__(None, None, None)
            self.assertTrue(path.exists())
