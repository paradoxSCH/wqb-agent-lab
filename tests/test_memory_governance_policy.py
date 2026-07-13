from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from src.alpha_memory.schema import MemoryNode
from src.memory_governance import build_memory_governance_report, write_memory_governance_report
from src.memory_governance.policy import (
    assess_evidence,
    evaluate_forgetting,
    is_retrievable_for_mode,
    resolve_action_permission,
)


class MemoryGovernancePolicyTests(unittest.TestCase):
    def test_raw_observation_has_prompt_only_permission(self) -> None:
        assessment = assess_evidence({"tested_count": 1, "near_pass_count": 0, "all_pass_count": 0})
        permission = resolve_action_permission(assessment)

        self.assertEqual(assessment.evidence_level, "L0")
        self.assertTrue(permission.can_use_in_prompt)
        self.assertFalse(permission.can_increase_budget)
        self.assertEqual(permission.max_budget_policy, "none")

    def test_repeated_pattern_allows_controlled_budget_only(self) -> None:
        assessment = assess_evidence({
            "tested_count": 24,
            "near_pass_count": 2,
            "all_pass_count": 0,
            "skeleton_diversity": 3,
            "field_diversity": 2,
            "low_value_rate": 0.55,
        })
        permission = resolve_action_permission(assessment)

        self.assertEqual(assessment.evidence_level, "L2")
        self.assertTrue(permission.can_use_in_prompt)
        self.assertFalse(permission.can_increase_budget)
        self.assertFalse(permission.can_promote)
        self.assertEqual(permission.max_budget_policy, "policy_evaluator_required")

    def test_actionable_rule_can_promote_or_block(self) -> None:
        assessment = assess_evidence({
            "tested_count": 30,
            "near_pass_count": 3,
            "all_pass_count": 1,
            "skeleton_diversity": 4,
            "field_diversity": 3,
            "low_value_rate": 0.35,
            "decision_outcome_lift": 0.22,
        })
        permission = resolve_action_permission(assessment)

        self.assertEqual(assessment.evidence_level, "L3")
        self.assertTrue(permission.can_promote)
        self.assertFalse(permission.can_block_generation)
        self.assertEqual(permission.max_budget_policy, "policy_evaluator_required")

    def test_weak_proxy_is_quarantined_by_forgetting_policy(self) -> None:
        update = evaluate_forgetting({
            "tested_count": 40,
            "low_fitness_count": 32,
            "low_sharpe_count": 30,
            "all_pass_count": 0,
            "near_pass_count": 0,
        })

        self.assertEqual(update.status, "deprecated")
        self.assertEqual(update.forgetting_state, "quarantined")
        self.assertGreaterEqual(update.decay_delta, 0.5)

    def test_duplicate_skeleton_is_blocked_and_forgotten_from_action_context(self) -> None:
        update = evaluate_forgetting({"duplicate": True, "blocked_skeleton_count": 2})

        self.assertEqual(update.status, "blocked")
        self.assertEqual(update.forgetting_state, "forgotten")
        self.assertEqual(update.decay_delta, 1.0)

    def test_default_planner_retrieval_excludes_non_actionable_statuses(self) -> None:
        active = self._node("active-node", status="active", forgetting_state="active")
        probation = self._node("probation-node", status="probation", forgetting_state="active")
        deprecated = self._node("deprecated-node", status="deprecated", forgetting_state="quarantined")
        blocked = self._node("blocked-node", status="blocked", forgetting_state="forgotten")

        self.assertTrue(is_retrievable_for_mode(active, "planner"))
        self.assertFalse(is_retrievable_for_mode(probation, "planner"))
        self.assertFalse(is_retrievable_for_mode(deprecated, "planner"))
        self.assertFalse(is_retrievable_for_mode(blocked, "planner"))
        self.assertTrue(is_retrievable_for_mode(probation, "risk_review"))
        self.assertTrue(is_retrievable_for_mode(deprecated, "risk_review"))
        self.assertTrue(is_retrievable_for_mode(blocked, "audit"))

    def test_memory_governance_report_promotes_strong_policy_and_quarantines_weak_policy(self) -> None:
        effectiveness = {
            "policies": [
                {
                    "diagnosis_type": "sub_universe_instability",
                    "decision_count": 3,
                    "simulations_spent": 45,
                    "submit_ready_count": 3,
                    "near_pass_count": 8,
                    "low_value_count": 16,
                    "low_value_rate": 0.3556,
                    "roi_per_1000": 66.667,
                },
                {
                    "diagnosis_type": "weak_behavior_proxy",
                    "decision_count": 2,
                    "simulations_spent": 40,
                    "submit_ready_count": 0,
                    "near_pass_count": 0,
                    "low_value_count": 38,
                    "low_value_rate": 0.95,
                    "roi_per_1000": 0.0,
                },
            ]
        }

        report = build_memory_governance_report(effectiveness)
        by_type = {row["diagnosis_type"]: row for row in report["policies"]}

        self.assertEqual(by_type["sub_universe_instability"]["evidence_level"], "L3")
        self.assertTrue(by_type["sub_universe_instability"]["permission"]["can_promote"])
        self.assertEqual(by_type["sub_universe_instability"]["memory_action"], "promote_candidate")
        self.assertEqual(by_type["weak_behavior_proxy"]["forgetting_update"]["forgetting_state"], "quarantined")
        self.assertEqual(by_type["weak_behavior_proxy"]["memory_action"], "quarantine_candidate")

    def test_write_memory_governance_report_reads_policy_effectiveness_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "policy_effectiveness_report.json").write_text(
                json.dumps(
                    {
                        "policies": [
                            {
                                "diagnosis_type": "overcrowded_skeleton",
                                "simulations_spent": 20,
                                "submit_ready_count": 0,
                                "near_pass_count": 0,
                                "low_value_count": 20,
                                "low_value_rate": 1.0,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            path = write_memory_governance_report(run_dir)
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(path.name, "memory_governance_report.json")
            self.assertEqual(payload["policies"][0]["memory_action"], "quarantine_candidate")

    def _node(self, node_id: str, *, status: str, forgetting_state: str) -> MemoryNode:
        return MemoryNode(
            id=node_id,
            type="alpha_family",
            layer="long_term",
            title=node_id,
            summary="quality value memory",
            status=status,
            forgetting_state=forgetting_state,
            confidence=0.8,
        )


if __name__ == "__main__":
    unittest.main()
