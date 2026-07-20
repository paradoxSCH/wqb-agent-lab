from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from wqb_agent_lab.runtime import OperationJournal
from wqb_agent_lab.runtime.scan import run_scan


class ReadOnlyClient:
    def __init__(self, journal: OperationJournal) -> None:
        self.session = type("JournalSession", (), {"operation_journal": journal})()

    def get_user_alphas(self, **_params):
        raise AssertionError("no remote evidence should be requested without journal candidates")


def test_standalone_reconcile_only_uses_stable_run_identity_without_live_capability() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = root / "scan.json"
        config.write_text(
            json.dumps(
                {
                    "output": str(root / "results.json"),
                    "candidates": [
                        {
                            "expression": "future_operator(custom_field)",
                            "settings": {"region": "USA", "novelSetting": "OPEN"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        journal = OperationJournal(root / "operations.db")
        run_ids: list[str] = []

        def from_config(*_args, **kwargs):
            run_ids.append(str(kwargs.get("run_id") or ""))
            return ReadOnlyClient(journal)

        with patch("wqb_agent_lab.runtime.scan.WQBClient.from_config", side_effect=from_config):
            first = asyncio.run(run_scan(str(config), reconcile_only=True))
            second = asyncio.run(run_scan(str(config), reconcile_only=True))

        assert first is not None and first.inspected == 0
        assert second is not None and second.inspected == 0
        assert len(run_ids) == 2
        assert run_ids[0] == run_ids[1]
        assert run_ids[0].startswith("standalone-scan-")
        assert not (root / "results.json").exists()
