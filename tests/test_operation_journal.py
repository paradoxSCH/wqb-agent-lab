from __future__ import annotations

import tempfile
from pathlib import Path

import requests

from wqb_agent_lab.runtime import OperationJournal, classify_transport_exception, payload_fingerprint


def test_classifies_connect_and_read_timeouts_differently() -> None:
    assert classify_transport_exception(requests.ConnectTimeout()) == ("not_sent_retryable", "connect_timeout")
    assert classify_transport_exception(requests.ReadTimeout()) == ("unknown_commit", "read_timeout_after_send")


def test_journal_records_unknown_commit_and_idempotent_outbox() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        journal = OperationJournal(Path(tmp) / "operations.db")
        started = journal.begin("simulation.create", {"regular": "rank(close)"}, run_id="run-1")
        record = journal.finish(started.operation_id, "unknown_commit", reason="read_timeout_after_send")
        assert record.fingerprint == payload_fingerprint({"regular": "rank(close)"})
        assert journal.unresolved("simulation.create") == [record]

        first = journal.enqueue("stage.completed", {"stage": "probe"}, idempotency_key="run-1:probe")
        second = journal.enqueue("stage.completed", {"stage": "probe"}, idempotency_key="run-1:probe")
        assert first == second
        event = journal.pending_events()[0]
        journal.mark_delivered(event["event_id"])
        assert journal.pending_events() == []
