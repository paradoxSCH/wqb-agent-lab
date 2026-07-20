from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import requests


OperationOutcome = Literal[
    "started",
    "accepted",
    "rejected",
    "not_accepted_retryable",
    "not_sent_retryable",
    "unknown_commit",
    "reconciliation_pending",
    "manual_review",
]


def payload_fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def classify_transport_exception(exc: BaseException) -> tuple[OperationOutcome, str]:
    if isinstance(exc, requests.ConnectTimeout):
        return "not_sent_retryable", "connect_timeout"
    if isinstance(exc, requests.ReadTimeout):
        return "unknown_commit", "read_timeout_after_send"
    if isinstance(exc, requests.ConnectionError):
        return "unknown_commit", "connection_lost_after_possible_send"
    if isinstance(exc, requests.Timeout):
        return "unknown_commit", "unclassified_timeout"
    return "unknown_commit", "transport_exception"


@dataclass(frozen=True, slots=True)
class OperationRecord:
    operation_id: str
    operation_type: str
    fingerprint: str
    outcome: str
    reason: str
    run_id: str
    status_code: int | None
    remote_ref: str
    payload: Any
    created_at: str
    updated_at: str
    reconcile_attempts: int
    next_reconcile_at: str
    reconciliation_reason: str


class SideEffectUncertainError(RuntimeError):
    def __init__(self, record: OperationRecord, cause: BaseException | None = None) -> None:
        super().__init__(f"{record.operation_type} outcome is uncertain: {record.reason} ({record.operation_id})")
        self.record = record
        self.__cause__ = cause


