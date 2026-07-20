from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from wqb_agent_lab.research.candidates import build_candidate_generation_artifacts


class BehavioralCandidateGenerationTests(unittest.TestCase):
    def test_build_artifacts_outputs_inventory_field_map_and_hypothesis_queue(self) -> None:
        artifacts = build_candidate_generation_artifacts(
            [
                self._field("eps_revision_surprise", "Analyst EPS estimate revision and earnings surprise", dataset="analyst4"),
                self._field("quality_cashflow_score", "Cashflow quality and accrual quality score", dataset="model77"),
                self._field("news_sentiment_extreme", "News sentiment and media attention score", dataset="news12"),
                self._field("short_interest_borrow", "Short interest and borrow pressure", dataset="model77"),
                self._field("high52w_anchor", "52 week high reference point distance", dataset="model77"),
                self._field("volume_attention", "Trading volume attention filter", dataset="pv1"),
            ]
        )

        inventory = artifacts["behavioral_mechanism_inventory"]
        field_map = artifacts["behavioral_proxy_field_map"]
        queue = artifacts["candidate_hypothesis_queue"]

        self.assertGreaterEqual(len(inventory["mechanisms"]), 6)
        self.assertGreaterEqual(len(field_map["mappings"]), 4)
        self.assertGreaterEqual(len(queue["hypotheses"]), 4)

        first = inventory["mechanisms"][0]
        self.assertTrue({"mechanism", "zh_name", "behavioral_logic", "proxy_requirements", "kill_conditions"} <= first.keys())
        self.assertTrue(first["zh_name"])

        mechanisms_with_fields = {row["mechanism"] for row in field_map["mappings"] if row["proxy_strength"] != "none"}
        for hypothesis in queue["hypotheses"]:
            self.assertIn(hypothesis["mechanism"], mechanisms_with_fields)
            self.assertTrue(hypothesis["kill_conditions"])
            self.assertTrue(hypothesis["skeleton_template"])
            self.assertIn("non_price_volume_primary_proxy", hypothesis["preflight_requirements"])
            self.assertNotIn(hypothesis["primary_proxy"], {"close", "returns", "volume", "vwap"})
            self.assertEqual(hypothesis["wqb_action_lane"], "probe")

    def test_field_map_blocks_unproxyable_mechanisms_from_queue(self) -> None:
        artifacts = build_candidate_generation_artifacts(
            [self._field("close", "Close price", dataset="pv1"), self._field("volume", "Daily volume", dataset="pv1")]
        )

        field_map = artifacts["behavioral_proxy_field_map"]
        queue = artifacts["candidate_hypothesis_queue"]

        self.assertTrue(all(row["proxy_strength"] == "none" for row in field_map["mappings"]))
        self.assertEqual(queue["hypotheses"], [])

    def test_policy_feedback_marks_next_hypothesis_queue_with_budget_controls(self) -> None:
        artifacts = build_candidate_generation_artifacts(
            [
                self._field("news_sentiment_extreme", "News sentiment and media attention score", dataset="news12"),
                self._field("quality_cashflow_score", "Cashflow quality and accrual quality score", dataset="model77"),
            ],
            policy_feedback={
                "budget_policy_actions": [
                    {
                        "diagnosis_type": "field_type_operator_mismatch",
                        "budget_action": "block_until_preflight_guard",
                        "max_budget_share": 0.0,
                    },
                    {
                        "diagnosis_type": "weak_behavior_proxy",
                        "budget_action": "downweight_family_or_proxy",
                        "max_budget_share": 0.05,
                    },
                    {
                        "diagnosis_type": "overcrowded_skeleton",
                        "budget_action": "allocate_controlled_repair_budget",
                        "max_budget_share": 0.15,
                    },
                    {
                        "diagnosis_type": "sub_universe_instability",
                        "budget_action": "allocate_grouping_probe_budget",
                        "max_budget_share": 0.08,
                    },
                ]
            },
        )

        queue = artifacts["candidate_hypothesis_queue"]
        self.assertTrue(queue["policy_feedback"]["static_preflight_required"])
        self.assertEqual(queue["policy_feedback"]["budget_actions"]["overcrowded_skeleton"]["budget_action"], "allocate_controlled_repair_budget")
        self.assertTrue(queue["hypotheses"])
        for hypothesis in queue["hypotheses"]:
            feedback = hypothesis["policy_feedback"]
            self.assertTrue(feedback["static_preflight_required"])
            self.assertTrue(feedback["requires_chassis_change"])
            self.assertEqual(hypothesis["wqb_action_lane"], "probe")
            self.assertEqual(feedback["recommended_action_lane"], "repair_probe")
            self.assertIn("industry_neutralization", feedback["required_experiments"])
            if feedback["proxy_strength"] == "weak":
                self.assertEqual(feedback["max_budget_share"], 0.05)

    def test_policy_feedback_uses_bucket_specific_actions_for_candidate_lanes(self) -> None:
        artifacts = build_candidate_generation_artifacts(
            [
                self._field("news_sentiment_extreme", "News sentiment and media attention score", dataset="news12"),
                self._field("quality_cashflow_score", "Cashflow quality and accrual quality score", dataset="model77"),
            ],
            policy_feedback={
                "budget_policy_actions": [
                    {
                        "diagnosis_type": "weak_behavior_proxy",
                        "bucket": "deep_fail",
                        "policy_key": "weak_behavior_proxy:deep_fail",
                        "budget_action": "replace_proxy_before_resimulation",
                        "max_budget_share": 0.0,
                    },
                    {
                        "diagnosis_type": "weak_behavior_proxy",
                        "bucket": "near_pass",
                        "policy_key": "weak_behavior_proxy:near_pass",
                        "budget_action": "allocate_small_parameter_repair",
                        "max_budget_share": 0.08,
                    },
                    {
                        "diagnosis_type": "sub_universe_instability",
                        "bucket": "severe",
                        "policy_key": "sub_universe_instability:severe",
                        "budget_action": "replace_unstable_universe_proxy",
                        "max_budget_share": 0.0,
                    },
                ]
            },
        )

        queue = artifacts["candidate_hypothesis_queue"]
        self.assertEqual(
            queue["policy_feedback"]["budget_actions_by_key"]["weak_behavior_proxy:deep_fail"]["budget_action"],
            "replace_proxy_before_resimulation",
        )
        self.assertTrue(queue["hypotheses"])
        for hypothesis in queue["hypotheses"]:
            feedback = hypothesis["policy_feedback"]
            self.assertEqual(hypothesis["wqb_action_lane"], "probe")
            self.assertEqual(feedback["recommended_action_lane"], "replace_probe")
            self.assertTrue(feedback["requires_chassis_change"])
            self.assertIn("sub_universe_instability:severe", feedback["budget_actions"])
            self.assertIn("weak_behavior_proxy:deep_fail", feedback["budget_actions"])

    def test_control_mode_can_apply_recommended_candidate_lane_explicitly(self) -> None:
        artifacts = build_candidate_generation_artifacts(
            [
                self._field("news_sentiment_extreme", "News sentiment and media attention score", dataset="news12"),
                self._field("quality_cashflow_score", "Cashflow quality and accrual quality score", dataset="model77"),
            ],
            policy_feedback={
                "budget_policy_actions": [
                    {
                        "diagnosis_type": "overcrowded_skeleton",
                        "budget_action": "allocate_controlled_repair_budget",
                        "max_budget_share": 0.15,
                    }
                ]
            },
            policy_feedback_mode="control",
        )

        self.assertTrue(artifacts["candidate_hypothesis_queue"]["hypotheses"])
        self.assertTrue(
            all(
                row["wqb_action_lane"] == "repair_probe"
                for row in artifacts["candidate_hypothesis_queue"]["hypotheses"]
            )
        )

    def test_cli_writes_three_named_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fields_path = root / "fields.json"
            output_dir = root / "out"
            fields_path.write_text(
                json.dumps(
                    {
                        "fields": [
                            self._field("eps_revision_surprise", "Analyst EPS estimate revision", dataset="analyst4"),
                            self._field("quality_cashflow_score", "Cashflow quality", dataset="model77"),
                            self._field("news_sentiment_extreme", "News sentiment", dataset="news12"),
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.research.build_behavioral_candidate_generation",
                    "--fields",
                    str(fields_path),
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((output_dir / "behavioral_mechanism_inventory.json").exists())
            self.assertTrue((output_dir / "behavioral_proxy_field_map.json").exists())
            self.assertTrue((output_dir / "candidate_hypothesis_queue.json").exists())
            self.assertIn("candidate_hypothesis_queue", completed.stdout)

    def test_cli_accepts_output_evaluation_report_for_policy_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fields_path = root / "fields.json"
            report_path = root / "output_evaluation_report.json"
            output_dir = root / "out"
            fields_path.write_text(
                json.dumps(
                    {
                        "fields": [
                            self._field("news_sentiment_extreme", "News sentiment and media attention score", dataset="news12"),
                            self._field("quality_cashflow_score", "Cashflow quality", dataset="model77"),
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report_path.write_text(
                json.dumps(
                    {
                        "budget_policy_actions": [
                            {
                                "diagnosis_type": "overcrowded_skeleton",
                                "budget_action": "allocate_controlled_repair_budget",
                                "max_budget_share": 0.15,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.research.build_behavioral_candidate_generation",
                    "--fields",
                    str(fields_path),
                    "--output-dir",
                    str(output_dir),
                    "--output-evaluation-report",
                    str(report_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            queue = json.loads((output_dir / "candidate_hypothesis_queue.json").read_text(encoding="utf-8"))
            self.assertTrue(queue["policy_feedback"]["budget_actions"])
            self.assertTrue(all(row["policy_feedback"]["requires_chassis_change"] for row in queue["hypotheses"]))

    def _field(self, field_id: str, description: str, *, dataset: str) -> dict[str, object]:
        return {
            "id": field_id,
            "description": description,
            "dataset_id": dataset,
            "dataset_name": dataset,
            "coverage": 1.0,
            "userCount": 10,
            "alphaCount": 20,
        }


if __name__ == "__main__":
    unittest.main()
