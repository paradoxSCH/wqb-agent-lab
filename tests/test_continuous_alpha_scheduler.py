from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from src.continuous_alpha_scheduler import (
    ContinuousAlphaScheduler,
    _dataset_bandit_score,
    _chassis_signature,
    _is_structural_field,
    _pass_row,
    _recent_dataset_dead_zone,
    _route_decision,
    _supplement_rule_based_families,
)
from src.atomic_json import locked_atomic_json_merge
from src.llm_template_generator import LLMTemplateGenerator
from src.llm_planning import LLMPlanAdapter
from src.llm_provider import (
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    llm_config_identity,
    resolve_llm_provider_config,
)

os.environ.setdefault("WQB_DISABLE_LLM_TEMPLATE_BACKEND", "1")


class ContinuousAlphaSchedulerTests(unittest.TestCase):
    def test_scheduler_digest_ignores_credential_availability_and_value(self) -> None:
        config = {
            "llm_provider": {
                "provider": "openai_compatible",
                "model": "scheduler-model",
                "api_key_env": "SCHEDULER_API_KEY",
            }
        }
        digests: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, env in enumerate(
                ({}, {"SCHEDULER_API_KEY": "one"}, {"SCHEDULER_API_KEY": "two"})
            ):
                state_path = root / f"run-{index}" / "iteration_state.json"
                self._write_json(state_path, {"run_tag": f"run-{index}"})
                with patch.dict(os.environ, env, clear=True):
                    scheduler = ContinuousAlphaScheduler(
                        root,
                        state_path,
                        workflow_config=config,
                    )
                digests.append(scheduler.llm_provider_config_digest)

        self.assertEqual([digests[0], digests[0], digests[0]], digests)

    def test_stopped_workflow_does_not_advance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            self._write_json(
                state_path,
                {
                    "run_tag": "test-run",
                    "workflow_status": "stopped",
                    "current_iteration": 1,
                    "current_stage": "family_generation",
                },
            )

            scheduler = ContinuousAlphaScheduler(root, state_path)
            result = scheduler.step()

            self.assertFalse(result.advanced)
            self.assertIn("stopped", result.summary)

    def test_run_scan_command_uses_concurrency_three(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
            self._write_json(state_path, {"run_tag": "test-run"})
            scheduler = ContinuousAlphaScheduler(root, state_path)

            with patch("src.continuous_alpha_scheduler.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                scheduler._run_scan_command(root / "scan_config.json")

            command = run_mock.call_args.args[0]
            self.assertIn("--max-concurrency", command)
            index = command.index("--max-concurrency")
            self.assertEqual(command[index + 1], "3")

    def test_self_corr_route_uses_value_bucket(self) -> None:
        mild = {
            "metrics": {"sharpe": 1.7, "fitness": 1.2, "turnover": 0.12},
            "checks": [{"name": "SELF_CORRELATION", "result": "FAIL", "limit": 0.7, "value": 0.715}],
        }
        not_near = {
            "metrics": {"sharpe": 1.7, "fitness": 1.2, "turnover": 0.12},
            "checks": [{"name": "SELF_CORRELATION", "result": "FAIL", "limit": 0.7, "value": 0.73}],
        }
        extreme = {
            "metrics": {"sharpe": 1.7, "fitness": 1.2, "turnover": 0.12},
            "checks": [{"name": "SELF_CORRELATION", "result": "FAIL", "limit": 0.7, "value": 0.94}],
        }

        self.assertEqual(_route_decision(mild), "self_corr_light_repair")
        self.assertEqual(_route_decision(not_near), "self_corr_escape")
        self.assertEqual(_route_decision(extreme), "replace_overcrowded_signal")

    def test_quality_failure_routes_use_value_buckets(self) -> None:
        severe_sub_universe = {
            "metrics": {"sharpe": 1.7, "fitness": 1.2, "turnover": 0.12},
            "checks": [{"name": "LOW_SUB_UNIVERSE_SHARPE", "result": "FAIL", "limit": 0.70, "value": 0.21}],
        }
        near_weak_signal = {
            "metrics": {"sharpe": 1.18, "fitness": 0.93, "turnover": 0.12},
            "checks": [{"name": "LOW_SHARPE", "result": "FAIL", "limit": 1.25, "value": 1.18}],
        }
        deep_weak_signal = {
            "metrics": {"sharpe": 0.62, "fitness": 0.25, "turnover": 0.12},
            "checks": [{"name": "LOW_SHARPE", "result": "FAIL", "limit": 1.25, "value": 0.62}],
        }
        concentrated_weight = {
            "metrics": {"sharpe": 1.7, "fitness": 1.2, "turnover": 0.12},
            "checks": [{"name": "CONCENTRATED_WEIGHT", "result": "FAIL", "limit": 0.10, "value": 0.24}],
        }

        self.assertEqual(_route_decision(severe_sub_universe), "replace_unstable_universe_proxy")
        self.assertEqual(_route_decision(near_weak_signal), "local_parameter_optimization")
        self.assertEqual(_route_decision(deep_weak_signal), "replace_weak_behavior_proxy")
        self.assertEqual(_route_decision(concentrated_weight), "replace_concentrated_expression_structure")

    def test_supplemental_generation_caps_chassis(self) -> None:
        selected_fields = [
            {
                "field_id": f"option_breakeven_{window}",
                "dataset_id": "option9",
                "category": "option",
                "description": "option breakeven price",
            }
            for window in (10, 20, 30, 60, 90, 180, 360)
        ]

        families = _supplement_rule_based_families(
            [],
            selected_fields=selected_fields,
            dataset="option9",
            blocked_skeletons=set(),
            blocked_chassis=set(),
            kept_fields=[],
            target_count=24,
            archetype="test",
            reason="test",
        )

        self.assertGreaterEqual(len(families), 12)
        chassis_counts = Counter(
            _chassis_signature(item["expression"], item.get("fields", []))
            for item in families
        )
        self.assertLessEqual(max(chassis_counts.values()), 3)

    def test_grading_holds_failed_live_recheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            self._write_json(state_path, {"run_tag": "test-run", "current_iteration": 1, "current_stage": "grading", "active_stage_inputs": {}})
            passing_row = {
                "alpha_id": "A1",
                "skeleton": "winner",
                "expression": "group_rank(rank(-returns) / 10 + pcr_oi_20 / 10, industry)",
                "settings": {"decay": 6},
                "metrics": {"sharpe": 1.7, "fitness": 1.1, "turnover": 0.2, "returns": 0.08, "drawdown": 0.04},
                "checks": [
                    {"name": "LOW_SHARPE", "result": "PASS"},
                    {"name": "LOW_FITNESS", "result": "PASS"},
                    {"name": "HIGH_TURNOVER", "result": "PASS"},
                    {"name": "LOW_TURNOVER", "result": "PASS"},
                    {"name": "SELF_CORRELATION", "result": "PASS", "value": 0.61, "limit": 0.7},
                ],
            }
            held_row = {**passing_row, "checks": [{"name": "SELF_CORRELATION", "result": "PENDING"}], "live_check_status": "checked"}
            self._write_json(run_dir / "direct_submit.json", [passing_row])
            scheduler = ContinuousAlphaScheduler(root, state_path)
            scheduler._live_recheck_pass_rows = lambda rows: ([], [held_row])  # type: ignore[method-assign]

            result = scheduler.step()

            self.assertTrue(result.advanced)
            tiers = json.loads((run_dir / "submission_tiers.json").read_text(encoding="utf-8"))
            self.assertEqual(tiers["tier_1"], [])
            self.assertEqual(tiers["pending_transport_retries"][0]["alpha_id"], "A1")

    def test_family_generation_respects_forced_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_json(
                root / ".local" / "data" / "field_quadrant_analysis.json",
                [
                    {
                        "field_id": "snt_buzz_fast_d1",
                        "dataset_id": "socialmedia12",
                        "category": "socialmedia",
                        "description": "negative sentiment buzz",
                        "crowding_score": 10.0,
                        "research_value_score": 85.0,
                        "alpha_count": 20,
                        "quadrant": "Q1 高价值低拥挤",
                    },
                    {
                        "field_id": "actual_sales_value_quarterly",
                        "dataset_id": "analyst4",
                        "category": "analyst",
                        "description": "actual quarterly sales",
                        "crowding_score": 18.0,
                        "research_value_score": 75.0,
                        "alpha_count": 147,
                        "quadrant": "Q1 高价值低拥挤",
                    },
                ],
            )
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            self._write_json(
                state_path,
                {
                    "run_tag": "test-run",
                    "region": "USA",
                    "delay": 1,
                    "universe": "TOP3000",
                    "current_iteration": 1,
                    "current_stage": "family_generation",
                    "dataset_preferences": {
                        "forced_dataset": "analyst4",
                        "exclude_datasets": ["socialmedia12"],
                    },
                    "completed_stages": [],
                    "active_stage_inputs": {},
                    "state_files": {},
                },
            )
            self._write_json(run_dir / "alpha_skeleton_blocklist.json", [])

            scheduler = ContinuousAlphaScheduler(root, state_path)
            result = scheduler.step()

            self.assertTrue(result.advanced)
            candidate_families = json.loads((run_dir / "candidate_families.json").read_text(encoding="utf-8"))
            self.assertTrue(candidate_families)
            self.assertTrue(all(family["dataset"] == "analyst4" for family in candidate_families))

    def test_family_generation_prepares_scan_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_json(
                root / ".local" / "data" / "field_quadrant_analysis.json",
                [
                    {
                        "field_id": "actual_sales_value_quarterly",
                        "dataset_id": "analyst4",
                        "category": "analyst",
                        "description": "actual quarterly sales",
                        "crowding_score": 19.0,
                        "research_value_score": 75.0,
                        "alpha_count": 147,
                        "quadrant": "Q1 高价值低拥挤",
                    }
                ],
            )
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            self._write_json(
                state_path,
                {
                    "run_tag": "test-run",
                    "region": "USA",
                    "delay": 1,
                    "universe": "TOP3000",
                    "current_iteration": 1,
                    "current_stage": "family_generation",
                    "completed_stages": [],
                    "active_stage_inputs": {},
                    "state_files": {},
                },
            )
            self._write_json(run_dir / "alpha_skeleton_blocklist.json", [])

            scheduler = ContinuousAlphaScheduler(root, state_path)
            result = scheduler.step()

            self.assertTrue(result.advanced)
            updated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["current_stage"], "scan")
            candidate_families = json.loads((run_dir / "candidate_families.json").read_text(encoding="utf-8"))
            self.assertTrue(candidate_families)
            self.assertTrue((run_dir / "candidate_families_round1.json").exists())
            self.assertTrue((run_dir / "field_pool_round1.json").exists())
            field_pool = json.loads((run_dir / "field_pool.json").read_text(encoding="utf-8"))
            self.assertIn("dataset_score_table", field_pool)
            self.assertIn("diversity_suggestions", field_pool)
            decision_trace = json.loads((run_dir / "decision_trace_round1.json").read_text(encoding="utf-8"))
            self.assertEqual(decision_trace["steps"][0]["stage"], "family_generation")
            scan_config = json.loads(
                (root / "scan_configs" / "workflow" / "continuous-alpha" / "test-run" / "scan_config_round1.json").read_text(encoding="utf-8")
            )
            self.assertTrue(scan_config["continue_on_pass"])
            self.assertTrue(scan_config["candidates"])

    def test_triage_moves_to_optimization_when_near_pass_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            self._write_json(
                state_path,
                {
                    "run_tag": "test-run",
                    "current_iteration": 3,
                    "current_stage": "triage",
                    "completed_stages": [],
                    "active_stage_inputs": {
                        "latest_scan_output": ".local/data/workflow/continuous-alpha/test-run/scan_round3_iteration3.json"
                    },
                    "state_files": {},
                },
            )
            self._write_json(
                run_dir / "candidate_families.json",
                [
                    {
                        "dataset": "analyst4",
                        "family": "Actual quarterly sales by cap",
                        "skeleton": "actual-sales-quarterly-cap",
                        "expression": "group_rank(actual_sales_value_quarterly / cap, subindustry)",
                        "fields": ["actual_sales_value_quarterly"],
                        "chassis": "group_rank(FIELD / cap, subindustry)",
                    }
                ],
            )
            self._write_json(run_dir / "alpha_skeleton_blocklist.json", [])
            self._write_json(run_dir / "low_value_avoid.json", [])
            self._write_json(
                run_dir / "scan_round3_iteration3.json",
                [
                    {
                        "alpha_id": "pw13G1Wq",
                        "expression": "group_rank(actual_sales_value_quarterly / cap, subindustry)",
                        "settings": {"decay": 6, "neutralization": "MARKET"},
                        "note": "iteration3 actual-sales-quarterly-cap",
                        "metrics": {"sharpe": 1.22, "fitness": 1.12, "turnover": 0.02, "returns": 0.10, "drawdown": 0.14},
                        "checks": [{"name": "LOW_SHARPE", "result": "FAIL"}],
                    }
                ],
            )

            scheduler = ContinuousAlphaScheduler(root, state_path)
            result = scheduler.step()

            self.assertTrue(result.advanced)
            updated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["current_stage"], "optimization")
            optimize_next = json.loads((run_dir / "optimize_next.json").read_text(encoding="utf-8"))
            self.assertEqual(len(optimize_next), 1)
            self.assertEqual(optimize_next[0]["skeleton"], "actual-sales-quarterly-cap")
            field_scoreboard = json.loads((run_dir / "field_scoreboard.json").read_text(encoding="utf-8"))
            dataset_scoreboard = json.loads((run_dir / "dataset_scoreboard.json").read_text(encoding="utf-8"))
            chassis_scoreboard = json.loads((run_dir / "chassis_scoreboard.json").read_text(encoding="utf-8"))
            self.assertEqual(field_scoreboard["actual_sales_value_quarterly"]["near_pass_count"], 1)
            self.assertEqual(dataset_scoreboard["analyst4"]["near_pass_count"], 1)
            self.assertTrue(chassis_scoreboard)
            self.assertEqual(optimize_next[0]["route_decision"], "local_parameter_optimization")
            self.assertIn("failed_checks", optimize_next[0])
            knowledge = json.loads((run_dir / "knowledge_base.json").read_text(encoding="utf-8"))
            self.assertTrue(knowledge["success_patterns"])
            trace = json.loads((run_dir / "decision_trace_round3.json").read_text(encoding="utf-8"))
            self.assertEqual(trace["steps"][0]["stage"], "triage")

    def test_scan_stage_waits_for_complete_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            scan_dir = root / "scan_configs" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            config_path = scan_dir / "scan_config_round2.json"
            output_path = run_dir / "scan_round2_iteration2.json"
            self._write_json(
                state_path,
                {
                    "run_tag": "test-run",
                    "current_iteration": 2,
                    "current_stage": "scan",
                    "completed_stages": [],
                    "active_stage_inputs": {
                        "next_scan_config": "scan_configs/workflow/continuous-alpha/test-run/scan_config_round2.json",
                        "next_scan_output": ".local/data/workflow/continuous-alpha/test-run/scan_round2_iteration2.json",
                    },
                    "state_files": {},
                },
            )
            self._write_json(
                config_path,
                {
                    "output": ".local/data/workflow/continuous-alpha/test-run/scan_round2_iteration2.json",
                    "candidates": [
                        {"expression": "group_rank(alpha_a / cap, subindustry)", "note": "iteration2 alpha-a"},
                        {"expression": "group_rank(alpha_b / cap, subindustry)", "note": "iteration2 alpha-b"},
                    ],
                },
            )
            self._write_json(
                output_path,
                [
                    {
                        "alpha_id": "A1",
                        "expression": "group_rank(alpha_a / cap, subindustry)",
                        "metrics": {"sharpe": 1.0, "fitness": 0.9, "turnover": 0.1},
                        "checks": [],
                    }
                ],
            )

            scheduler = ContinuousAlphaScheduler(root, state_path)
            scheduler._run_scan_command = lambda _config: None  # type: ignore[method-assign]
            result = scheduler.step()

            self.assertFalse(result.advanced)
            updated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["current_stage"], "scan")
            self.assertEqual(updated["active_stage_inputs"]["scan_status"], "waiting_for_completion")
            self.assertEqual(updated["completed_stages"], [])

    def test_self_corr_failures_do_not_get_decay_sweeps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            self._write_json(
                state_path,
                {
                    "run_tag": "test-run",
                    "current_iteration": 2,
                    "current_stage": "optimization",
                    "completed_stages": [],
                    "active_stage_inputs": {},
                    "state_files": {},
                },
            )
            scheduler = ContinuousAlphaScheduler(root, state_path)
            candidates = scheduler._build_optimization_candidates(
                1,
                [],
                [
                    {
                        "alpha_id": "SC1",
                        "dataset": "analyst4",
                        "family": "Analyst reversal clone",
                        "skeleton": "analyst-reversal-clone",
                        "expression": "group_rank(rank(-returns) / 10 + actual_sales_value_quarterly / cap / 10, industry)",
                        "fields": ["actual_sales_value_quarterly"],
                        "settings": {"decay": 6, "neutralization": "MARKET"},
                        "metrics": {"sharpe": 1.4, "fitness": 1.1, "turnover": 0.2},
                        "checks": [{"name": "SELF_CORRELATION", "result": "FAIL"}],
                    }
                ],
            )
            self.assertFalse([item for item in candidates if item["axis"] == "decay"])
            self.assertTrue([item for item in candidates if item["axis"] == "self_corr_escape"])

    def test_low_sharpe_below_threshold_gets_no_optimization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            self._write_json(state_path, {"run_tag": "test-run", "current_iteration": 2, "current_stage": "optimization"})
            scheduler = ContinuousAlphaScheduler(root, state_path)
            candidates = scheduler._build_optimization_candidates(
                1,
                [],
                [
                    {
                        "alpha_id": "LS1",
                        "dataset": "analyst4",
                        "family": "Weak analyst",
                        "skeleton": "weak-analyst",
                        "expression": "group_rank(actual_sales_value_quarterly / cap, subindustry)",
                        "fields": ["actual_sales_value_quarterly"],
                        "settings": {"decay": 6, "neutralization": "MARKET"},
                        "metrics": {"sharpe": 0.95, "fitness": 0.80, "turnover": 0.2},
                        "checks": [{"name": "LOW_SHARPE", "result": "FAIL"}],
                    }
                ],
            )
            self.assertEqual(candidates, [])

    def test_dataset_bandit_cools_invalid_dataset(self) -> None:
        rows = [
            {"research_value_score": 99.0, "crowding_score": 1.0},
            {"research_value_score": 95.0, "crowding_score": 2.0},
        ]
        bad_score = _dataset_bandit_score(
            priority=0,
            dataset="analyst4",
            rows=rows,
            dataset_stats={"scanned_count": 2, "terminal_error_count": 2, "last_seen_iteration": 4},
            iteration=5,
            blocked_skeletons=set(),
        )
        good_score = _dataset_bandit_score(
            priority=0,
            dataset="fundamental6",
            rows=[{"research_value_score": 60.0, "crowding_score": 5.0}],
            dataset_stats={"scanned_count": 4, "near_pass_count": 2, "best_sharpe": 1.3, "best_fitness": 1.1, "last_seen_iteration": 2},
            iteration=5,
            blocked_skeletons=set(),
        )
        self.assertLess(bad_score[1], good_score[1])

    def test_structural_filter_blocks_universe_and_currency_fields(self) -> None:
        self.assertTrue(_is_structural_field({"field_id": "top1000", "description": "universe membership"})[0])
        self.assertTrue(_is_structural_field({"field_id": "actuals_value_currency_code", "description": "pricing currency code"})[0])

    def test_pass_row_blocks_units_warning_and_self_corr_pending(self) -> None:
        base_row = {
            "metrics": {"sharpe": 1.5, "fitness": 1.2, "turnover": 0.2},
            "checks": [
                {"name": "LOW_SHARPE", "result": "PASS"},
                {"name": "LOW_FITNESS", "result": "PASS"},
            ],
        }
        self.assertTrue(_pass_row(base_row))
        self.assertFalse(
            _pass_row({**base_row, "checks": [*base_row["checks"], {"name": "UNITS", "result": "WARNING"}]})
        )
        self.assertFalse(
            _pass_row({**base_row, "checks": [*base_row["checks"], {"name": "SELF_CORRELATION", "result": "PENDING"}]})
        )

    def test_recent_dataset_dead_zone_detects_exhausted_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state = {"completed_stages": []}
            for iteration in range(1, 5):
                state["completed_stages"].append(
                    {
                        "iteration": iteration,
                        "stage": "family_generation",
                        "chosen_bucket": "Q1 analyst4 autogenerated branch",
                    }
                )
                state["completed_stages"].append(
                    {
                        "iteration": iteration,
                        "stage": "scan",
                        "scan_output": f".local/data/workflow/continuous-alpha/test-run/scan_round{iteration}_iteration{iteration}.json",
                    }
                )
                self._write_json(
                    run_dir / f"scan_round{iteration}_iteration{iteration}.json",
                    [
                        {
                            "alpha_id": f"weak-{iteration}-{index}",
                            "metrics": {"sharpe": 0.8, "fitness": 0.6, "turnover": 0.1},
                            "checks": [
                                {"name": "LOW_SHARPE", "result": "FAIL"},
                                {"name": "LOW_FITNESS", "result": "FAIL"},
                            ],
                        }
                        for index in range(3)
                    ],
                )

            self.assertTrue(_recent_dataset_dead_zone(root, state, "analyst4"))

    def test_triage_dedupes_low_value_avoid_by_skeleton(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            self._write_json(
                state_path,
                {
                    "run_tag": "test-run",
                    "current_iteration": 3,
                    "current_stage": "triage",
                    "completed_stages": [],
                    "active_stage_inputs": {
                        "latest_scan_output": ".local/data/workflow/continuous-alpha/test-run/scan_round3_iteration3.json"
                    },
                    "state_files": {},
                },
            )
            self._write_json(
                run_dir / "candidate_families.json",
                [
                    {
                        "dataset": "analyst4",
                        "family": "Actual quarterly sales by cap",
                        "skeleton": "actual-sales-quarterly-cap",
                        "expression": "group_rank(actual_sales_value_quarterly / cap, subindustry)",
                    }
                ],
            )
            self._write_json(run_dir / "alpha_skeleton_blocklist.json", [])
            self._write_json(
                run_dir / "low_value_avoid.json",
                [
                    {
                        "dataset": "analyst4",
                        "family": "Actual quarterly sales by cap",
                        "skeleton": "actual-sales-quarterly-cap",
                        "reason": "Existing low-value marker",
                        "avoid_mode": "do_not_regenerate_unchanged",
                        "blocked_in_iteration": 2,
                        "representative_alphas": ["pw13G1Wq"],
                    }
                ],
            )
            self._write_json(
                run_dir / "scan_round3_iteration3.json",
                [
                    {
                        "alpha_id": "pw13G1Wq",
                        "expression": "group_rank(actual_sales_value_quarterly / cap, subindustry)",
                        "settings": {"decay": 6, "neutralization": "MARKET"},
                        "note": "iteration3 actual-sales-quarterly-cap",
                        "metrics": {"sharpe": 0.82, "fitness": 0.61, "turnover": 0.05, "returns": 0.03, "drawdown": 0.20},
                        "checks": [{"name": "LOW_SHARPE", "result": "FAIL"}],
                    }
                ],
            )

            scheduler = ContinuousAlphaScheduler(root, state_path)
            result = scheduler.step()

            self.assertTrue(result.advanced)
            low_value = json.loads((run_dir / "low_value_avoid.json").read_text(encoding="utf-8"))
            self.assertEqual(len(low_value), 1)
            updated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["current_iteration"], 4)
            self.assertEqual(updated["current_stage"], "family_generation")

    def test_triage_blocks_submission_risky_chassis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            self._write_json(
                state_path,
                {
                    "run_tag": "test-run",
                    "current_iteration": 3,
                    "current_stage": "triage",
                    "completed_stages": [],
                    "active_stage_inputs": {
                        "latest_scan_output": ".local/data/workflow/continuous-alpha/test-run/scan_round3_iteration3.json"
                    },
                    "state_files": {},
                },
            )
            self._write_json(
                run_dir / "candidate_families.json",
                [
                    {
                        "dataset": "analyst4",
                        "family": "Unit-risk blend",
                        "skeleton": "unit-risk-blend",
                        "expression": "group_rank(rank(-returns) / 10 + anl4_af_div_value / cap / 10, industry)",
                        "fields": ["anl4_af_div_value"],
                    }
                ],
            )
            self._write_json(run_dir / "alpha_skeleton_blocklist.json", [])
            self._write_json(run_dir / "chassis_blocklist.json", [])
            self._write_json(run_dir / "low_value_avoid.json", [])
            self._write_json(
                run_dir / "scan_round3_iteration3.json",
                [
                    {
                        "alpha_id": "WARN1",
                        "expression": "group_rank(rank(-returns) / 10 + anl4_af_div_value / cap / 10, industry)",
                        "settings": {"decay": 6, "neutralization": "MARKET"},
                        "note": "iteration3 unit-risk-blend",
                        "metrics": {"sharpe": 1.5, "fitness": 1.2, "turnover": 0.2, "returns": 0.12, "drawdown": 0.06},
                        "checks": [{"name": "UNITS", "result": "WARNING"}],
                    }
                ],
            )

            scheduler = ContinuousAlphaScheduler(root, state_path)
            result = scheduler.step()

            self.assertTrue(result.advanced)
            self.assertFalse(json.loads((run_dir / "direct_submit.json").read_text(encoding="utf-8")))
            chassis_blocklist = json.loads((run_dir / "chassis_blocklist.json").read_text(encoding="utf-8"))
            self.assertEqual(len(chassis_blocklist), 1)
            self.assertEqual(chassis_blocklist[0]["status"], "blocked_submission_risk")

    def test_optimization_stage_waits_for_complete_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            self._write_json(
                state_path,
                {
                    "run_tag": "test-run",
                    "current_iteration": 2,
                    "current_stage": "optimization",
                    "completed_stages": [],
                    "active_stage_inputs": {},
                    "state_files": {},
                },
            )
            self._write_json(
                run_dir / "direct_submit.json",
                [
                    {
                        "alpha_id": "ZYWMJ6M3",
                        "dataset": "analyst4",
                        "family": "Analyst EPS blend",
                        "skeleton": "analyst-eps-level-price",
                        "expression": "group_rank(anl4_afv4_eps_high / close, subindustry)",
                        "settings": {"decay": 6, "neutralization": "MARKET"},
                        "metrics": {"sharpe": 1.29, "fitness": 1.09, "turnover": 0.0268, "returns": 0.0896, "drawdown": 0.0964},
                        "checks": [],
                    }
                ],
            )
            self._write_json(run_dir / "optimize_next.json", [])
            self._write_json(
                run_dir / "optimization_round1.json",
                [
                    {
                        "alpha_id": "probe-1",
                        "expression": "group_rank(anl4_afv4_eps_high / close, subindustry)",
                        "note": "optimization1 analyst-eps-level-price decay4",
                        "metrics": {"sharpe": 1.10, "fitness": 0.95, "turnover": 0.03},
                        "checks": [{"name": "LOW_SHARPE", "result": "FAIL"}],
                    }
                ],
            )

            scheduler = ContinuousAlphaScheduler(root, state_path)
            scheduler._run_scan_command = lambda _config: None  # type: ignore[method-assign]
            result = scheduler.step()

            self.assertFalse(result.advanced)
            updated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["current_stage"], "optimization")
            self.assertEqual(updated["active_stage_inputs"]["optimization_status"], "waiting_for_completion")
            self.assertEqual(updated["completed_stages"], [])

    def test_grading_advances_to_next_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            state_path = run_dir / "iteration_state.json"
            self._write_json(
                state_path,
                {
                    "run_tag": "test-run",
                    "current_iteration": 5,
                    "current_stage": "grading",
                    "completed_stages": [],
                    "active_stage_inputs": {
                        "optimization_output": ".local/data/workflow/continuous-alpha/test-run/optimization_round1.json"
                    },
                    "state_files": {},
                },
            )
            self._write_json(
                run_dir / "direct_submit.json",
                [
                    {
                        "alpha_id": "ZYWMJ6M3",
                        "skeleton": "analyst-eps-level-price",
                        "expression": "group_rank(anl4_afv4_eps_high / close, subindustry)",
                        "settings": {"decay": 6, "neutralization": "MARKET"},
                        "metrics": {"sharpe": 1.29, "fitness": 1.09, "turnover": 0.0268, "returns": 0.0896, "drawdown": 0.0964},
                        "checks": [],
                    }
                ],
            )
            self._write_json(run_dir / "optimization_candidates.json", [])
            self._write_json(run_dir / "optimization_round1.json", [])

            scheduler = ContinuousAlphaScheduler(root, state_path)
            result = scheduler.step()

            self.assertTrue(result.advanced)
            updated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["current_iteration"], 6)
            self.assertEqual(updated["current_stage"], "family_generation")
            submission_tiers = json.loads((run_dir / "submission_tiers_round5.json").read_text(encoding="utf-8"))
            self.assertEqual(len(submission_tiers["tier_1"]), 1)
            self.assertIn("settings", submission_tiers["tier_1"][0])
            self.assertIn("metrics", submission_tiers["tier_1"][0])
            best_parameters = json.loads((run_dir / "best_parameters.json").read_text(encoding="utf-8"))
            self.assertEqual(best_parameters["best_by_skeleton"][0]["settings"]["decay"], 6)
            stable_submission_tiers = json.loads((run_dir / "submission_tiers.json").read_text(encoding="utf-8"))
            self.assertEqual(stable_submission_tiers, submission_tiers)
            self.assertEqual(
                updated["state_files"]["submission_tiers"],
                ".local/data/workflow/continuous-alpha/test-run/submission_tiers.json",
            )

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


