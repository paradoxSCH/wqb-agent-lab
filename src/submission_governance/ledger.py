from __future__ import annotations

import json
from pathlib import Path
from typing import Any


AUDIT_FILE = "submission_governance_audit.jsonl"
DECISIONS_FILE = "submission_decisions.jsonl"
EVALUATIONS_FILE = "submission_policy_evaluations.jsonl"


class SubmissionGovernanceLedger:
    def __init__(self, run_dir: Path | str) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @property
    def audit_path(self) -> Path:
        return self.run_dir / AUDIT_FILE

    @property
    def decisions_path(self) -> Path:
        return self.run_dir / DECISIONS_FILE

    @property
    def evaluations_path(self) -> Path:
        return self.run_dir / EVALUATIONS_FILE

    def append_decision(self, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.decisions_path, payload)

    def append_evaluation(self, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.evaluations_path, payload)

    def append_audit(self, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.audit_path, payload)

    def decision_ids(self) -> set[str]:
        return {str(row.get("decision_id")) for row in self._read_jsonl(self.decisions_path) if row.get("decision_id")}

    def audit_tail(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._read_jsonl(self.audit_path)
        return rows[-max(0, int(limit)) :]

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows
