from __future__ import annotations

import unittest

from src.output_evaluation.registry import built_in_registry, registry_by_artifact


class OutputEvaluationRegistryTests(unittest.TestCase):
    def test_registry_covers_required_output_classes(self) -> None:
        registry = built_in_registry()
        stages = {entry.stage for entry in registry}

        self.assertTrue(
            {
                "user_boundary",
                "candidate_generation",
                "llm_planner",
                "scan_config_expression",
                "wqb_simulation",
                "triage",
                "memory",
                "agent_evaluation",
                "report_ui",
            }
            <= stages
        )

    def test_registry_maps_known_artifacts_to_policy_evaluators(self) -> None:
        by_artifact = registry_by_artifact(built_in_registry())

        self.assertEqual(by_artifact["scan_results_snapshot.json"].policy_evaluator, "diagnosis_policy")
        self.assertEqual(by_artifact["candidate_hypothesis_queue.json"].stage, "candidate_generation")
        self.assertIn("static_preflight", by_artifact["scan_config_*.json"].validators)
        self.assertIn("render_consistency", by_artifact["wqb-agent-latest-workflow-uml.html"].validators)


if __name__ == "__main__":
    unittest.main()
