from __future__ import annotations

import unittest

from wqb_agent_lab.memory.core.governance import (
    GovernanceDecision,
    decide_memory_governance,
    suggest_merge_key,
)


class AlphaMemoryGovernanceTests(unittest.TestCase):
    def test_promotes_low_corr_repeated_success(self) -> None:
        decision = decide_memory_governance({
            "near_pass_count": 2,
            "self_corr": 0.18,
            "submit_ready_count": 0,
            "duplicate": False,
        })

        self.assertEqual(decision.action, "promote")
        self.assertIn("near-pass", decision.reason)

    def test_does_not_promote_submit_ready_with_unknown_self_corr(self) -> None:
        decision = decide_memory_governance({
            "near_pass_count": 0,
            "self_corr": None,
            "submit_ready_count": 1,
            "duplicate": False,
            "spent_simulations": 0,
            "low_fitness_count": 0,
        })

        self.assertEqual(decision.action, "hold")

    def test_does_not_promote_near_pass_with_missing_self_corr(self) -> None:
        decision = decide_memory_governance({
            "near_pass_count": 2,
            "submit_ready_count": 0,
            "duplicate": False,
            "spent_simulations": 0,
            "low_fitness_count": 0,
        })

        self.assertEqual(decision.action, "hold")

    def test_decays_non_submit_ready_budget_sink(self) -> None:
        decision = decide_memory_governance({
            "spent_simulations": 600,
            "near_pass_count": 0,
            "submit_ready_count": 0,
            "low_fitness_count": 0,
        })

        self.assertEqual(decision.action, "decay")
        self.assertGreater(decision.decay_score, 0.5)
        self.assertIn("budget", decision.reason)

    def test_preserves_submit_ready_evidence_from_budget_sink_decay(self) -> None:
        decision = decide_memory_governance({
            "spent_simulations": 600,
            "near_pass_count": 0,
            "submit_ready_count": 1,
            "low_fitness_count": 0,
            "self_corr": 0.8,
        })

        self.assertEqual(decision.action, "hold")

    def test_decays_persistent_low_fitness(self) -> None:
        decision = decide_memory_governance({
            "spent_simulations": 0,
            "near_pass_count": 1,
            "submit_ready_count": 0,
            "low_fitness_count": 18,
        })

        self.assertEqual(decision.action, "decay")
        self.assertGreater(decision.decay_score, 0.5)

    def test_forgets_decorative_non_actionable_memory(self) -> None:
        decision = decide_memory_governance({
            "non_actionable_retrievals": 4,
            "proxy_mapping_count": 0,
        })

        self.assertEqual(decision.action, "forget")

    def test_merge_key_canonicalizes_operator_skeleton(self) -> None:
        self.assertEqual(
            suggest_merge_key("Rank( Ts_Mean( cashflow , 60 ) ) - Rank(close)"),
            "rank(ts_mean(cashflow,60))-rank(close)",
        )

    def test_governance_decision_serializes(self) -> None:
        decision = GovernanceDecision(action="block", reason="duplicate skeleton", decay_score=1.0)
        self.assertEqual(decision.to_dict()["action"], "block")


if __name__ == "__main__":
    unittest.main()
