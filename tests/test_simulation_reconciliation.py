from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wqb_agent_lab.platform.models import WQBAlphaDetail
from wqb_agent_lab.runtime import (
    OperationJournal,
    SimulationReconciler,
    SimulationResultBinding,
)


class EvidenceClient:
    def __init__(
        self,
        *,
        alphas: list[dict[str, Any]] | None = None,
        polls: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.alphas = list(alphas or [])
        self.polls = dict(polls or {})
        self.list_calls = 0
        self.poll_calls: list[str] = []
        self.detail_calls: list[str] = []

    def get_user_alphas(self, **_params: Any) -> dict[str, Any]:
        self.list_calls += 1
        return {"results": self.alphas}

    def poll_simulation(self, location: str) -> dict[str, Any]:
        self.poll_calls.append(location)
        return self.polls.get(location, {})

    def get_alpha(self, alpha_id: str) -> WQBAlphaDetail:
        self.detail_calls.append(alpha_id)
        payload = next((row for row in self.alphas if row.get("id") == alpha_id), None)
        payload = payload or {
            "id": alpha_id,
            "regular": {"code": "future_operator(custom_field)"},
            "is": {"sharpe": 1.4, "fitness": 1.1, "turnover": 0.2},
        }
        return WQBAlphaDetail.from_payload(payload, http_status=200)


def alpha_payload(alpha_id: str, expression: str, settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": alpha_id,
        "dateCreated": datetime.now(timezone.utc).isoformat(),
        "regular": {"code": expression},
        "settings": settings,
        "is": {"sharpe": 1.4, "fitness": 1.1, "turnover": 0.2},
    }


def binding(root: Path, expression: str, settings: dict[str, Any]) -> SimulationResultBinding:
    return SimulationResultBinding(
        output_path=root / "simulation_results.json",
        expression=expression,
        settings=settings,
        note="novel mechanism remains intact",
    )


def test_reconciles_unknown_commit_from_positive_alpha_evidence() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        expression = "future_operator(custom_field)"
        settings = {"region": "USA", "delay": 1, "novelSetting": "OPEN"}
        target = binding(root, expression, settings)
        journal = OperationJournal(root / "operations.db")
        started = journal.begin("simulation.create", target.request_payload, run_id="run-open")
        journal.finish(started.operation_id, "unknown_commit", reason="read_timeout_after_send")
        client = EvidenceClient(alphas=[alpha_payload("A-RECOVERED", expression, settings)])

        report = SimulationReconciler(journal, client, run_id="run-open").reconcile([target])

        assert report.recovered == 1
        assert report.unresolved == 0
        record = journal.get(started.operation_id)
        assert record.outcome == "accepted"
        assert record.remote_ref == "/alphas/A-RECOVERED"
        rows = json.loads(target.output_path.read_text(encoding="utf-8"))
        assert rows[0]["alpha_id"] == "A-RECOVERED"
        assert rows[0]["expression"] == expression
        assert rows[0]["settings"]["novelSetting"] == "OPEN"


def test_accepted_location_is_polled_before_recent_alpha_search() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = binding(root, "future_operator(custom_field)", {"region": "USA"})
        journal = OperationJournal(root / "operations.db")
        started = journal.begin("simulation.create", target.request_payload, run_id="run-location")
        journal.finish(
            started.operation_id,
            "accepted",
            reason="location_received",
            remote_ref="/simulations/S1",
        )
        client = EvidenceClient(polls={"/simulations/S1": {"alpha": "A1", "status": "COMPLETE"}})

        report = SimulationReconciler(journal, client, run_id="run-location").reconcile([target])

        assert report.recovered == 1
        assert client.poll_calls == ["/simulations/S1"]
        assert client.list_calls == 0


def test_hard_crash_started_record_is_not_replayed_and_eventually_requires_review() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = binding(root, "unknown_mechanism(field_x)", {"delay": 1})
        journal = OperationJournal(root / "operations.db")
        started = journal.begin("simulation.create", target.request_payload, run_id="run-crash")
        client = EvidenceClient()

        first = SimulationReconciler(
            journal,
            client,
            run_id="run-crash",
            max_attempts=2,
            retry_after_seconds=0,
            clock=lambda: datetime(2026, 7, 20, 12, 0),
        ).reconcile([target])
        second = SimulationReconciler(
            journal,
            client,
            run_id="run-crash",
            max_attempts=2,
            retry_after_seconds=0,
            clock=lambda: datetime(2026, 7, 20, 12, 1),
        ).reconcile([target])

        assert first.deferred == 1
        assert second.manual_review == 1
        record = journal.get(started.operation_id)
        assert record.outcome == "manual_review"
        assert record.reconcile_attempts == 2
        rows = json.loads(target.output_path.read_text(encoding="utf-8"))
        assert rows[0]["diagnosis"]["diagnosis_type"] == "simulation_reconciliation_manual_review"
        assert "alpha_id" not in rows[0]


def test_does_not_match_same_expression_when_requested_settings_differ() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        expression = "rank(close)"
        target = binding(root, expression, {"region": "USA", "delay": 1})
        journal = OperationJournal(root / "operations.db")
        started = journal.begin("simulation.create", target.request_payload, run_id="run-settings")
        journal.finish(started.operation_id, "unknown_commit", reason="connection_lost")
        client = EvidenceClient(
            alphas=[alpha_payload("A-WRONG", expression, {"region": "USA", "delay": 0})]
        )

        report = SimulationReconciler(
            journal,
            client,
            run_id="run-settings",
            retry_after_seconds=0,
        ).reconcile([target])

        assert report.recovered == 0
        assert report.deferred == 1
        assert journal.get(started.operation_id).outcome == "reconciliation_pending"
