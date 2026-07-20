from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from wqb_agent_lab.workflow.engine import ResearchWorkflow
from wqb_agent_lab.llm.provider import LLMProviderError, LLMResponse, LLMUsage
from wqb_agent_lab.workflow import StageResult


class RecordingPlanningProvider:
    provider_id = "ollama"
    model = "open-research-model"

    def __init__(self) -> None:
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return LLMResponse(
            content=json.dumps(
                {
                    "families": [
                        {
                            "mechanism": "unknown cross-cohort attention migration",
                            "expression": "future_operator(vector_neutralize(x, custom_group))",
                            "requested_action_kind": "future_research_lane",
                        }
                    ],
                    "freeform_notes": "retain this hypothesis without an allowlist",
                }
            ),
            provider=self.provider_id,
            model=self.model,
            usage=LLMUsage(),
        )


class RetryablePlanningProvider(RecordingPlanningProvider):
    def complete(self, request):
        self.requests.append(request)
        raise LLMProviderError(
            code="rate_limited",
            message="retry later",
            provider=self.provider_id,
            model=self.model,
            retryable=True,
        )


class WorkflowPlanningStageTests(unittest.TestCase):
    def _workflow(self, root: Path, provider) -> ResearchWorkflow:
        config_path = root / "workflow.json"
        config_path.write_text(
            json.dumps(
                {
                    "capacity_estimate": {"recommended_mode": "standard"},
                    "daily_budget_modes": {
                        "standard": {"daily_budget": 10, "stage_budgets": {}}
                    },
                    "stage_order": [],
                    "llm_provider": {"provider": "ollama", "model": provider.model},
                }
            ),
            encoding="utf-8",
        )
        return ResearchWorkflow(
            root,
            workflow_config=config_path,
            run_date=date(2026, 7, 20),
            llm_provider=provider,
        )

    def test_planning_stage_preserves_output_and_reuses_existing_provider_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = RecordingPlanningProvider()
            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                workflow = self._workflow(root, provider)
                ledger = workflow.load_or_create_ledger()
                first = workflow.run_llm_plan(ledger, now=datetime(2026, 7, 20, 9, 0))
                second = workflow.run_llm_plan(ledger, now=datetime(2026, 7, 20, 9, 1))

            self.assertEqual(first, second)
            self.assertEqual(1, len(provider.requests))
            assert second is not None
            payload = json.loads(second.read_text(encoding="utf-8"))
            self.assertEqual(
                "future_operator(vector_neutralize(x, custom_group))",
                payload["families"][0]["expression"],
            )
            checkpoint = workflow.stage_checkpoint_store.load("llm_planning")
            assert checkpoint is not None
            self.assertEqual("completed", checkpoint.status)
            self.assertEqual(2, checkpoint.attempt_number)
            self.assertIn(second.resolve().relative_to(root.resolve()).as_posix(), checkpoint.artifacts)
            self.assertTrue(checkpoint.extensions["research_payload_preserved_in_artifact"])

    def test_interrupted_planning_checkpoint_is_safely_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = RecordingPlanningProvider()
            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                workflow = self._workflow(root, provider)
                ledger = workflow.load_or_create_ledger()
                workflow.stage_checkpoint_store.write(
                    StageResult.create(
                        run_id=workflow.run_tag,
                        stage_id="llm_planning",
                        attempt_id="interrupted-planning",
                        attempt_number=4,
                        status="running",
                        started_at="2026-07-20T08:59:00",
                        completed_at=None,
                    )
                )
                output = workflow.run_llm_plan(ledger, now=datetime(2026, 7, 20, 9, 0))

            self.assertIsNotNone(output)
            checkpoint = workflow.stage_checkpoint_store.load("llm_planning")
            assert checkpoint is not None
            self.assertEqual(5, checkpoint.attempt_number)
            self.assertEqual("interrupted-planning", checkpoint.extensions["resumed_from_attempt_id"])

    def test_retryable_planner_error_is_deferred_without_blocking_research_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = RetryablePlanningProvider()
            with patch.dict(os.environ, {"WQB_DISABLE_LLM_TEMPLATE_BACKEND": "0"}):
                workflow = self._workflow(root, provider)
                output = workflow.run_llm_plan(
                    workflow.load_or_create_ledger(),
                    now=datetime(2026, 7, 20, 9, 0),
                )

            self.assertIsNotNone(output)
            checkpoint = workflow.stage_checkpoint_store.load("llm_planning")
            assert checkpoint is not None
            self.assertEqual("deferred", checkpoint.status)
            self.assertTrue(checkpoint.output["retryable"])


if __name__ == "__main__":
    unittest.main()
