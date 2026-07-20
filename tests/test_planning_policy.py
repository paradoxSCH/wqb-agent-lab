from __future__ import annotations

import unittest

from wqb_agent_lab.governance import PlanningPolicyContext, evaluate_plan_proposal
from wqb_agent_lab.planning import parse_plan_proposal


def proposal_with_action(kind: str, *, requested_budget: int = 2):
    return parse_plan_proposal(
        {
            "schema_version": 1,
            "plan_id": "plan-policy",
            "objective": "Keep novel research while controlling execution.",
            "hypotheses": [
                {
                    "hypothesis_id": "hyp-novel",
                    "thesis": "A new proxy may expose delayed belief updating.",
                    "mechanism": "new_unclassified_mechanism",
                    "expressions": ["unknown_operator(new_proxy, 17)"],
                    "requested_budget": requested_budget,
                    "extensions": {"proposed_proxy_fields": ["new_proxy"]},
                }
            ],
            "requested_actions": [
                {
                    "action_id": "action-1",
                    "kind": kind,
                    "candidate_ref": "hyp-novel",
                }
            ],
        }
    )


class PlanningPolicyTests(unittest.TestCase):
    def test_novel_research_is_soft_guidance_and_original_proposal_is_preserved(self) -> None:
        proposal = proposal_with_action("query_operator_catalog")
        decision = evaluate_plan_proposal(
            proposal,
            PlanningPolicyContext(simulation_budget_remaining=0),
        )

        self.assertIs(proposal, decision.proposal)
        self.assertEqual("allow", decision.actions[0].disposition)
        self.assertTrue(decision.actions[0].executable)
        self.assertTrue(decision.research_findings)
        self.assertTrue(all(finding.strength == "soft" for finding in decision.research_findings))
        self.assertIn("research.novel_mechanism", {item.policy_id for item in decision.research_findings})

    def test_unknown_action_kind_enters_exploration_instead_of_being_erased(self) -> None:
        decision = evaluate_plan_proposal(
            proposal_with_action("future_model_action"),
            PlanningPolicyContext(simulation_budget_remaining=0),
        )

        self.assertEqual("explore", decision.actions[0].disposition)
        self.assertEqual("soft", decision.actions[0].findings[0].strength)

    def test_simulation_requires_explicit_runtime_capability(self) -> None:
        decision = evaluate_plan_proposal(
            proposal_with_action("simulate"),
            PlanningPolicyContext(simulation_budget_remaining=10),
        )

        self.assertEqual("deny", decision.actions[0].disposition)
        self.assertEqual("execution.capability_disabled", decision.actions[0].findings[0].policy_id)
        self.assertEqual("hard", decision.actions[0].findings[0].strength)

    def test_enabled_simulation_still_respects_deterministic_budget(self) -> None:
        allowed = evaluate_plan_proposal(
            proposal_with_action("simulate", requested_budget=2),
            PlanningPolicyContext(
                simulation_budget_remaining=2,
                enabled_capabilities=frozenset({"simulation"}),
            ),
        )
        denied = evaluate_plan_proposal(
            proposal_with_action("simulate", requested_budget=3),
            PlanningPolicyContext(
                simulation_budget_remaining=2,
                enabled_capabilities=frozenset({"simulation"}),
            ),
        )

        self.assertEqual("allow", allowed.actions[0].disposition)
        self.assertEqual("deny", denied.actions[0].disposition)
        self.assertEqual("execution.simulation_budget_exceeded", denied.actions[0].findings[0].policy_id)

    def test_unresolved_remote_outcome_defers_replay_even_when_capability_is_enabled(self) -> None:
        decision = evaluate_plan_proposal(
            proposal_with_action("submit"),
            PlanningPolicyContext(
                simulation_budget_remaining=0,
                enabled_capabilities=frozenset({"submission"}),
                unresolved_side_effects=frozenset({"submission"}),
            ),
        )

        self.assertEqual("defer", decision.actions[0].disposition)
        self.assertEqual("execution.unresolved_side_effect", decision.actions[0].findings[0].policy_id)


if __name__ == "__main__":
    unittest.main()
