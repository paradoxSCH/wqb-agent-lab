from __future__ import annotations

import unittest


EXPECTED_SCHEMA_NAMES = (
    "candidate",
    "diagnosis",
    "memory_event",
    "research_policy",
    "run_summary",
    "simulation_request",
    "simulation_result",
    "submission_job",
)


class SchemaContractTests(unittest.TestCase):
    def test_schema_directory_has_no_undeclared_contracts(self) -> None:
        from src.contracts.registry import SCHEMA_DIR

        discovered = tuple(sorted(path.name.removesuffix(".schema.json") for path in SCHEMA_DIR.glob("*.schema.json")))

        self.assertEqual(EXPECTED_SCHEMA_NAMES, discovered)

    def test_schema_readme_classifies_every_public_contract(self) -> None:
        from src.contracts.registry import SCHEMA_DIR

        readme = (SCHEMA_DIR / "README.md").read_text(encoding="utf-8")

        for name in EXPECTED_SCHEMA_NAMES:
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", readme)
        normalized = " ".join(readme.split())
        self.assertIn("only contract automatically enforced", normalized)
        self.assertIn("published validation boundaries", normalized)

    def test_lists_exact_p0_schema_names(self) -> None:
        from src.contracts import list_schema_names

        self.assertEqual(EXPECTED_SCHEMA_NAMES, list_schema_names())

    def test_loads_every_schema_with_required_metadata(self) -> None:
        from src.contracts import load_schema, schema_digest, schema_path

        for name in EXPECTED_SCHEMA_NAMES:
            with self.subTest(name=name):
                path = schema_path(name)
                schema = load_schema(name)
                digest = schema_digest(name)

                self.assertTrue(path.name.endswith(".schema.json"))
                self.assertTrue(path.is_file())
                self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
                self.assertIn("$id", schema)
                self.assertIn("title", schema)
                self.assertEqual("object", schema["type"])
                self.assertIsInstance(schema["required"], list)
                self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_valid_examples_pass_contract_validation(self) -> None:
        from src.contracts import validate_contract

        examples = {
            "candidate": {
                "candidate_id": "cand-001",
                "expression": "rank(ts_delta(close, 5))",
                "status": "draft",
                "hypothesis": {
                    "mechanism_id": "anchoring_reversal",
                    "thesis": "Investors underreact to short-term anchor breaks.",
                    "proxy_fields": ["close"],
                },
                "tags": ["behavioral", "reversal"],
            },
            "diagnosis": {
                "diagnosis_id": "diag-001",
                "diagnosis_type": "low_fitness",
                "severity": "medium",
                "policy_action": "repair",
                "evidence": ["fitness below configured threshold"],
                "confidence": 0.72,
            },
            "memory_event": {
                "memory_id": "mem-001",
                "event_type": "promote",
                "layer": "long_term",
                "source_artifact": ".local/data/runs/example/output_evaluation_report.json",
                "evidence_score": 0.84,
                "dependencies": ["diag-001"],
            },
            "research_policy": {
                "version": 1,
                "budget": {
                    "daily_simulation_limit": 20,
                    "exploration_share_limit": 0.2,
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
                            "allowed_proxy_fields": ["anl*"],
                            "kill_conditions": ["SELF_CORRELATION"],
                        }
                    ],
                },
            },
            "run_summary": {
                "run_id": "run-001",
                "mode": "dry_run",
                "budget": {"planned": 1000, "used": 0, "remaining": 1000},
                "counters": {"candidates": 12, "simulations": 0, "submit_ready": 0},
                "artifacts": [".local/data/runs/example/triage_summary.md"],
            },
            "simulation_request": {
                "candidate_id": "cand-001",
                "expression": "rank(ts_delta(close, 5))",
                "settings": {
                    "instrumentType": "EQUITY",
                    "region": "USA",
                    "universe": "TOP3000",
                    "delay": 1,
                    "decay": 13,
                    "neutralization": "INDUSTRY",
                    "truncation": 0.13,
                },
            },
            "simulation_result": {
                "candidate_id": "cand-001",
                "status": "pass",
                "metrics": {"sharpe": 1.7, "fitness": 1.2, "turnover": 0.18},
                "checks": [{"name": "LOW_SHARPE", "result": "PASS"}],
                "alpha_id": "abc123",
            },
            "submission_job": {
                "job_id": "job-001",
                "alpha_id": "abc123",
                "state": "queued",
                "auto_submit": False,
                "expression": "rank(ts_delta(close, 5))",
            },
        }

        for name, payload in examples.items():
            with self.subTest(name=name):
                self.assertEqual([], validate_contract(name, payload))

    def test_invalid_payload_reports_stable_paths_and_messages(self) -> None:
        from src.contracts import validate_contract

        errors = validate_contract("submission_job", {"job_id": "job-001", "auto_submit": "yes"})
        rendered = [str(error) for error in errors]

        self.assertIn("$.alpha_id: missing required property", rendered)
        self.assertIn("$.state: missing required property", rendered)
        self.assertIn("$.auto_submit: expected boolean, got string", rendered)

    def test_assert_valid_contract_raises_value_error(self) -> None:
        from src.contracts import assert_valid_contract

        with self.assertRaisesRegex(ValueError, "candidate contract validation failed"):
            assert_valid_contract("candidate", {"candidate_id": "cand-001"})


if __name__ == "__main__":
    unittest.main()
