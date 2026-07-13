from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.behavioral_proxy.map import build_behavioral_proxy_map


def _field(
    field_id: str,
    description: str,
    *,
    coverage: float = 1.0,
    user_count: int = 10,
    alpha_count: int = 20,
    dataset_name: str = "Synthetic",
    category_name: str = "Model",
) -> dict[str, object]:
    return {
        "id": field_id,
        "description": description,
        "coverage": coverage,
        "userCount": user_count,
        "alphaCount": alpha_count,
        "dataset_name": dataset_name,
        "category_name": category_name,
    }


def _result(
    family: str,
    *,
    sharpe: float,
    fitness: float,
    failures: list[str] | None = None,
) -> dict[str, object]:
    failures = failures or []
    checks = [
        {"name": "LOW_SHARPE", "result": "FAIL" if "LOW_SHARPE" in failures else "PASS"},
        {"name": "LOW_FITNESS", "result": "FAIL" if "LOW_FITNESS" in failures else "PASS"},
        {"name": "SELF_CORRELATION", "result": "FAIL" if "SELF_CORRELATION" in failures else "PASS"},
    ]
    return {
        "note": f"{family}: synthetic candidate",
        "metrics": {"sharpe": sharpe, "fitness": fitness},
        "checks": checks,
    }


class BehavioralProxyMapTests(unittest.TestCase):
    def test_field_first_map_scores_proxyable_mechanisms(self) -> None:
        fields = [
            _field("eps_revision_surprise", "Analyst EPS estimate revision and earnings surprise", coverage=1.0),
            _field("analyst_numest", "Number of analyst forecasts counted in aggregation", coverage=0.95),
            _field("call_breakeven_30", "Call option breakeven and implied volatility proxy", coverage=0.9),
            _field("short_interest_ratio", "Short interest and borrow pressure", coverage=0.85),
            _field("high52w_anchor", "Distance to 52 week high reference price", coverage=0.99),
        ]

        report = build_behavioral_proxy_map(fields)
        mechanisms = {row["mechanism"]: row for row in report["mechanisms"]}

        analyst = mechanisms["analyst_expectation_revision"]
        self.assertEqual(analyst["proxy_strength"], "strong")
        self.assertGreaterEqual(analyst["field_evidence"]["matched_field_count"], 2)
        self.assertIn("分析师", analyst["label_zh"])

        limits = mechanisms["limits_to_arbitrage_conditioned_mispricing"]
        self.assertEqual(limits["proxy_strength"], "strong")
        self.assertEqual(limits["field_evidence"]["matched_field_count"], 2)

        reference = mechanisms["reference_point_disposition_drift"]
        self.assertEqual(reference["proxy_strength"], "weak")
        self.assertEqual(reference["budget_policy"], "downweight")

    def test_result_feedback_promotes_passed_proxy_and_penalizes_weak_outcomes(self) -> None:
        fields = [
            _field("social_sentiment_score", "Social media sentiment score", coverage=0.9),
            _field("news_sentiment_reversal", "News sentiment reversal model", coverage=0.9),
            _field("high52w_anchor", "Distance to 52 week high reference price", coverage=0.99),
        ]
        results = [
            _result("media_sentiment_reversal", sharpe=1.34, fitness=1.06),
            _result("reference_point_disposition_drift", sharpe=0.42, fitness=0.12, failures=["LOW_SHARPE", "LOW_FITNESS"]),
        ]

        report = build_behavioral_proxy_map(fields, result_rows=results)
        mechanisms = {row["mechanism"]: row for row in report["mechanisms"]}

        media = mechanisms["media_sentiment_reversal"]
        self.assertEqual(media["result_feedback"]["all_pass_count"], 1)
        self.assertEqual(media["result_strength"], "promising")
        self.assertEqual(media["budget_policy"], "promote")
        self.assertIn("已经出现全检查通过", media["rationale_zh"])

        reference = mechanisms["reference_point_disposition_drift"]
        self.assertEqual(reference["result_strength"], "weak")
        self.assertEqual(reference["budget_policy"], "downweight")
        self.assertIn("LOW_FITNESS", reference["expected_failure_modes"])

    def test_cli_exports_behavioral_proxy_map_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fields_path = root / "fields.json"
            results_path = root / "results.json"
            output_path = root / "proxy_map.json"
            fields_path.write_text(
                json.dumps(
                    {
                        "fields": [
                            _field("eps_revision_surprise", "Analyst EPS estimate revision", coverage=1.0),
                            _field("analyst_numest", "Number of analyst forecasts", coverage=0.95),
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps([_result("analyst_expectation_revision", sharpe=1.5, fitness=1.2)], ensure_ascii=False),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.build_behavioral_proxy_map",
                    "--fields",
                    str(fields_path),
                    "--results",
                    str(results_path),
                    "--output",
                    str(output_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mechanisms"][0]["mechanism"], "analyst_expectation_revision")
            self.assertIn("wrote", completed.stdout.lower())


if __name__ == "__main__":
    unittest.main()
