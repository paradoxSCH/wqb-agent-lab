from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.atomic_json import atomic_write_json, locked_atomic_json_merge


class AtomicJsonTests(unittest.TestCase):
    def test_atomic_write_supports_queue_arrays(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.json"
            atomic_write_json(path, [{"alpha_id": "A1"}])
            self.assertEqual([{"alpha_id": "A1"}], json.loads(path.read_text(encoding="utf-8")))

    def test_locked_merge_reclaims_dead_owner_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            lock_path = path.with_name(f".{path.name}.lock")
            lock_path.write_text("999999\n", encoding="ascii")

            with patch("src.atomic_json.pid_is_running", return_value=False):
                result = locked_atomic_json_merge(path, {"status": "ok"}, timeout_seconds=0.1)

            self.assertEqual({"status": "ok"}, result)
            self.assertFalse(lock_path.exists())
