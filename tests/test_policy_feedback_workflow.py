from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.kimi_daily_workflow import KimiDailyWorkflow


class PolicyFeedbackWorkflowTests(unittest.TestCase):
    def test_prepare_budgeted_scan_applies_policy_feedback_budget_and_metadata(self) -> None:
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

            workflow = KimiDailyWorkflow(root, run_date=date(2026, 5, 5), execute_scans=False)
            ledger = workflow.load_or_create_ledger()
            plan = workflow.prepare_budgeted_scan(workflow.plan_next_scan(ledger))

            assert plan.sliced_config is not None
            payload = json.loads(plan.sliced_config.read_text(encoding="utf-8"))
            selected = payload["candidates"]
            capped = [row for row in selected if row.get("policy_feedback", {}).get("max_budget_share") == 0.34]

            self.assertLessEqual(len(capped), 2)
            self.assertEqual(payload["daily_budget_context"]["required_policy_experiments"], ["industry_neutralization"])
            self.assertEqual(payload["daily_budget_context"]["policy_action_lanes"], ["repair_probe"])

            attribution = json.loads((workflow.run_dir / "decision_attribution.json").read_text(encoding="utf-8"))
            self.assertEqual(attribution[0]["policy_action_lanes"], ["repair_probe"])
            self.assertEqual(attribution[0]["required_experiments_used"], ["industry_neutralization"])
            self.assertEqual(
                attribution[0]["policy_actions_used"][0]["diagnosis_type"],
                "overcrowded_skeleton",
            )

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

    def _write_workflow_config(self, root: Path) -> None:
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
            "diversity_caps": {
                "single_base_alpha_daily_budget_max_share": 1.0,
                "single_field_daily_budget_max_share": 1.0,
            },
        }
        self._write_json(root / ".local" / "research" / "workflows" / "production.json", payload)
        self._write_json(root / ".local" / "research" / "workflows" / "continuous-alpha" / "deepseek_v4_pro_daily_budget.json", payload)
        self._write_json(root / ".local" / "research" / "workflows" / "continuous-alpha" / "kimi_daily_budget_20260504.json", payload)

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
