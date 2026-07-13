from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluation_worker import EvaluationWorker, evaluation_state_path
from src.workflow_daemon import CompletionHookResult


class EvaluationWorkerTests(unittest.TestCase):
    def test_run_once_writes_state_from_completion_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: list[Path] = []

            def fake_hook(path: Path) -> CompletionHookResult:
                calls.append(path)
                return CompletionHookResult(
                    evaluation_ran=True,
                    run_tag="daily-run",
                    report_path=".local/data/evaluations/latest-ablation-suite/ablation_report.json",
                    message="evaluation complete",
                )

            worker = EvaluationWorker(root, hook_runner=fake_hook)
            result = worker.run_once()

            self.assertEqual(result["status"], "evaluation_complete")
            self.assertEqual(calls, [root])
            state = json.loads(evaluation_state_path(root).read_text(encoding="utf-8"))
            self.assertEqual(state["run_tag"], "daily-run")
            self.assertEqual(state["message"], "evaluation complete")

    def test_run_once_records_no_completed_run_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = EvaluationWorker(
                root,
                hook_runner=lambda _path: CompletionHookResult(evaluation_ran=False, message="no completed run"),
            )

            result = worker.run_once()

            self.assertEqual(result["status"], "no_completed_evaluation")
            state = json.loads(evaluation_state_path(root).read_text(encoding="utf-8"))
            self.assertEqual(state["message"], "no completed run")


if __name__ == "__main__":
    unittest.main()
