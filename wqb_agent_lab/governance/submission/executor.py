from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from wqb_agent_lab.governance.side_effects import evaluate_side_effect_capability

from .ledger import SubmissionGovernanceLedger
from .models import PolicyEvaluation, SubmitDecision, SubmissionAuditEvent


BACKLOG_FILE = "submission_backlog.json"


class SubmissionExecutor:
    def __init__(self, run_dir: Path | str, *, env: Mapping[str, str] | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.ledger = SubmissionGovernanceLedger(self.run_dir)
        self.env = env

    def execute(self, decision: SubmitDecision, evaluation: PolicyEvaluation) -> dict[str, Any]:
        if decision.decision_id in self.ledger.decision_ids():
            event = self._audit("duplicate_decision", decision, "duplicate_decision", "Decision already processed.")
            return {"executed": False, "queued": False, "status": "duplicate_decision", "audit": event}

        self.ledger.append_decision(decision.to_dict())
        self.ledger.append_evaluation(evaluation.to_dict())

        if not evaluation.allowed:
            event = self._audit("policy_blocked", decision, "policy_blocked", "Policy evaluation blocked execution.")
            return {"executed": False, "queued": False, "status": "policy_blocked", "audit": event}

        if decision.requested_mode == "queue_only":
            self._append_backlog(decision)
            event = self._audit("queued", decision, "queued", "Submission intent queued for worker execution.")
            return {"executed": False, "queued": True, "status": "queued", "audit": event}

        capability = evaluate_side_effect_capability("submission", env=self.env)
        if not capability.enabled:
            event = self._audit("capability_disabled", decision, "capability_disabled", "Live submit capability disabled.")
            return {
                "executed": False,
                "queued": False,
                "status": "capability_disabled",
                "capability": capability.to_dict(),
                "audit": event,
            }

        self._append_backlog(decision)
        event = self._audit("live_dispatch_queued", decision, "live_dispatch_queued", "Live-capable intent queued for worker execution.")
        return {"executed": True, "queued": True, "status": "live_dispatch_queued", "audit": event}

    def _append_backlog(self, decision: SubmitDecision) -> None:
        path = self.run_dir / BACKLOG_FILE
        if path.exists():
            try:
                rows = json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                rows = []
        else:
            rows = []
        if not isinstance(rows, list):
            rows = []
        if any(isinstance(row, dict) and row.get("decision_id") == decision.decision_id for row in rows):
            return
        rows.append(
            {
                "decision_id": decision.decision_id,
                "alpha_id": decision.alpha_id,
                "recommended_action": "live_recheck_then_submit" if decision.requested_mode == "execute_live" else "submit",
                "requires_live_recheck": decision.requested_mode == "execute_live",
                "source": "submission_governance",
                "score": decision.metadata.get("score"),
            }
        )
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _audit(self, event_type: str, decision: SubmitDecision, status: str, message: str) -> dict[str, Any]:
        event = SubmissionAuditEvent(
            event_type=event_type,
            decision_id=decision.decision_id,
            alpha_id=decision.alpha_id,
            status=status,
            message=message,
            payload={"requested_mode": decision.requested_mode, "agent_id": decision.agent_id},
        ).to_dict()
        self.ledger.append_audit(event)
        return event
