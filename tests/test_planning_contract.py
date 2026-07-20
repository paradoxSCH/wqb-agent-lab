from __future__ import annotations

import unittest

from wqb_agent_lab.planning import PlanProposalValidationError, parse_plan_proposal


class PlanningContractTests(unittest.TestCase):
    def test_round_trip_preserves_unknown_research_ideas_and_extensions(self) -> None:
        payload = {
            "schema_version": 1,
            "plan_id": "plan-open-001",
            "objective": "Explore a mechanism that the product has never classified.",
            "hypotheses": [
                {
                    "hypothesis_id": "hyp-new-001",
                    "thesis": "An unknown attention proxy may reveal delayed belief updating.",
                    "mechanism": "previously_unknown_mechanism",
                    "expressions": [
                        "future_operator(field_never_seen_before, ts_custom_window(close, 17))"
                    ],
                    "evidence_refs": ["memory:new-observation"],
                    "assumptions": ["The proposed field will be checked during preflight."],
                    "uncertainty": 0.73,
                    "kill_conditions": [],
                    "requested_budget": 2,
                    "extensions": {
                        "proposed_proxy_fields": ["field_never_seen_before"],
                        "nested": {"model_specific_trace": [1, 2, 3]},
                    },
                }
            ],
            "requested_actions": [
                {
                    "action_id": "action-new-001",
                    "kind": "action_kind_added_by_a_future_model",
                    "candidate_ref": "hyp-new-001",
                    "rationale": "Request an offline feasibility check before simulation.",
                    "priority": 7,
                    "parameters": {"mode": "offline", "new_parameter": True},
                    "extensions": {"provider_hint": "retain"},
                }
            ],
            "alternatives": [{"unstructured_future_shape": {"is_allowed": True}}],
            "policy_exception_requests": [
                {
                    "policy_id": "soft.known_proxy_preference",
                    "rationale": "The new proxy is the substance of the experiment.",
                    "evidence_refs": ["memory:new-observation"],
                    "extensions": {"review_lane": "exploration"},
                }
            ],
            "freeform_notes": "Free-form reasoning remains available beside the stable envelope.",
            "extensions": {"future_top_level_capability": {"enabled": True}},
        }

        proposal = parse_plan_proposal(payload)
        round_trip = proposal.to_dict()

        self.assertEqual("previously_unknown_mechanism", proposal.hypotheses[0].mechanism)
        self.assertEqual("action_kind_added_by_a_future_model", proposal.requested_actions[0].kind)
        self.assertEqual(payload["hypotheses"][0]["expressions"], round_trip["hypotheses"][0]["expressions"])
        self.assertEqual(payload["extensions"], round_trip["extensions"])
        self.assertEqual(payload["alternatives"], round_trip["alternatives"])

    def test_empty_hypothesis_set_is_valid_and_does_not_force_fabrication(self) -> None:
        proposal = parse_plan_proposal(
            {
                "schema_version": 1,
                "plan_id": "plan-no-evidence",
                "objective": "Report whether current evidence supports a new experiment.",
                "hypotheses": [],
                "requested_actions": [],
                "freeform_notes": "No sufficiently grounded hypothesis was found.",
            }
        )

        self.assertEqual((), proposal.hypotheses)
        self.assertEqual((), proposal.requested_actions)

    def test_structural_failure_produces_repair_feedback_without_policy_language(self) -> None:
        with self.assertRaises(PlanProposalValidationError) as raised:
            parse_plan_proposal(
                {
                    "schema_version": 1,
                    "plan_id": "plan-invalid",
                    "hypotheses": [],
                    "requested_actions": [],
                }
            )

        feedback = raised.exception.repair_feedback()
        self.assertIn("$.objective: missing required property", feedback)
        self.assertIn("without narrowing or discarding the research ideas", feedback)
        self.assertNotIn("policy violation", feedback.lower())

    def test_models_deep_freeze_extension_data(self) -> None:
        payload = {
            "schema_version": 1,
            "plan_id": "plan-immutable",
            "objective": "Keep parsed planning evidence immutable.",
            "hypotheses": [],
            "requested_actions": [],
            "extensions": {"nested": {"values": [1, 2]}},
        }

        proposal = parse_plan_proposal(payload)
        payload["extensions"]["nested"]["values"].append(3)

        self.assertEqual([1, 2], proposal.to_dict()["extensions"]["nested"]["values"])


if __name__ == "__main__":
    unittest.main()
