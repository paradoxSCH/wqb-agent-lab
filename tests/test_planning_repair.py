from __future__ import annotations

import unittest

from wqb_agent_lab.planning import (
    PlanProposalRepairExhausted,
    generate_plan_proposal,
    generate_plan_proposal_result,
)


def _valid_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "plan_id": "plan-repaired",
        "objective": "Preserve a novel mechanism while repairing structure.",
        "hypotheses": [
            {
                "thesis": "A newly proposed proxy may expose attention decay.",
                "mechanism": "unregistered_attention_decay",
                "expressions": ["unseen_operator(new_field, 21)"],
                "extensions": {"new_proxy": "new_field"},
            }
        ],
        "requested_actions": [],
    }


class PlanningRepairTests(unittest.TestCase):
    def test_repairs_structure_and_preserves_novel_research_content(self) -> None:
        prompts: list[str] = []
        responses: list[object] = [
            {
                "schema_version": 1,
                "plan_id": "plan-repaired",
                "hypotheses": _valid_payload()["hypotheses"],
                "requested_actions": [],
            },
            _valid_payload(),
        ]

        def generate(prompt: str) -> object:
            prompts.append(prompt)
            return responses.pop(0)

        proposal = generate_plan_proposal("Create a broad research plan.", generate, max_repairs=1)

        self.assertEqual("unregistered_attention_decay", proposal.hypotheses[0].mechanism)
        self.assertEqual(("unseen_operator(new_field, 21)",), proposal.hypotheses[0].expressions)
        self.assertEqual(2, len(prompts))
        self.assertIn("$.objective: missing required property", prompts[1])
        self.assertIn("did not match the structural envelope", prompts[1])
        self.assertNotIn("policy violation", prompts[1].lower())

    def test_non_object_output_can_be_repaired(self) -> None:
        responses: list[object] = ["I need to return the proposal envelope.", _valid_payload()]

        proposal = generate_plan_proposal("Plan.", lambda _prompt: responses.pop(0), max_repairs=1)

        self.assertEqual("plan-repaired", proposal.plan_id)

    def test_result_reports_failed_structural_attempts(self) -> None:
        responses: list[object] = [{"schema_version": 1}, _valid_payload()]

        result = generate_plan_proposal_result(
            "Plan.",
            lambda _prompt: responses.pop(0),
            max_repairs=1,
        )

        self.assertEqual("plan-repaired", result.proposal.plan_id)
        self.assertEqual(1, result.repair_count)
        self.assertEqual(1, result.failed_attempts[0].attempt)

    def test_repair_attempts_are_bounded_and_auditable(self) -> None:
        calls = 0

        def generate(_prompt: str) -> object:
            nonlocal calls
            calls += 1
            return {"schema_version": 1}

        with self.assertRaises(PlanProposalRepairExhausted) as raised:
            generate_plan_proposal("Plan.", generate, max_repairs=2)

        self.assertEqual(3, calls)
        self.assertEqual(3, len(raised.exception.attempts))
        self.assertTrue(all(attempt.structural_errors for attempt in raised.exception.attempts))

    def test_zero_repairs_does_not_retry(self) -> None:
        calls = 0

        def generate(_prompt: str) -> object:
            nonlocal calls
            calls += 1
            return None

        with self.assertRaises(PlanProposalRepairExhausted):
            generate_plan_proposal("Plan.", generate, max_repairs=0)

        self.assertEqual(1, calls)

    def test_repair_limit_is_an_operational_bound_not_a_content_filter(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 0 and 5"):
            generate_plan_proposal("Plan.", lambda _prompt: _valid_payload(), max_repairs=6)


if __name__ == "__main__":
    unittest.main()
