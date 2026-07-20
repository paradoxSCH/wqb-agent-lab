from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from wqb_agent_lab.workflow import (
    StageCheckpointStore,
    StageInterruptionRequiresReconciliation,
    StageOutcome,
    StageResult,
    StageRunner,
)


class WorkflowStageTests(unittest.TestCase):
    def test_stage_result_preserves_open_research_output_immutably(self) -> None:
        output = {
            "unknown_mechanism": "attention migration across analyst cohorts",
            "expression": "future_operator(vector_neutralize(x, custom_group))",
            "requested_action": {"kind": "future_research_lane"},
        }
        result = StageResult.create(
            run_id="run-open",
            stage_id="llm_planning",
            attempt_id="attempt-1",
            attempt_number=1,
            status="completed",
            started_at="2026-07-20T12:00:00",
            completed_at="2026-07-20T12:00:01",
            output=output,
        )
        output["requested_action"]["kind"] = "mutated"

        self.assertEqual(
            "future_research_lane",
            result.to_dict()["output"]["requested_action"]["kind"],
        )
        result.validate()

    def test_orchestrator_lifecycle_fields_are_consistent(self) -> None:
        with self.assertRaisesRegex(ValueError, "workflow_stage_result"):
            StageResult.create(
                run_id="run-invalid-state",
                stage_id="llm_planning",
                attempt_id="attempt-1",
                attempt_number=1,
                status="running",
                started_at="2026-07-20T12:00:00",
                completed_at="2026-07-20T12:00:01",
            )

    def test_safe_stage_resumes_an_interrupted_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StageCheckpointStore(Path(tmp))
            interrupted = StageResult.create(
                run_id="run-resume",
                stage_id="llm_planning",
                attempt_id="interrupted-attempt",
                attempt_number=1,
                status="running",
                started_at="2026-07-20T12:00:00",
                completed_at=None,
            )
            store.write(interrupted)
            runner = StageRunner(
                store,
                clock=lambda: datetime(2026, 7, 20, 12, 1),
                attempt_ids=lambda: "resumed-attempt",
            )

            result = runner.run(
                run_id="run-resume",
                stage_id="llm_planning",
                input_digest="prompt-digest",
                replay_policy="safe",
                execute=lambda: StageOutcome.create(
                    artifacts=("runs/plan.json",),
                    output={"proposal_contract": "open"},
                ),
            )

            self.assertEqual("completed", result.status)
            self.assertEqual(2, result.attempt_number)
            self.assertEqual("interrupted-attempt", result.extensions["resumed_from_attempt_id"])
            self.assertEqual(result.to_dict(), store.load("llm_planning").to_dict())

    def test_non_replay_safe_stage_requires_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StageCheckpointStore(Path(tmp))
            store.write(
                StageResult.create(
                    run_id="run-side-effect",
                    stage_id="simulation",
                    attempt_id="unknown-commit",
                    attempt_number=1,
                    status="running",
                    started_at="2026-07-20T12:00:00",
                    completed_at=None,
                )
            )
            called = False

            def execute() -> StageOutcome:
                nonlocal called
                called = True
                return StageOutcome.create()

            with self.assertRaisesRegex(StageInterruptionRequiresReconciliation, "unknown-commit"):
                StageRunner(store).run(
                    run_id="run-side-effect",
                    stage_id="simulation",
                    input_digest="request-digest",
                    replay_policy="reconcile",
                    execute=execute,
                )

            self.assertFalse(called)

    def test_failure_checkpoint_does_not_replace_original_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StageCheckpointStore(Path(tmp))
            runner = StageRunner(
                store,
                clock=lambda: datetime(2026, 7, 20, 12, 1),
                attempt_ids=lambda: "failed-attempt",
            )

            def fail() -> StageOutcome:
                raise LookupError("original failure")

            with self.assertRaisesRegex(LookupError, "original failure"):
                runner.run(
                    run_id="run-failed",
                    stage_id="llm_planning",
                    input_digest="prompt-digest",
                    replay_policy="safe",
                    execute=fail,
                )

            checkpoint = store.load("llm_planning")
            assert checkpoint is not None
            self.assertEqual("failed", checkpoint.status)
            self.assertEqual("LookupError", checkpoint.error.error_type if checkpoint.error else "")

    def test_invalid_executor_result_is_recorded_as_a_stage_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StageCheckpointStore(Path(tmp))
            runner = StageRunner(
                store,
                clock=lambda: datetime(2026, 7, 20, 12, 1),
                attempt_ids=lambda: "invalid-outcome",
            )

            with self.assertRaisesRegex(TypeError, "StageOutcome"):
                runner.run(
                    run_id="run-invalid-outcome",
                    stage_id="llm_planning",
                    input_digest="prompt-digest",
                    replay_policy="safe",
                    execute=lambda: None,  # type: ignore[arg-type,return-value]
                )

            checkpoint = store.load("llm_planning")
            assert checkpoint is not None
            self.assertEqual("failed", checkpoint.status)
            self.assertEqual("TypeError", checkpoint.error.error_type if checkpoint.error else "")

    def test_stage_id_is_an_orchestrator_identifier_not_an_llm_action_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StageCheckpointStore(Path(tmp))

            with self.assertRaisesRegex(ValueError, "orchestration stage id"):
                store.path_for("../../arbitrary-action")


if __name__ == "__main__":
    unittest.main()