class LLMTemplateGeneratorTests(unittest.TestCase):
    class RecordingProvider:
        provider_id = "test"
        model = "test-model"

        def __init__(self, content: str) -> None:
            self.content = content
            self.requests: list[LLMRequest] = []

        def complete(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            return LLMResponse(
                content=self.content,
                provider=self.provider_id,
                model=self.model,
                usage=LLMUsage(),
            )

    class FailingProvider:
        provider_id = "test"
        model = "test-model"

        def complete(self, request: LLMRequest) -> LLMResponse:
            raise LLMProviderError(
                code="rate_limited",
                message="retry after token-secret",
                provider=self.provider_id,
                model=self.model,
                retryable=True,
                secrets=("token-secret",),
            )

    def test_template_generator_uses_injected_provider_once(self) -> None:
        provider = self.RecordingProvider(
            json.dumps(
                [
                    {
                        "family": "Temporal operating income",
                        "skeleton": "temporal-operating-income",
                        "signal_idea": "operating income change",
                        "expression": "group_rank(ts_delta(operating_income, 20) / cap, industry)",
                        "reason": "Use a temporal chassis.",
                        "archetype": "temporal",
                    }
                ]
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            self._write_json(
                root / ".local" / "data" / "field_quadrant_analysis.json",
                [{"field_id": "operating_income", "dataset_id": "fundamental6"}],
            )
            self._write_json(run_dir / "alpha_skeleton_blocklist.json", [])

            families = LLMTemplateGenerator(provider=provider).generate(
                workspace_root=root,
                run_dir=run_dir,
                selected_dataset="fundamental6",
                selected_fields=[
                    {
                        "field_id": "operating_income",
                        "dataset_id": "fundamental6",
                        "category": "fundamental",
                        "description": "operating income",
                    }
                ],
                max_families=5,
            )

        self.assertEqual(1, len(provider.requests))
        self.assertEqual("json", provider.requests[0].response_format)
        self.assertTrue(any(row["skeleton"] == "temporal-operating-income" for row in families))

    def test_template_generator_without_provider_is_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            self._write_json(
                root / ".local" / "data" / "field_quadrant_analysis.json",
                [{"field_id": "operating_income", "dataset_id": "fundamental6"}],
            )
            self._write_json(run_dir / "alpha_skeleton_blocklist.json", [])
            generator = LLMTemplateGenerator(provider=None)

            with patch("src.llm_provider.registry.create_llm_provider") as create:
                families = generator.generate(
                    workspace_root=root,
                    run_dir=run_dir,
                    selected_dataset="fundamental6",
                    selected_fields=[{"field_id": "operating_income", "description": "operating income"}],
                    max_families=3,
                )

        create.assert_not_called()
        self.assertTrue(families)
        self.assertIsNone(generator.last_diagnostic)

    def test_provider_error_keeps_deterministic_candidates_and_structured_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            self._write_json(
                root / ".local" / "data" / "field_quadrant_analysis.json",
                [{"field_id": "operating_income", "dataset_id": "fundamental6"}],
            )
            self._write_json(run_dir / "alpha_skeleton_blocklist.json", [])
            generator = LLMTemplateGenerator(provider=self.FailingProvider())

            families = generator.generate(
                workspace_root=root,
                run_dir=run_dir,
                selected_dataset="fundamental6",
                selected_fields=[{"field_id": "operating_income", "description": "operating income"}],
                max_families=3,
            )

        self.assertTrue(families)
        self.assertEqual("rate_limited", generator.last_diagnostic["code"])
        self.assertTrue(generator.last_diagnostic["retryable"])
        self.assertNotIn("token-secret", json.dumps(generator.last_diagnostic))

    def test_scheduler_resolves_provider_once_and_reuses_instance_for_generators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
            config_path = root / "workflow.json"
            self._write_json(state_path, {"run_tag": "test-run"})
            self._write_json(config_path, {"llm_provider": {"provider": "ollama", "model": "qwen-test"}})
            provider = self.RecordingProvider("[]")
            resolved = resolve_llm_provider_config(
                {"llm_provider": {"provider": "ollama", "model": "qwen-test"}},
                env={},
                require_credentials=False,
            )

            with (
                patch("src.continuous_alpha_scheduler.resolve_llm_provider_config", return_value=resolved) as resolve,
                patch("src.continuous_alpha_scheduler.create_llm_provider", return_value=provider) as create,
                patch("src.continuous_alpha_scheduler.LLMTemplateGenerator") as generator_type,
            ):
                scheduler = ContinuousAlphaScheduler(root, state_path, workflow_config=config_path)
                scheduler._create_template_generator()
                scheduler._create_template_generator()

            self.assertEqual(1, resolve.call_count)
            self.assertEqual(1, create.call_count)
            self.assertEqual([provider, provider], [call.kwargs["provider"] for call in generator_type.call_args_list])
            first_digest = scheduler.llm_provider_config_digest

            with (
                patch("src.continuous_alpha_scheduler.resolve_llm_provider_config", return_value=resolved),
                patch("src.continuous_alpha_scheduler.create_llm_provider", return_value=self.RecordingProvider("[]")),
            ):
                second_process = ContinuousAlphaScheduler(root, state_path, workflow_config=config_path)

            self.assertEqual(first_digest, second_process.llm_provider_config_digest)
            self.assertIsNot(scheduler.llm_provider, second_process.llm_provider)

    def test_network_scheduler_resolves_once_and_env_mutation_cannot_split_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "captured-key"},
            clear=True,
        ):
            root = Path(tmp)
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
            config = {
                "llm_provider": {
                    "provider": "anthropic",
                    "model": "claude-test",
                    "api_key_env": "ANTHROPIC_API_KEY",
                }
            }
            self._write_json(state_path, {"run_tag": "test-run"})
            provider = self.RecordingProvider("[]")
            captured_resolved: list[object] = []

            def create_once(resolved: object, *, workspace_root: Path) -> object:
                captured_resolved.append(resolved)
                os.environ["ANTHROPIC_API_KEY"] = "mutated-key"
                return provider

            with (
                patch(
                    "src.continuous_alpha_scheduler.resolve_llm_provider_config",
                    wraps=resolve_llm_provider_config,
                ) as resolve,
                patch(
                    "src.continuous_alpha_scheduler.create_llm_provider",
                    side_effect=create_once,
                ) as create,
            ):
                scheduler = ContinuousAlphaScheduler(
                    root,
                    state_path,
                    workflow_config=config,
                )

            self.assertEqual(1, resolve.call_count)
            self.assertEqual(False, resolve.call_args.kwargs["require_credentials"])
            self.assertEqual(1, create.call_count)
            self.assertIs(scheduler.resolved_llm_provider, captured_resolved[0])
            self.assertEqual("captured-key", scheduler.resolved_llm_provider.api_key)
            self.assertEqual("mutated-key", os.environ["ANTHROPIC_API_KEY"])
            self.assertEqual(
                llm_config_identity(scheduler.resolved_llm_provider)["config_digest"],
                scheduler.llm_provider_config_digest,
            )

    def test_scheduler_and_planning_processes_share_effective_config_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
            config = {"llm_provider": {"provider": "ollama", "model": "qwen-test"}}
            self._write_json(state_path, {"run_tag": "test-run", "workflow_config": config})

            scheduler = ContinuousAlphaScheduler(root, state_path)
            planning = LLMPlanAdapter.from_config(config, workspace_root=root)

            self.assertEqual(planning.metadata()["config_digest"], scheduler.llm_provider_config_digest)
            self.assertEqual("ollama", scheduler.state["llm_provider"]["provider"])
            self.assertEqual("qwen-test", scheduler.state["llm_provider"]["model"])
            self.assertIsNot(planning.llm_provider, scheduler.llm_provider)

    def test_missing_anthropic_key_keeps_cross_process_config_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
            config = {
                "llm_provider": {
                    "provider": "anthropic",
                    "model": "claude-test",
                    "api_key_env": "ANTHROPIC_API_KEY",
                }
            }
            self._write_json(state_path, {"run_tag": "test-run"})

            planning = LLMPlanAdapter.from_config(config, workspace_root=root)
            scheduler = ContinuousAlphaScheduler(root, state_path, workflow_config=config)
            planning_metadata = planning.metadata()
            scheduler_metadata = scheduler.state["llm_provider"]

            self.assertEqual("anthropic", planning_metadata["provider"])
            self.assertEqual("anthropic", scheduler_metadata["provider"])
            self.assertEqual("claude-test", planning_metadata["model"])
            self.assertEqual("claude-test", scheduler_metadata["model"])
            self.assertEqual(
                planning_metadata["config_digest"],
                scheduler_metadata["config_digest"],
            )
            self.assertEqual(
                "invalid_configuration",
                planning_metadata["configuration_error"]["code"],
            )
            self.assertEqual(
                "invalid_configuration",
                scheduler_metadata["initialization_diagnostic"]["code"],
            )

    def test_real_scheduler_entry_uses_explicit_planning_workflow_config(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
            config_path = root / "configs" / "workflow.json"
            config = {
                "llm_provider": {
                    "provider": "cli",
                    "model": "local-test",
                    "command": [
                        sys.executable,
                        "-c",
                        "import sys; sys.stdin.read(); print('[]')",
                    ],
                    "prompt_transport": "stdin",
                    "response_format": "json",
                }
            }
            self._write_json(config_path, config)
            self._write_json(
                state_path,
                {
                    "run_tag": "test-run",
                    "current_iteration": 1,
                    "current_stage": "family_generation",
                    "completed_stages": [],
                    "active_stage_inputs": {},
                    "state_files": {},
                },
            )
            self._write_json(
                root / ".local" / "data" / "field_quadrant_analysis.json",
                [
                    {
                        "field_id": "operating_income",
                        "dataset_id": "fundamental6",
                        "category": "fundamental",
                        "description": "operating income",
                        "quadrant": "Q1 high-value",
                    }
                ],
            )
            self._write_json(state_path.parent / "alpha_skeleton_blocklist.json", [])

            completed = subprocess.run(
                [
                    sys.executable,
                    str(repository_root / "continuous_alpha_scheduler.py"),
                    "--workspace-root",
                    str(root),
                    "--state",
                    str(state_path),
                    "--workflow-config",
                    "configs/workflow.json",
                    "--max-stages",
                    "1",
                ],
                cwd=repository_root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            updated = json.loads(state_path.read_text(encoding="utf-8"))
            planning = LLMPlanAdapter.from_config(config, workspace_root=root)
            self.assertEqual(planning.metadata()["provider"], updated["llm_provider"]["provider"])
            self.assertEqual(planning.metadata()["model"], updated["llm_provider"]["model"])
            self.assertEqual(
                planning.metadata()["config_digest"],
                updated["llm_provider"]["config_digest"],
            )
            self.assertEqual("configs/workflow.json", updated["workflow_config_path"])

            conflicting_path = root / "configs" / "other.json"
            self._write_json(conflicting_path, {"llm_provider": {"provider": "disabled"}})
            conflict = subprocess.run(
                [
                    sys.executable,
                    str(repository_root / "continuous_alpha_scheduler.py"),
                    "--workspace-root",
                    str(root),
                    "--state",
                    str(state_path),
                    "--workflow-config",
                    "configs/other.json",
                    "--max-stages",
                    "1",
                ],
                cwd=repository_root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(0, conflict.returncode)
            self.assertIn("conflicts with iteration_state", conflict.stderr)

    def test_real_scheduler_entry_supports_historical_state_and_run_tag_invocations(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            offline_env = {
                key: value
                for key, value in os.environ.items()
                if key
                not in {
                    "ANTHROPIC_API_KEY",
                    "DEEPSEEK_API_KEY",
                    "GEMINI_API_KEY",
                    "KIMI_API_KEY",
                    "MOONSHOT_API_KEY",
                    "OPENAI_API_KEY",
                }
            }
            self._write_json(
                root / ".local" / "data" / "field_quadrant_analysis.json",
                [
                    {
                        "field_id": "operating_income",
                        "dataset_id": "fundamental6",
                        "category": "fundamental",
                        "description": "operating income",
                        "quadrant": "Q1 high-value",
                    }
                ],
            )
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "state-only" / "iteration_state.json"
            self._write_json(state_path, {**self._family_generation_state(), "run_tag": "state-only"})
            self._write_json(state_path.parent / "alpha_skeleton_blocklist.json", [])

            state_only = subprocess.run(
                [
                    sys.executable,
                    str(repository_root / "continuous_alpha_scheduler.py"),
                    "--workspace-root",
                    str(root),
                    "--state",
                    str(state_path),
                    "--max-stages",
                    "1",
                ],
                cwd=repository_root,
                env=offline_env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(0, state_only.returncode, state_only.stderr)
            state_diagnostic = json.loads(
                (state_path.parent / "llm_template_diagnostic.json").read_text(encoding="utf-8")
            )
            self.assertEqual("offline", state_diagnostic["status"])

            run_tag = "run-tag-only"
            run_state = root / ".local" / "data" / "runs" / "continuous-alpha" / run_tag / "iteration_state.json"
            self._write_json(run_state, {**self._family_generation_state(), "run_tag": run_tag})
            self._write_json(run_state.parent / "alpha_skeleton_blocklist.json", [])
            run_tag_only = subprocess.run(
                [
                    sys.executable,
                    str(repository_root / "continuous_alpha_scheduler.py"),
                    "--workspace-root",
                    str(root),
                    "--run-tag",
                    run_tag,
                    "--max-stages",
                    "1",
                ],
                cwd=repository_root,
                env=offline_env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(0, run_tag_only.returncode, run_tag_only.stderr)
            run_diagnostic = json.loads(
                (run_state.parent / "llm_template_diagnostic.json").read_text(encoding="utf-8")
            )
            self.assertEqual("offline", run_diagnostic["status"])

    def test_missing_provider_credential_falls_back_and_persists_initialization_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            state_path = self._write_family_generation_fixture(root)
            config_path = root / "workflow.json"
            self._write_json(
                config_path,
                {
                    "llm_provider": {
                        "provider": "openai_compatible",
                        "model": "test-model",
                        "api_key_env": "MISSING_TEST_KEY",
                    }
                },
            )

            scheduler = ContinuousAlphaScheduler(root, state_path, workflow_config=config_path)
            initialized_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "invalid_configuration",
                initialized_state["llm_provider"]["initialization_diagnostic"]["code"],
            )
            initialized_diagnostic = json.loads(
                (state_path.parent / "llm_template_diagnostic.json").read_text(encoding="utf-8")
            )
            self.assertEqual("error", initialized_diagnostic["status"])
            self.assertEqual("initialization", initialized_diagnostic["phase"])
            result = scheduler.step()

            self.assertTrue(result.advanced)
            self.assertIsNone(scheduler.llm_provider)
            families = json.loads((state_path.parent / "candidate_families.json").read_text(encoding="utf-8"))
            self.assertTrue(families)
            updated = json.loads(state_path.read_text(encoding="utf-8"))
            initialization = updated["llm_provider"]["initialization_diagnostic"]
            self.assertEqual("invalid_configuration", initialization["code"])
            self.assertNotIn('"api_key":', json.dumps(initialization))

    def test_template_diagnostic_overwrites_error_with_offline_and_success_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = self._write_family_generation_fixture(root)
            config_path = root / "workflow.json"
            diagnostic_path = state_path.parent / "llm_template_diagnostic.json"
            self._write_json(
                config_path,
                {
                    "llm_provider": {
                        "provider": "openai_compatible",
                        "model": "test-model",
                        "api_key_env": "MISSING_TEST_KEY",
                    }
                },
            )
            with patch.dict(os.environ, {}, clear=True):
                ContinuousAlphaScheduler(root, state_path, workflow_config=config_path).step()
            error = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            self.assertEqual("error", error["status"])
            self.assertEqual("initialization", error["phase"])
            self.assertIn("timestamp", error)
            self.assertIn("config_digest", error)

            self._write_json(state_path, self._family_generation_state())
            self._write_json(config_path, {"llm_provider": {"provider": "disabled"}})
            ContinuousAlphaScheduler(root, state_path, workflow_config=config_path).step()
            offline = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            self.assertEqual("offline", offline["status"])
            self.assertNotIn("error", offline)
            self.assertNotEqual(error["config_digest"], offline["config_digest"])

            self._write_json(state_path, self._family_generation_state())
            self._write_json(config_path, {"llm_provider": {"provider": "ollama", "model": "qwen-test"}})
            provider = self.RecordingProvider("[]")
            with patch("src.continuous_alpha_scheduler.create_llm_provider", return_value=provider):
                ContinuousAlphaScheduler(root, state_path, workflow_config=config_path).step()
            success = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            self.assertEqual("success", success["status"])
            self.assertNotIn("error", success)
            self.assertEqual(1, len(provider.requests))

    def test_initialization_merge_preserves_newer_state_from_overlapping_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
            config = {"llm_provider": {"provider": "disabled"}}
            self._write_json(
                state_path,
                {
                    **self._family_generation_state(),
                    "counters": {"spent": 1},
                    "external_marker": "initial",
                },
            )
            first = ContinuousAlphaScheduler(root, state_path, workflow_config=config)
            second = ContinuousAlphaScheduler(root, state_path, workflow_config=config)
            current = json.loads(state_path.read_text(encoding="utf-8"))
            current["current_stage"] = "scan"
            current["counters"] = {"spent": 9}
            current["external_marker"] = "newer-writer"
            self._write_json(state_path, current)

            first._persist_initialization_state()
            second._persist_initialization_state()

            merged = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("scan", merged["current_stage"])
            self.assertEqual({"spent": 9}, merged["counters"])
            self.assertEqual("newer-writer", merged["external_marker"])
            self.assertEqual("offline", merged["llm_template_diagnostic"]["status"])

    def test_locked_atomic_merge_preserves_concurrent_top_level_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "iteration_state.json"
            self._write_json(path, {"current_stage": "scan"})

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [
                    executor.submit(locked_atomic_json_merge, path, {f"marker_{index}": index})
                    for index in range(16)
                ]
                for future in futures:
                    future.result()

            merged = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("scan", merged["current_stage"])
            self.assertEqual(
                list(range(16)),
                [merged[f"marker_{index}"] for index in range(16)],
            )

    def test_save_merges_scheduler_delta_without_overwriting_unrelated_external_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
            self._write_json(
                state_path,
                {
                    **self._family_generation_state(),
                    "counters": {"spent": 1},
                    "external_marker": "initial",
                },
            )
            scheduler = ContinuousAlphaScheduler(
                root,
                state_path,
                workflow_config={"llm_provider": {"provider": "disabled"}},
            )
            external = json.loads(state_path.read_text(encoding="utf-8"))
            external["current_stage"] = "scan"
            external["counters"] = {"spent": 9}
            external["external_marker"] = "newer"
            self._write_json(state_path, external)

            scheduler.state["recommended_next_step"] = "scheduler-owned"
            scheduler.save()

            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("scan", persisted["current_stage"])
            self.assertEqual({"spent": 9}, persisted["counters"])
            self.assertEqual("newer", persisted["external_marker"])
            self.assertEqual("scheduler-owned", persisted["recommended_next_step"])
            self.assertEqual(persisted, scheduler.state)
            self.assertEqual(persisted, scheduler._baseline_state)

    def test_save_gives_scheduler_deterministic_ownership_of_changed_top_level_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
            self._write_json(state_path, self._family_generation_state())
            scheduler = ContinuousAlphaScheduler(
                root,
                state_path,
                workflow_config={"llm_provider": {"provider": "disabled"}},
            )
            external = json.loads(state_path.read_text(encoding="utf-8"))
            external["current_stage"] = "scan"
            self._write_json(state_path, external)
            scheduler.state["current_stage"] = "triage"

            scheduler.save()

            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("triage", persisted["current_stage"])

            external = dict(persisted)
            external["counters"] = {"spent": 12}
            self._write_json(state_path, external)
            scheduler.save()
            refreshed = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual({"spent": 12}, refreshed["counters"])

    def test_invalid_config_identity_matches_planning_and_scheduler_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
            self._write_json(state_path, {"run_tag": "test-run"})
            config = {
                "llm_provider": {
                    "provider": "cli",
                    "model": "model-secret-value",
                    "api_key": "must-never-appear",
                    "base_url": "https://example.invalid/v1?key=url-secret-value",
                    "command": ["tool", "--token", "cli-secret-value"],
                    "nested": {"refresh_token": "also-secret"},
                }
            }

            planning = LLMPlanAdapter.from_config(config, workspace_root=root)
            scheduler = ContinuousAlphaScheduler(root, state_path, workflow_config=config)
            planning_metadata = planning.metadata()
            scheduler_metadata = scheduler.state["llm_provider"]

            self.assertEqual(
                planning_metadata["config_digest"],
                scheduler_metadata["config_digest"],
            )
            serialized = json.dumps(
                {"planning": planning_metadata, "scheduler": scheduler_metadata},
                sort_keys=True,
            )
            self.assertNotIn("must-never-appear", serialized)
            self.assertNotIn("also-secret", serialized)
            self.assertNotIn("model-secret-value", serialized)
            self.assertNotIn("url-secret-value", serialized)
            self.assertNotIn("cli-secret-value", serialized)

    def test_state_workflow_config_path_rejects_traversal_absolute_and_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            outside = root.parent / "outside.json"
            self._write_json(outside, {"llm_provider": {"provider": "disabled"}})
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"

            for unsafe in ("../outside.json", str(outside.resolve())):
                with self.subTest(path=unsafe):
                    self._write_json(
                        state_path,
                        {"run_tag": "test-run", "workflow_config_path": unsafe},
                    )
                    with self.assertRaisesRegex(ValueError, "untrusted workflow config path"):
                        ContinuousAlphaScheduler(root, state_path)

            link = root / "configs" / "linked.json"
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                link.symlink_to(outside)
            except OSError:
                pass
            else:
                self._write_json(
                    state_path,
                    {"run_tag": "test-run", "workflow_config_path": "configs/linked.json"},
                )
                with self.assertRaisesRegex(ValueError, "untrusted workflow config path"):
                    ContinuousAlphaScheduler(root, state_path)

    def test_explicit_external_config_is_allowed_but_not_persisted_as_state_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            external = root.parent / "external.json"
            self._write_json(external, {"llm_provider": {"provider": "disabled"}})
            state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
            self._write_json(state_path, {"run_tag": "test-run"})

            ContinuousAlphaScheduler(root, state_path, workflow_config=external)

            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn("workflow_config_path", persisted)
            self.assertEqual("explicit", persisted["workflow_config_metadata"]["source"])
            self.assertEqual("external_not_persisted", persisted["workflow_config_metadata"]["path_status"])

    def test_ready_provider_initialization_supersedes_old_error_before_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = self._write_family_generation_fixture(root)
            old = {
                "status": "error",
                "phase": "initialization",
                "evaluated_config_digest": "old-digest",
                "timestamp": "2020-01-01T00:00:00+00:00",
            }
            self._write_json(state_path.parent / "llm_template_diagnostic.json", old)
            current = json.loads(state_path.read_text(encoding="utf-8"))
            current["llm_template_diagnostic"] = old
            self._write_json(state_path, current)
            provider = self.RecordingProvider("[]")

            with patch("src.continuous_alpha_scheduler.create_llm_provider", return_value=provider):
                scheduler = ContinuousAlphaScheduler(
                    root,
                    state_path,
                    workflow_config={"llm_provider": {"provider": "ollama", "model": "qwen-test"}},
                )

            diagnostic = json.loads(
                (state_path.parent / "llm_template_diagnostic.json").read_text(encoding="utf-8")
            )
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("not_evaluated", diagnostic["status"])
            self.assertEqual(scheduler.llm_provider_config_digest, diagnostic["evaluated_config_digest"])
            self.assertEqual(diagnostic, persisted["llm_template_diagnostic"])
            self.assertNotEqual("old-digest", diagnostic["evaluated_config_digest"])

    def _write_family_generation_fixture(self, root: Path) -> Path:
        state_path = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run" / "iteration_state.json"
        self._write_json(state_path, self._family_generation_state())
        self._write_json(
            root / ".local" / "data" / "field_quadrant_analysis.json",
            [
                {
                    "field_id": "operating_income",
                    "dataset_id": "fundamental6",
                    "category": "fundamental",
                    "description": "operating income",
                    "quadrant": "Q1 high-value",
                }
            ],
        )
        self._write_json(state_path.parent / "alpha_skeleton_blocklist.json", [])
        return state_path

    @staticmethod
    def _family_generation_state() -> dict[str, object]:
        return {
            "run_tag": "test-run",
            "current_iteration": 1,
            "current_stage": "family_generation",
            "completed_stages": [],
            "active_stage_inputs": {},
            "state_files": {},
        }

    def test_generate_builds_offline_families_from_reference_alphas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            self._write_json(
                root / ".local" / "data" / "field_quadrant_analysis.json",
                [
                    {
                        "field_id": "operating_profit_before_depr_amort",
                        "dataset_id": "fundamental6",
                        "category": "fundamental",
                        "description": "operating profit before depreciation and amortization",
                    },
                    {
                        "field_id": "operating_income",
                        "dataset_id": "fundamental6",
                        "category": "fundamental",
                        "description": "operating income",
                    },
                ],
            )
            self._write_json(
                root / "submitted_alphas" / "index.json",
                [
                    {
                        "alpha_id": "1Yn2kk8M",
                        "expression": "group_rank(rank(-returns) / 10 + operating_profit_before_depr_amort / cap / 10, industry)",
                        "settings": {"decay": 5, "neutralization": "MARKET"},
                        "metrics": {"sharpe": 2.24, "fitness": 1.69, "turnover": 0.3208, "returns": 0.1835, "drawdown": 0.0981},
                        "status": "ACTIVE",
                    },
                    {
                        "alpha_id": "N15al1aw",
                        "expression": "rank(-returns) - rank(close - ts_mean(close, 5)) + group_rank(operating_income / cap, subindustry)",
                        "settings": {"decay": 6, "neutralization": "SUBINDUSTRY"},
                        "metrics": {"sharpe": 2.07, "fitness": 1.50, "turnover": 0.31, "returns": 0.1617, "drawdown": 0.0809},
                        "status": "ACTIVE",
                    },
                ],
            )
            self._write_json(
                run_dir / "alpha_skeleton_blocklist.json",
                [{"skeleton": "operating-profit-before-depr-amort-anchor-subindustry"}],
            )

            generator = LLMTemplateGenerator()
            families = generator.generate(
                workspace_root=root,
                run_dir=run_dir,
                selected_dataset="fundamental6",
                selected_fields=[
                    {
                        "field_id": "operating_profit_before_depr_amort",
                        "category": "fundamental",
                        "description": "operating profit before depreciation and amortization",
                    },
                    {
                        "field_id": "operating_income",
                        "category": "fundamental",
                        "description": "operating income",
                    },
                ],
                max_families=5,
            )

            self.assertTrue(families)
            self.assertTrue(any("rank(-returns)" in item["expression"] for item in families))
            self.assertTrue(all(item["skeleton"] != "operating-profit-before-depr-amort-anchor-subindustry" for item in families))
            self.assertTrue(all(item["fields"] for item in families))

    def test_generate_compresses_high_self_corr_archetypes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            self._write_json(
                root / ".local" / "data" / "field_quadrant_analysis.json",
                [
                    {
                        "field_id": "operating_profit_before_depr_amort",
                        "dataset_id": "fundamental6",
                        "category": "fundamental",
                        "description": "operating profit before depreciation and amortization",
                    },
                    {
                        "field_id": "operating_income",
                        "dataset_id": "fundamental6",
                        "category": "fundamental",
                        "description": "operating income",
                    },
                    {
                        "field_id": "net_income",
                        "dataset_id": "fundamental6",
                        "category": "fundamental",
                        "description": "net income",
                    },
                ],
            )
            self._write_json(
                root / "submitted_alphas" / "index.json",
                [
                    {
                        "alpha_id": "1Yn2kk8M",
                        "expression": "group_rank(rank(-returns) / 10 + operating_profit_before_depr_amort / cap / 10, industry)",
                        "settings": {"decay": 5, "neutralization": "MARKET"},
                        "metrics": {"sharpe": 2.24, "fitness": 1.69, "turnover": 0.3208, "returns": 0.1835, "drawdown": 0.0981},
                        "status": "ACTIVE",
                    },
                    {
                        "alpha_id": "N15al1aw",
                        "expression": "rank(-returns) - rank(close - ts_mean(close, 5)) + group_rank(operating_income / cap, subindustry)",
                        "settings": {"decay": 6, "neutralization": "SUBINDUSTRY"},
                        "metrics": {"sharpe": 2.07, "fitness": 1.50, "turnover": 0.31, "returns": 0.1617, "drawdown": 0.0809},
                        "status": "ACTIVE",
                    },
                ],
            )
            self._write_json(run_dir / "alpha_skeleton_blocklist.json", [])

            generator = LLMTemplateGenerator()
            families = generator.generate(
                workspace_root=root,
                run_dir=run_dir,
                selected_dataset="fundamental6",
                selected_fields=[
                    {
                        "field_id": "operating_profit_before_depr_amort",
                        "category": "fundamental",
                        "description": "operating profit before depreciation and amortization",
                    },
                    {
                        "field_id": "operating_income",
                        "category": "fundamental",
                        "description": "operating income",
                    },
                    {
                        "field_id": "net_income",
                        "category": "fundamental",
                        "description": "net income",
                    },
                ],
                max_families=8,
            )

            high_risk = [item for item in families if item["self_corr_risk"] == "high"]
            self.assertEqual(len(high_risk), len({item["chassis"] for item in high_risk}))
            archetype_counts = Counter(item["archetype"] for item in families)
            self.assertLessEqual(archetype_counts["reversal-anchor-blend"], 1)
            self.assertLessEqual(archetype_counts["price-reversion-combo"], 1)
            field_counts = Counter(field_id for item in families for field_id in item["fields"])
            self.assertTrue(field_counts)
            self.assertLessEqual(max(field_counts.values()), 2)

    def test_llm_prompt_includes_constrained_slots_and_failure_lessons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "workflow" / "continuous-alpha" / "test-run"
            self._write_json(
                run_dir / "iteration_state.json",
                {
                    "completed_stages": [
                        {
                            "stage": "scan",
                            "iteration": 1,
                            "scan_output": ".local/data/workflow/continuous-alpha/test-run/scan_round1_iteration1.json",
                        }
                    ]
                },
            )
            self._write_json(
                run_dir / "scan_round1_iteration1.json",
                [
                    {
                        "alpha_id": "SC1",
                        "expression": "group_rank(rank(-returns) / 10 + actual_sales_value_quarterly / cap / 10, industry)",
                        "metrics": {"sharpe": 1.7, "fitness": 1.2, "turnover": 0.2},
                        "checks": [{"name": "SELF_CORRELATION", "result": "FAIL"}],
                    },
                    {
                        "expression": "group_rank(top1000 / cap, subindustry)",
                        "error": "Unit[Universe:1] incompatible with divide",
                        "metrics": {},
                        "checks": [],
                    },
                ],
            )
            self._write_json(
                run_dir / "knowledge_base.json",
                {
                    "success_patterns": [
                        {"pattern": "group_rank(FIELD / SERIES, GROUP)", "dataset": "analyst4", "metrics": {"sharpe": 1.4}}
                    ],
                    "failure_pitfalls": [
                        {"pattern": "group_rank(FIELD / SERIES, GROUP)", "route_decision": "self_corr_escape", "failed_checks": ["SELF_CORRELATION"]}
                    ],
                    "field_insights": [
                        {"pattern": "FIELD_EFFECTIVE:actual_sales_value_quarterly", "field": "actual_sales_value_quarterly"},
                        {"pattern": "FIELD_PROBLEMATIC:top1000", "field": "top1000"},
                    ],
                },
            )
            self._write_json(
                run_dir / "field_pool.json",
                {
                    "diversity_suggestions": {
                        "underused_fields": ["actual_sales_value_quarterly"],
                        "overused_fields": ["top1000"],
                        "required_slots": ["conditional-gated"],
                    }
                },
            )
            prompt = LLMTemplateGenerator()._build_prompt(
                workspace_root=root,
                run_dir=run_dir,
                selected_dataset="analyst4",
                selected_fields=[{"field_id": "actual_sales_value_quarterly", "description": "actual quarterly sales"}],
                principles={},
                seed_families=[
                    {
                        "skeleton": "seed",
                        "expression": "group_rank(actual_sales_value_quarterly / cap, subindustry)",
                        "fields": ["actual_sales_value_quarterly"],
                        "reason": "seed",
                    }
                ],
                blocked_skeletons=set(),
                blocked_chassis=set(),
                submitted_expressions=set(),
            )
            self.assertIn("HIGH SELF-CORRELATION CHASSIS TO ESCAPE", prompt)
            self.assertIn("INVALID FIELD/DATASET LESSONS", prompt)
            self.assertIn("LIGHTWEIGHT KNOWLEDGE BASE FEEDBACK", prompt)
            self.assertIn("DIVERSITY SUGGESTIONS FOR THIS RUN", prompt)
            self.assertIn("conditional/gated", prompt)
            self.assertIn("self-corr escape drafts", prompt)
            self.assertIn("Avoid UNITS warnings", prompt)

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
