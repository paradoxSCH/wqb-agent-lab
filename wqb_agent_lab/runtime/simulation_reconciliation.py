from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any, Protocol

from wqb_agent_lab.runtime.atomic_json import atomic_write_json

from .operations import OperationJournal, OperationRecord, payload_fingerprint


class SimulationEvidenceClient(Protocol):
    def get_user_alphas(self, **params: Any) -> dict[str, Any]: ...

    def poll_simulation(self, location: str) -> dict[str, Any]: ...

    def get_alpha(self, alpha_id: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class SimulationResultBinding:
    output_path: Path
    expression: str
    settings: dict[str, Any]
    note: str = ""

    @property
    def request_payload(self) -> dict[str, Any]:
        return {
            "type": "REGULAR",
            "settings": dict(self.settings),
            "regular": self.expression,
        }

    @property
    def fingerprint(self) -> str:
        return payload_fingerprint(self.request_payload)


@dataclass(frozen=True, slots=True)
class SimulationReconciliationReport:
    inspected: int
    recovered: int
    deferred: int
    manual_review: int
    operation_ids: tuple[str, ...]

    @property
    def unresolved(self) -> int:
        return self.deferred + self.manual_review

    def to_dict(self) -> dict[str, Any]:
        return {
            "inspected": self.inspected,
            "recovered": self.recovered,
            "deferred": self.deferred,
            "manual_review": self.manual_review,
            "unresolved": self.unresolved,
            "operation_ids": list(self.operation_ids),
        }


class SimulationReconciler:
    """Recover local simulation results without replaying an ambiguous POST.

    A positive match is required to resolve an operation as accepted. Absence from a
    recent-alpha listing is not proof that the platform rejected the request, so the
    operation is retried as a read-only observation and eventually routed to manual
    review instead of issuing another simulation.
    """

    def __init__(
        self,
        journal: OperationJournal,
        client: SimulationEvidenceClient,
        *,
        run_id: str,
        max_attempts: int = 3,
        retry_after_seconds: int = 300,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.journal = journal
        self.client = client
        self.run_id = run_id
        self.max_attempts = max(1, int(max_attempts))
        self.retry_after_seconds = max(0, int(retry_after_seconds))
        self.clock = clock

    def reconcile(
        self,
        bindings: list[SimulationResultBinding],
    ) -> SimulationReconciliationReport:
        by_fingerprint = {binding.fingerprint: binding for binding in bindings}
        records = self.journal.records(
            "simulation.create",
            run_id=self.run_id,
            outcomes=(
                "started",
                "unknown_commit",
                "accepted",
                "reconciliation_pending",
                "manual_review",
            ),
        )
        candidates = [record for record in records if record.fingerprint in by_fingerprint]
        if not candidates:
            return SimulationReconciliationReport(0, 0, 0, 0, ())

        recent_alphas: list[dict[str, Any]] | None = None
        recovered = 0
        deferred = 0
        manual_review = 0
        operation_ids: list[str] = []
        for record in candidates:
            binding = by_fingerprint[record.fingerprint]
            if _binding_has_final_row(binding):
                continue
            operation_ids.append(record.operation_id)
            if record.outcome == "manual_review":
                _write_pending_row(binding, record, "manual_review")
                manual_review += 1
                continue
            if not _retry_due(record, self.clock()):
                _write_pending_row(binding, record, "reconciliation_deferred")
                deferred += 1
                continue

            alpha_id = ""
            evidence: dict[str, Any] = {}
            if record.remote_ref and "/simulations/" in record.remote_ref:
                evidence = self.client.poll_simulation(record.remote_ref)
                alpha_id = str(evidence.get("alpha") or "")
            if not alpha_id:
                if recent_alphas is None:
                    payload = self.client.get_user_alphas(limit=100, offset=0, order="-dateCreated")
                    rows = payload.get("results") if isinstance(payload, dict) else []
                    recent_alphas = [row for row in (rows or []) if isinstance(row, dict)]
                matches = [
                    alpha
                    for alpha in recent_alphas
                    if _remote_alpha_matches(record, binding, alpha)
                ]
                if len(matches) == 1:
                    evidence = matches[0]
                    alpha_id = str(evidence.get("id") or evidence.get("alpha_id") or "")
                elif len(matches) > 1:
                    updated = self.journal.record_reconciliation_attempt(
                        record.operation_id,
                        reason="multiple_matching_alphas_require_manual_review",
                        retry_after_seconds=self.retry_after_seconds,
                        max_attempts=1,
                        now=self.clock(),
                    )
                    _write_pending_row(binding, updated, "manual_review")
                    manual_review += 1
                    continue

            if alpha_id:
                detail = self.client.get_alpha(alpha_id)
                _write_recovered_row(binding, alpha_id, detail, evidence, record.operation_id)
                self.journal.finish(
                    record.operation_id,
                    "accepted",
                    reason=f"{record.reason};reconciled_alpha_match".strip(";"),
                    remote_ref=f"/alphas/{alpha_id}",
                )
                recovered += 1
                continue

            updated = self.journal.record_reconciliation_attempt(
                record.operation_id,
                reason="no_positive_remote_match",
                retry_after_seconds=self.retry_after_seconds,
                max_attempts=self.max_attempts,
                now=self.clock(),
            )
            state = "manual_review" if updated.outcome == "manual_review" else "reconciliation_deferred"
            _write_pending_row(binding, updated, state)
            if updated.outcome == "manual_review":
                manual_review += 1
            else:
                deferred += 1

        return SimulationReconciliationReport(
            inspected=len(candidates),
            recovered=recovered,
            deferred=deferred,
            manual_review=manual_review,
            operation_ids=tuple(operation_ids),
        )


def _retry_due(record: OperationRecord, now: datetime) -> bool:
    if not record.next_reconcile_at:
        return True
    next_at = _parse_datetime(record.next_reconcile_at)
    observed_at = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return next_at is None or observed_at >= next_at


def _remote_alpha_matches(
    record: OperationRecord,
    binding: SimulationResultBinding,
    alpha: dict[str, Any],
) -> bool:
    regular = alpha.get("regular")
    remote_expression = (
        regular.get("code") if isinstance(regular, dict) else alpha.get("expression")
    )
    if _normalize_expression(str(remote_expression or "")) != _normalize_expression(binding.expression):
        return False
    remote_settings = alpha.get("settings")
    if not isinstance(remote_settings, dict):
        return False
    if any(remote_settings.get(key) != value for key, value in binding.settings.items()):
        return False
    created_at = _parse_datetime(str(alpha.get("dateCreated") or alpha.get("created_at") or ""))
    operation_at = _parse_datetime(record.created_at)
    if created_at is None or operation_at is None:
        return False
    if not operation_at - timedelta(minutes=2) <= created_at <= operation_at + timedelta(hours=2):
        return False
    return bool(alpha.get("id") or alpha.get("alpha_id"))


def _normalize_expression(expression: str) -> str:
    return re.sub(r"\s+", "", expression)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _row_identity(row: dict[str, Any]) -> tuple[str, str]:
    return (
        _normalize_expression(str(row.get("expression") or "")),
        json.dumps(row.get("settings") or {}, ensure_ascii=False, sort_keys=True),
    )


def _binding_identity(binding: SimulationResultBinding) -> tuple[str, str]:
    return (
        _normalize_expression(binding.expression),
        json.dumps(binding.settings, ensure_ascii=False, sort_keys=True),
    )


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _binding_has_final_row(binding: SimulationResultBinding) -> bool:
    identity = _binding_identity(binding)
    for row in _read_rows(binding.output_path):
        if _row_identity(row) != identity:
            continue
        diagnosis = row.get("diagnosis")
        state = diagnosis.get("diagnosis_type") if isinstance(diagnosis, dict) else ""
        if state not in {
            "simulation_unknown_commit",
            "simulation_reconciliation_pending",
            "simulation_reconciliation_manual_review",
        }:
            return True
    return False


def _replace_binding_row(binding: SimulationResultBinding, replacement: dict[str, Any]) -> None:
    identity = _binding_identity(binding)
    rows = [row for row in _read_rows(binding.output_path) if _row_identity(row) != identity]
    rows.append(replacement)
    binding.output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(binding.output_path, rows)


def _write_pending_row(
    binding: SimulationResultBinding,
    record: OperationRecord,
    state: str,
) -> None:
    diagnosis_type = (
        "simulation_reconciliation_manual_review"
        if state == "manual_review"
        else "simulation_reconciliation_pending"
    )
    _replace_binding_row(
        binding,
        {
            "expression": binding.expression,
            "settings": binding.settings,
            "note": binding.note,
            "error": "Simulation outcome requires reconciliation; the request was not replayed.",
            "diagnosis": {
                "diagnosis_type": diagnosis_type,
                "operation_id": record.operation_id,
                "operation_fingerprint": record.fingerprint,
                "reconcile_attempts": record.reconcile_attempts,
                "next_reconcile_at": record.next_reconcile_at,
            },
        },
    )


def _write_recovered_row(
    binding: SimulationResultBinding,
    alpha_id: str,
    detail: Any,
    evidence: dict[str, Any],
    operation_id: str,
) -> None:
    metrics = dict(getattr(detail, "metrics", {}) or {})
    checks = [
        check.to_dict() if hasattr(check, "to_dict") else dict(check)
        for check in (getattr(detail, "checks", []) or [])
    ]
    if not metrics and isinstance(evidence.get("is"), dict):
        metrics = dict(evidence["is"])
    _replace_binding_row(
        binding,
        {
            "alpha_id": alpha_id,
            "expression": binding.expression,
            "settings": binding.settings,
            "note": binding.note,
            "metrics": metrics,
            "checks": checks,
            "reconciliation": {
                "status": "recovered",
                "operation_id": operation_id,
                "evidence": "platform_alpha_match",
            },
        },
    )
