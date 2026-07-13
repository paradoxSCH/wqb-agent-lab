from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.decision_attribution import record_scan_decision, score_decision_outcomes


class DecisionAttributionTests(unittest.TestCase):
    def test_record_scan_decision_writes_proxy_backed_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "daily-20260704"
            run_dir.mkdir(parents=True)
            proxy_path = root / ".local" / "data" / "behavioral_proxy" / "behavioral_proxy_map.json"
            self._write_json(
                proxy_path,
                {
                    "mechanisms": [
                        {
                            "mechanism": "media_sentiment_reversal",
                            "budget_policy": "promote",
                            "proxy_strength": "medium",
                            "result_strength": "promising",
                        },
                        {
                            "mechanism": "reference_point_disposition_drift",
                            "budget_policy": "downweight",
                            "proxy_strength": "medium",
                            "result_strength": "weak",
                        },
                    ]
                },
            )
            candidates = [
                {"expression": "rank(news_sentiment)", "behavior_family": "media_sentiment_reversal"},
                {"expression": "rank(high52w)", "behavior_family": "reference_point_disposition_drift"},
            ]

            record = record_scan_decision(
                root,
                run_dir,
                stage="direction_probe",
                stage_budget=120,
                remaining_stage_budget=120,
                remaining_daily_budget=1000,
                source_config=Path("configs/source/scan_config_round1.json"),
                sliced_config=Path("configs/run/direction_probe_2.json"),
                output_path=run_dir / "direction_probe_results.json",
                candidates=candidates,
                proxy_map_path=proxy_path,
            )

            path = run_dir / "decision_attribution.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["decision_id"], record["decision_id"])
            self.assertEqual(payload[0]["decision_type"], "stage_scan_budget")
            self.assertEqual(payload[0]["budget_delta"], 2)
            self.assertEqual(payload[0]["candidate_count"], 2)
            self.assertEqual(set(payload[0]["families_affected"]), {"media_sentiment_reversal", "reference_point_disposition_drift"})
            self.assertEqual(payload[0]["proxy_signals_used"][0]["mechanism"], "media_sentiment_reversal")
            self.assertEqual(payload[0]["deterministic_validation_result"], "passed")

    def test_score_decision_outcomes_updates_roi_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "daily-20260704"
            run_dir.mkdir(parents=True)
            result_path = run_dir / "direction_probe_results.json"
            record_scan_decision(
                root,
                run_dir,
                stage="direction_probe",
                stage_budget=3,
                remaining_stage_budget=3,
                remaining_daily_budget=1000,
                source_config=Path("configs/source/scan_config_round1.json"),
                sliced_config=Path("configs/run/direction_probe_3.json"),
                output_path=result_path,
                candidates=[
                    {"expression": "rank(a)", "behavior_family": "family_a"},
                    {"expression": "rank(b)", "behavior_family": "family_a"},
                    {"expression": "rank(c)", "behavior_family": "family_b"},
                ],
            )
            self._write_json(
                result_path,
                [
                    self._result(1.5, 1.1, []),
                    self._result(1.7, 1.2, ["SELF_CORRELATION"]),
                    self._result(0.2, 0.1, ["LOW_SHARPE", "LOW_FITNESS"]),
                ],
            )

            records = score_decision_outcomes(run_dir)

            self.assertEqual(records[0]["outcome"]["simulations_spent"], 3)
            self.assertEqual(records[0]["outcome"]["submit_ready_count"], 1)
            self.assertEqual(records[0]["outcome"]["near_pass_count"], 2)
            self.assertEqual(records[0]["outcome"]["low_value_count"], 1)
            self.assertEqual(records[0]["outcome"]["self_corr_fail_count"], 1)
            self.assertEqual(records[0]["outcome"]["roi_per_1000"], 333.333)

    def _result(self, sharpe: float, fitness: float, failures: list[str]) -> dict[str, object]:
        return {
            "metrics": {"sharpe": sharpe, "fitness": fitness, "turnover": 0.1},
            "checks": [
                {"name": "LOW_SHARPE", "result": "FAIL" if "LOW_SHARPE" in failures else "PASS"},
                {"name": "LOW_FITNESS", "result": "FAIL" if "LOW_FITNESS" in failures else "PASS"},
                {"name": "SELF_CORRELATION", "result": "FAIL" if "SELF_CORRELATION" in failures else "PASS"},
            ],
        }

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
