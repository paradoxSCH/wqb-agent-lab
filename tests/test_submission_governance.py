from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class SubmissionGovernanceTests(unittest.TestCase):
    def test_policy_evaluation_accepts_complete_submit_decision_without_quality_thresholds(self) -> None:
        from wqb_agent_lab.governance.submission import SubmitDecision, SubmissionPolicyEvaluator

        decision = SubmitDecision(
            decision_id="dec-001",
            alpha_id="alpha-001",
            requested_mode="queue_only",
            agent_id="agent-main",
            rationale="Candidate passed upstream policy and should enter submission executor.",
        )

        evaluation = SubmissionPolicyEvaluator().evaluate(decision)

        self.assertTrue(evaluation.allowed)
        self.assertEqual(evaluation.policy_action, "allow")
        self.assertEqual(evaluation.decision_id, "dec-001")
        self.assertNotIn("sharpe", json.dumps(evaluation.to_dict()).lower())

    def test_policy_evaluation_blocks_incomplete_decision(self) -> None:
        from wqb_agent_lab.governance.submission import SubmitDecision, SubmissionPolicyEvaluator

        decision = SubmitDecision(
            decision_id="dec-002",
            alpha_id="",
            requested_mode="execute_live",
            agent_id="agent-main",
            rationale="",
        )

        evaluation = SubmissionPolicyEvaluator().evaluate(decision)

        self.assertFalse(evaluation.allowed)
        self.assertEqual(evaluation.policy_action, "block")
        self.assertIn("alpha_id_required", evaluation.reasons)
        self.assertIn("rationale_required", evaluation.reasons)

    def test_executor_records_audit_and_refuses_live_when_capability_disabled(self) -> None:
        from wqb_agent_lab.governance.submission import SubmitDecision, SubmissionExecutor, SubmissionPolicyEvaluator

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            decision = SubmitDecision(
                decision_id="dec-003",
                alpha_id="alpha-003",
                requested_mode="execute_live",
                agent_id="agent-main",
                rationale="Agent intentionally requests live execution.",
            )
            evaluation = SubmissionPolicyEvaluator().evaluate(decision)

            result = SubmissionExecutor(run_dir, env={}).execute(decision, evaluation)

            self.assertFalse(result["executed"])
            self.assertEqual(result["status"], "capability_disabled")
            audit_lines = (run_dir / "submission_governance_audit.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(audit_lines))
            self.assertEqual("capability_disabled", json.loads(audit_lines[0])["event_type"])

    def test_queue_only_execution_writes_decision_and_backlog(self) -> None:
        from wqb_agent_lab.governance.submission import SubmitDecision, SubmissionExecutor, SubmissionPolicyEvaluator

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            decision = SubmitDecision(
                decision_id="dec-004",
                alpha_id="alpha-004",
                requested_mode="queue_only",
                agent_id="agent-main",
                rationale="Queue for independent worker execution.",
                metadata={"score": 4.2},
            )
            evaluation = SubmissionPolicyEvaluator().evaluate(decision)

            result = SubmissionExecutor(run_dir, env={}).execute(decision, evaluation)

            self.assertTrue(result["queued"])
            self.assertEqual(result["status"], "queued")
            backlog = json.loads((run_dir / "submission_backlog.json").read_text(encoding="utf-8"))
            self.assertEqual("alpha-004", backlog[0]["alpha_id"])
            self.assertEqual("dec-004", backlog[0]["decision_id"])
            decisions = (run_dir / "submission_decisions.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual("dec-004", json.loads(decisions[0])["decision_id"])

    def test_executor_is_idempotent_by_decision_id(self) -> None:
        from wqb_agent_lab.governance.submission import SubmitDecision, SubmissionExecutor, SubmissionPolicyEvaluator

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            decision = SubmitDecision(
                decision_id="dec-005",
                alpha_id="alpha-005",
                requested_mode="queue_only",
                agent_id="agent-main",
                rationale="Queue once.",
            )
            evaluation = SubmissionPolicyEvaluator().evaluate(decision)
            executor = SubmissionExecutor(run_dir, env={})

            first = executor.execute(decision, evaluation)
            second = executor.execute(decision, evaluation)

            self.assertEqual("queued", first["status"])
            self.assertEqual("duplicate_decision", second["status"])
            backlog = json.loads((run_dir / "submission_backlog.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(backlog))

    def test_audit_tail_returns_recent_events(self) -> None:
        from wqb_agent_lab.governance.submission import SubmissionGovernanceLedger

        with tempfile.TemporaryDirectory() as tmp:
            ledger = SubmissionGovernanceLedger(Path(tmp))
            ledger.append_audit({"event_type": "one", "decision_id": "d1"})
            ledger.append_audit({"event_type": "two", "decision_id": "d2"})
            ledger.append_audit({"event_type": "three", "decision_id": "d3"})

            tail = ledger.audit_tail(limit=2)

            self.assertEqual(["two", "three"], [event["event_type"] for event in tail])


if __name__ == "__main__":
    unittest.main()
