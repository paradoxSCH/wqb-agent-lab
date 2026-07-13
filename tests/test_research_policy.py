from __future__ import annotations

import unittest


def valid_workflow_config() -> dict[str, object]:
    return {
        "workflow_name": "policy-test",
        "research_policy": {
            "version": 1,
            "budget": {
                "daily_simulation_limit": 20,
                "exploration_share_limit": 0.4,
                "exploration_stages": ["direction_probe"],
                "stage_allocations": {
                    "direction_probe": 8,
                    "scale_winners": 8,
                    "holdout": 4,
                },
            },
            "behavioral_boundaries": {
                "block_unclassified_candidates": True,
                "require_kill_conditions": True,
                "forbid_pure_price_volume": True,
                "mechanisms": [
                    {
                        "mechanism_id": "reference_point_disposition_drift",
                        "enabled": True,
                        "allowed_proxy_fields": ["anl*", "fundamental_*"],
                        "kill_conditions": ["SELF_CORRELATION", "LOW_FITNESS"],
                    },
                    {
                        "mechanism_id": "disabled_attention_proxy",
                        "enabled": False,
                        "allowed_proxy_fields": [],
                        "kill_conditions": [],
                    },
                ],
            },
        },
    }


class ResearchPolicyContractTests(unittest.TestCase):
    def test_missing_policy_has_stable_error_code(self) -> None:
        from src.research_policy import ResearchPolicyError, load_research_policy

        with self.assertRaises(ResearchPolicyError) as raised:
            load_research_policy({"workflow_name": "missing-policy"})

        self.assertEqual("missing_research_policy", raised.exception.code)
        self.assertEqual("$.research_policy", raised.exception.path)

    def test_stage_allocations_must_equal_daily_limit(self) -> None:
        from src.research_policy import ResearchPolicyError, load_research_policy

        config = valid_workflow_config()
        config["research_policy"]["budget"]["stage_allocations"]["holdout"] = 3

        with self.assertRaises(ResearchPolicyError) as raised:
            load_research_policy(config)

        self.assertEqual("budget_allocation_mismatch", raised.exception.code)

    def test_mechanism_ids_must_be_unique(self) -> None:
        from src.research_policy import ResearchPolicyError, load_research_policy

        config = valid_workflow_config()
        mechanisms = config["research_policy"]["behavioral_boundaries"]["mechanisms"]
        mechanisms.append(dict(mechanisms[0]))

        with self.assertRaises(ResearchPolicyError) as raised:
            load_research_policy(config)

        self.assertEqual("duplicate_behavioral_mechanism", raised.exception.code)

    def test_exploration_stages_must_respect_share_limit(self) -> None:
        from src.research_policy import ResearchPolicyError, load_research_policy

        config = valid_workflow_config()
        config["research_policy"]["budget"]["exploration_share_limit"] = 0.2

        with self.assertRaises(ResearchPolicyError) as raised:
            load_research_policy(config)

        self.assertEqual("exploration_budget_exceeded", raised.exception.code)

    def test_valid_policy_builds_domain_model_and_stable_digest(self) -> None:
        from src.research_policy import load_research_policy, policy_digest

        policy = load_research_policy(valid_workflow_config())

        self.assertEqual(1, policy.version)
        self.assertEqual(20, policy.budget.daily_simulation_limit)
        self.assertEqual(("reference_point_disposition_drift",), policy.enabled_mechanism_ids)
        self.assertRegex(policy_digest(policy), r"^[0-9a-f]{64}$")
        self.assertEqual(policy_digest(policy), policy_digest(load_research_policy(valid_workflow_config())))


class CandidateBoundaryEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        from src.research_policy import load_research_policy

        self.policy = load_research_policy(valid_workflow_config())

    def evaluate(self, **overrides: object):
        from src.research_policy import evaluate_candidate_boundaries

        candidate = {
            "candidate_id": "candidate-1",
            "behavioral_mechanism": "reference_point_disposition_drift",
            "fields": ["anl_revision_3m"],
            "kill_conditions": ["SELF_CORRELATION", "LOW_FITNESS"],
        }
        candidate.update(overrides)
        return evaluate_candidate_boundaries(candidate, self.policy)

    def test_unknown_mechanism_is_blocked(self) -> None:
        decision = self.evaluate(behavioral_mechanism="invented_mechanism")

        self.assertFalse(decision.allowed)
        self.assertIn("unknown_behavioral_mechanism", decision.error_codes)

    def test_disabled_mechanism_is_blocked(self) -> None:
        decision = self.evaluate(behavioral_mechanism="disabled_attention_proxy")

        self.assertFalse(decision.allowed)
        self.assertIn("disabled_behavioral_mechanism", decision.error_codes)

    def test_field_outside_allowed_patterns_is_blocked(self) -> None:
        decision = self.evaluate(fields=["news_sentiment"])

        self.assertFalse(decision.allowed)
        self.assertIn("proxy_field_outside_boundary", decision.error_codes)

    def test_missing_required_kill_condition_is_blocked(self) -> None:
        decision = self.evaluate(kill_conditions=["LOW_FITNESS"])

        self.assertFalse(decision.allowed)
        self.assertIn("missing_required_kill_condition", decision.error_codes)

    def test_pure_price_volume_candidate_is_blocked(self) -> None:
        decision = self.evaluate(fields=["close", "volume"])

        self.assertFalse(decision.allowed)
        self.assertIn("pure_price_volume_candidate", decision.error_codes)

    def test_candidate_inside_boundaries_is_allowed_and_serializable(self) -> None:
        decision = self.evaluate(fields=["anl_revision_3m", "fundamental_quality"])

        self.assertTrue(decision.allowed)
        self.assertEqual((), decision.error_codes)
        self.assertEqual(
            {
                "candidate_id": "candidate-1",
                "behavioral_mechanism": "reference_point_disposition_drift",
                "allowed": True,
                "errors": [],
            },
            decision.to_dict(),
        )

    def test_non_string_candidate_fields_fail_closed(self) -> None:
        decision = self.evaluate(fields=[123])

        self.assertFalse(decision.allowed)
        self.assertIn("missing_proxy_fields", decision.error_codes)


if __name__ == "__main__":
    unittest.main()