class OperationJournal:
    """Durable side-effect journal and transactional outbox.

    SQLite gives the local single-user runtime one commit boundary for operation state
    and follow-up events. Consumers claim events idempotently and can replay them after
    an interrupted process.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        with closing(self.connect()) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    operation_type TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    status_code INTEGER,
                    remote_ref TEXT NOT NULL DEFAULT '',
                    reconcile_attempts INTEGER NOT NULL DEFAULT 0,
                    next_reconcile_at TEXT NOT NULL DEFAULT '',
                    reconciliation_reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS operations_lookup
                    ON operations(operation_type, fingerprint, outcome);
                CREATE TABLE IF NOT EXISTS outbox (
                    event_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                );
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(operations)").fetchall()
            }
            if "reconcile_attempts" not in columns:
                conn.execute(
                    "ALTER TABLE operations ADD COLUMN reconcile_attempts INTEGER NOT NULL DEFAULT 0"
                )
            if "next_reconcile_at" not in columns:
                conn.execute(
                    "ALTER TABLE operations ADD COLUMN next_reconcile_at TEXT NOT NULL DEFAULT ''"
                )
            if "reconciliation_reason" not in columns:
                conn.execute(
                    "ALTER TABLE operations ADD COLUMN reconciliation_reason TEXT NOT NULL DEFAULT ''"
                )

    def begin(self, operation_type: str, payload: Any, *, run_id: str = "") -> OperationRecord:
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        operation_id = uuid.uuid4().hex
        fingerprint = payload_fingerprint(payload)
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """INSERT INTO operations
                (operation_id, operation_type, fingerprint, run_id, payload_json,
                 outcome, reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'started', '', ?, ?)""",
                (
                    operation_id,
                    operation_type,
                    fingerprint,
                    run_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
        return self.get(operation_id)

    def finish(
        self,
        operation_id: str,
        outcome: OperationOutcome,
        *,
        reason: str = "",
        status_code: int | None = None,
        remote_ref: str = "",
        outbox_event: tuple[str, str, dict[str, Any]] | None = None,
    ) -> OperationRecord:
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """UPDATE operations SET outcome=?, reason=?, status_code=?, remote_ref=?, updated_at=?
                WHERE operation_id=?""",
                (outcome, reason, status_code, remote_ref, now, operation_id),
            )
            if outbox_event is not None:
                event_type, idempotency_key, payload = outbox_event
                conn.execute(
                    """INSERT OR IGNORE INTO outbox
                    (event_id, idempotency_key, event_type, payload_json, status, created_at)
                    VALUES (?, ?, ?, ?, 'pending', ?)""",
                    (
                        uuid.uuid4().hex,
                        idempotency_key,
                        event_type,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
        return self.get(operation_id)

    def get(self, operation_id: str) -> OperationRecord:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return self._record(row)

    def unresolved(self, operation_type: str | None = None) -> list[OperationRecord]:
        query = (
            "SELECT * FROM operations "
            "WHERE outcome IN ('started', 'unknown_commit', 'reconciliation_pending')"
        )
        params: tuple[Any, ...] = ()
        if operation_type:
            query += " AND operation_type=?"
            params = (operation_type,)
        query += " ORDER BY created_at"
        with closing(self.connect()) as conn:
            return [self._record(row) for row in conn.execute(query, params).fetchall()]

    def records(
        self,
        operation_type: str | None = None,
        *,
        run_id: str | None = None,
        outcomes: tuple[str, ...] | None = None,
    ) -> list[OperationRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if operation_type:
            clauses.append("operation_type=?")
            params.append(operation_type)
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(run_id)
        if outcomes:
            clauses.append(f"outcome IN ({','.join('?' for _ in outcomes)})")
            params.extend(outcomes)
        query = "SELECT * FROM operations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at"
        with closing(self.connect()) as conn:
            return [self._record(row) for row in conn.execute(query, tuple(params)).fetchall()]

    def record_reconciliation_attempt(
        self,
        operation_id: str,
        *,
        reason: str,
        retry_after_seconds: int,
        max_attempts: int,
        now: datetime | None = None,
    ) -> OperationRecord:
        observed_at = now or datetime.now(timezone.utc)
        next_reconcile_at = (
            observed_at + timedelta(seconds=max(0, int(retry_after_seconds)))
        ).isoformat(timespec="seconds")
        updated_at = observed_at.isoformat(timespec="milliseconds")
        with closing(self.connect()) as conn, conn:
            row = conn.execute(
                "SELECT reconcile_attempts FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(operation_id)
            attempts = int(row["reconcile_attempts"] or 0) + 1
            outcome = (
                "manual_review"
                if attempts >= max(1, int(max_attempts))
                else "reconciliation_pending"
            )
            conn.execute(
                """UPDATE operations
                SET outcome=?, reconciliation_reason=?, reconcile_attempts=?, next_reconcile_at=?, updated_at=?
                WHERE operation_id=?""",
                (outcome, reason, attempts, next_reconcile_at, updated_at, operation_id),
            )
        return self.get(operation_id)

    def enqueue(self, event_type: str, payload: dict[str, Any], *, idempotency_key: str) -> str:
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        event_id = uuid.uuid4().hex
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """INSERT OR IGNORE INTO outbox
                (event_id, idempotency_key, event_type, payload_json, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)""",
                (event_id, idempotency_key, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), now),
            )
            row = conn.execute("SELECT event_id FROM outbox WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        assert row is not None
        return str(row["event_id"])

    def pending_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM outbox WHERE status IN ('pending', 'failed') ORDER BY created_at LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [
            {
                "event_id": str(row["event_id"]),
                "idempotency_key": str(row["idempotency_key"]),
                "event_type": str(row["event_type"]),
                "payload": json.loads(row["payload_json"]),
                "attempts": int(row["attempts"]),
            }
            for row in rows
        ]

    def mark_delivered(self, event_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "UPDATE outbox SET status='delivered', delivered_at=?, attempts=attempts+1, last_error='' WHERE event_id=?",
                (now, event_id),
            )

    def mark_failed(self, event_id: str, error: str) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "UPDATE outbox SET status='failed', attempts=attempts+1, last_error=? WHERE event_id=?",
                (error[:500], event_id),
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> OperationRecord:
        return OperationRecord(
            operation_id=str(row["operation_id"]),
            operation_type=str(row["operation_type"]),
            fingerprint=str(row["fingerprint"]),
            outcome=str(row["outcome"]),
            reason=str(row["reason"]),
            run_id=str(row["run_id"]),
            status_code=row["status_code"],
            remote_ref=str(row["remote_ref"]),
            payload=json.loads(str(row["payload_json"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            reconcile_attempts=int(row["reconcile_attempts"] or 0),
            next_reconcile_at=str(row["next_reconcile_at"] or ""),
            reconciliation_reason=str(row["reconciliation_reason"] or ""),
        )
