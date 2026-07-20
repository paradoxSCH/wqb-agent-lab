from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from wqb_agent_lab.workflow.engine import ResearchWorkflow


class PolicyFeedbackWorkflowTests(unittest.TestCase):
    def test_prepare_budgeted_scan_defaults_to_shadow_without_changing_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root)
            source_config = root / ".local" / "research" / "scans" / "continuous-alpha" / "source-500" / "scan_config_round1.json"
            candidates = [
                self._candidate("rank(field_a)", "family_a", max_share=0.34),
                self._candidate("rank(field_b)", "family_b", max_share=0.34),
                self._candidate("rank(field_c)", "family_c", max_share=0.34),
                {"expression": "rank(field_d)", "behavior_family": "family_d"},
                {"expression": "rank(field_e)", "behavior_family": "family_e"},
                {"expression": "rank(field_f)", "behavior_family": "family_f"},
            ]
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})

            workflow = ResearchWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            plan = workflow.prepare_budgeted_scan(workflow.plan_next_scan(ledger))

            assert plan.sliced_config is not None
            payload = json.loads(plan.sliced_config.read_text(encoding="utf-8"))
            selected = payload["candidates"]
            capped = [row for row in selected if row.get("policy_feedback", {}).get("max_budget_share") == 0.34]

            self.assertEqual(len(selected), 6)
            self.assertEqual(len(capped), 3)
            context = payload["daily_budget_context"]
            self.assertEqual(context["policy_feedback_governance"]["effective_mode"], "shadow")
            self.assertEqual(context["required_policy_experiments"], [])
            self.assertEqual(context["policy_action_lanes"], [])
            self.assertEqual(context["recommended_policy_experiments"], ["industry_neutralization"])
            self.assertEqual(context["recommended_policy_action_lanes"], ["repair_probe"])
            self.assertEqual(context["policy_budget_caps_applied"], {})
            self.assertTrue(context["policy_budget_caps_recommended"])

            attribution = json.loads((workflow.run_dir / "decision_attribution.json").read_text(encoding="utf-8"))
            self.assertEqual(attribution[0]["policy_action_lanes"], [])
            self.assertEqual(attribution[0]["required_experiments_used"], [])
            self.assertEqual(attribution[0]["policy_actions_used"], [])
            self.assertEqual(
                attribution[0]["policy_actions_observed"][0]["diagnosis_type"],
                "overcrowded_skeleton",
            )
            shadow = json.loads((workflow.run_dir / "policy_feedback_shadow.json").read_text(encoding="utf-8"))
            self.assertEqual(6, len(shadow["decisions"][0]["baseline_candidates"]))
            self.assertEqual("shadow", shadow["decisions"][0]["governance"]["effective_mode"])

    def test_control_mode_is_blocked_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root, policy_feedback={"mode": "control"})
            source_config = root / ".local/research/scans/continuous-alpha/source-500/scan_config_round1.json"
            candidates = [self._candidate(f"rank(field_{index})", f"family_{index}", max_share=0.2) for index in range(6)]
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})
            workflow = ResearchWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)

            plan = workflow.prepare_budgeted_scan(workflow.plan_next_scan(workflow.load_or_create_ledger()))

            assert plan.sliced_config is not None
            payload = json.loads(plan.sliced_config.read_text(encoding="utf-8"))
            governance = payload["daily_budget_context"]["policy_feedback_governance"]
            self.assertEqual("control", governance["requested_mode"])
            self.assertEqual("shadow", governance["effective_mode"])
            self.assertFalse(governance["promotion_gate"]["passed"])
            self.assertEqual(6, len(payload["candidates"]))

    def test_control_mode_applies_only_after_multi_run_promotion_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workflow_config(root, policy_feedback={"mode": "control"})
            self._seed_passing_shadow_evidence(root)
            source_config = root / ".local/research/scans/continuous-alpha/source-500/scan_config_round1.json"
            candidates = [
                self._candidate(f"rank(field_{index})", f"family_{index}", max_share=0.34)
                for index in range(5)
            ]
            candidates.append({"expression": "rank(open_field)", "behavior_family": "open_family"})
            candidates[4]["extensions"] = {"novel_llm_operator_request": "retain-even-if-overflow"}
            self._write_json(source_config, {"output": "unused.json", "candidates": candidates})
            workflow = ResearchWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)

            plan = workflow.prepare_budgeted_scan(workflow.plan_next_scan(workflow.load_or_create_ledger()))

            assert plan.sliced_config is not None
            payload = json.loads(plan.sliced_config.read_text(encoding="utf-8"))
            governance = payload["daily_budget_context"]["policy_feedback_governance"]
            self.assertEqual("control", governance["effective_mode"])
            self.assertTrue(governance["promotion_gate"]["passed"])
            self.assertGreaterEqual(governance["exploration_overflow_admitted"], 1)
            self.assertEqual(5, len(payload["candidates"]))
            shadow = json.loads((workflow.run_dir / "policy_feedback_shadow.json").read_text(encoding="utf-8"))
            overflow = shadow["decisions"][0]["overflow_candidates"]
            self.assertTrue(
                any(
                    row.get("extensions", {}).get("novel_llm_operator_request") == "retain-even-if-overflow"
                    for row in overflow
                )
            )
            attribution = json.loads(
                (workflow.run_dir / "decision_attribution.json").read_text(encoding="utf-8")
            )
            self.assertTrue(attribution[0]["policy_actions_used"])

    def _candidate(self, expression: str, family: str, *, max_share: float) -> dict[str, object]:
        return {
            "expression": expression,
            "behavior_family": family,
            "wqb_action_lane": "repair_probe",
            "policy_feedback": {
                "max_budget_share": max_share,
                "required_experiments": ["industry_neutralization"],
                "budget_actions": {
                    "overcrowded_skeleton": {
                        "diagnosis_type": "overcrowded_skeleton",
                        "budget_action": "allocate_controlled_repair_budget",
                        "max_budget_share": 0.15,
                    }
                },
            },
        }

    def _write_workflow_config(
        self,
        root: Path,
        *,
        policy_feedback: dict[str, object] | None = None,
    ) -> None:
        payload = {
            "daily_run_tag_prefix": "kimi-daily-budget",
            "capacity_estimate": {"recommended_mode": "standard", "max_scan_concurrency": 3},
            "daily_budget_modes": {
                "standard": {
                    "daily_budget": 10,
                    "stage_budgets": {"scale_winners": 6},
                }
            },
            "stage_order": ["scale_winners"],
            "default_queued_scan_configs": [".local/research/scans/continuous-alpha/source-500/scan_config_round1.json"],
            "decision_attribution": {"enabled": True},
            "policy_feedback": policy_feedback or {"mode": "shadow"},
            "diversity_caps": {
                "single_base_alpha_daily_budget_max_share": 1.0,
                "single_field_daily_budget_max_share": 1.0,
            },
        }
        self._write_json(root / ".local" / "research" / "workflows" / "production.json", payload)
        self._write_json(root / ".local" / "research" / "workflows" / "continuous-alpha" / "deepseek_v4_pro_daily_budget.json", payload)
        self._write_json(root / ".local" / "research" / "workflows" / "continuous-alpha" / "kimi_daily_budget_20260504.json", payload)

    def _seed_passing_shadow_evidence(self, root: Path) -> None:
        runs_root = root / ".local/data/runs/continuous-alpha"
        for index in range(3):
            self._write_json(
                runs_root / f"evidence-{index}" / "policy_feedback_shadow_evaluation.json",
                {
                    "aggregate": {
                        "baseline_simulations_observed": 50,
                        "recommended_simulations_observed": 50,
                        "baseline_submit_ready_count": 5,
                        "recommended_submit_ready_count": 10,
                        "baseline_low_value_count": 25,
                        "recommended_low_value_count": 15,
                        "baseline_distinct_family_count": 5,
                        "recommended_distinct_family_count": 4,
                    }
                },
            )

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
