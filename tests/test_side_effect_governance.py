from __future__ import annotations

import unittest
from unittest.mock import patch


class SideEffectGovernanceTests(unittest.TestCase):
    def test_scan_error_summary_preserves_status_and_detail(self) -> None:
        from wqb_agent_lab.runtime import scan

        summary = scan.summarize_simulation_payload(
            {
                "diagnosis": "simulation_create_failed",
                "status_code": 429,
                "detail": {"message": "too many simulations"},
            }
        )

        self.assertIn("429", summary)
        self.assertIn("too many simulations", summary)

    def test_scan_expression_comparison_ignores_only_whitespace(self) -> None:
        from wqb_agent_lab.runtime import scan

        self.assertEqual(scan._normalized_expression("rank( close )"), "rank(close)")
        self.assertNotEqual(scan._normalized_expression("rank(open)"), "rank(close)")

    def test_capabilities_are_disabled_unless_exactly_enabled(self) -> None:
        from wqb_agent_lab.governance.side_effects import evaluate_side_effect_capability

        missing = evaluate_side_effect_capability("simulation", env={})
        truthy_but_invalid = evaluate_side_effect_capability(
            "submission",
            env={"WQB_LIVE_SUBMIT_CAPABILITY": "true"},
        )

        self.assertFalse(missing.enabled)
        self.assertEqual(missing.environment_variable, "WQB_LIVE_SIMULATION_CAPABILITY")
        self.assertEqual(missing.status, "capability_disabled")
        self.assertFalse(truthy_but_invalid.enabled)

    def test_capabilities_are_independent(self) -> None:
        from wqb_agent_lab.governance.side_effects import evaluate_side_effect_capability

        env = {"WQB_LIVE_SIMULATION_CAPABILITY": "1"}

        self.assertTrue(evaluate_side_effect_capability("simulation", env=env).enabled)
        self.assertFalse(evaluate_side_effect_capability("submission", env=env).enabled)

    def test_enforcement_error_contains_structured_non_secret_decision(self) -> None:
        from wqb_agent_lab.governance.side_effects import SideEffectCapabilityDisabled, require_side_effect_capability

        with self.assertRaises(SideEffectCapabilityDisabled) as raised:
            require_side_effect_capability(
                "submission",
                env={"WQB_LIVE_SUBMIT_CAPABILITY": "not-a-secret-but-still-hidden"},
            )

        decision = raised.exception.decision
        self.assertEqual(decision.operation, "submission")
        self.assertEqual(decision.status, "capability_disabled")
        self.assertNotIn("not-a-secret", str(raised.exception))
        self.assertNotIn("not-a-secret", str(decision.to_dict()))

    def test_single_submit_cli_refuses_before_loading_credentials(self) -> None:
        from scripts.submit import submit_alpha_v2

        with patch.dict("os.environ", {}, clear=True), patch.object(
            submit_alpha_v2,
            "load_config",
        ) as load_config:
            result = submit_alpha_v2.main("A1")

        self.assertEqual(result, 2)
        load_config.assert_not_called()

    def test_scan_runner_refuses_before_reading_config_or_creating_session(self) -> None:
        import asyncio

        from wqb_agent_lab.runtime import scan

        with patch.dict("os.environ", {}, clear=True), patch.object(
            scan.WQBClient,
            "from_config",
        ) as create_client:
            with self.assertRaisesRegex(RuntimeError, "simulation side effect is disabled"):
                asyncio.run(scan.run_scan("missing-config.json"))

        create_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
