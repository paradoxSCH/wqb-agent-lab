from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.maintenance.build_validation_scan import build_validation_scan


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_builder_selects_exact_budget_of_unique_untested_candidates() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / ".local" / "research" / "scans" / "continuous-alpha" / "source" / "scan.json"
        candidates = [
            {
                "expression": f"rank(field_{index})",
                "settings": {"region": "USA", "delay": 1},
                "behavior_family": f"family_{index % 3}",
            }
            for index in range(8)
        ]
        write_json(source, {"candidates": candidates})
        write_json(
            root / ".local" / "data" / "runs" / "continuous-alpha" / "old" / "results.json",
            [{"expression": "rank(field_0)", "settings": {"region": "USA", "delay": 1}, "alpha_id": "A0"}],
        )

        config_path = build_validation_scan(root, run_tag="validation-5", budget=5)
        payload = json.loads(config_path.read_text(encoding="utf-8"))

        assert len(payload["candidates"]) == 5
        assert len({row["expression"] for row in payload["candidates"]}) == 5
        assert "rank(field_0)" not in {row["expression"] for row in payload["candidates"]}
        assert payload["output"] == ".local/data/runs/continuous-alpha/validation-5/simulation_results.json"
        assert payload["validation"]["auto_submit"] is False


def test_builder_fails_when_unique_pool_is_smaller_than_budget() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        write_json(
            root / ".local" / "research" / "scans" / "continuous-alpha" / "source" / "scan.json",
            {"candidates": [{"expression": "rank(close)", "settings": {}}]},
        )

        try:
            build_validation_scan(root, run_tag="validation-2", budget=2)
        except ValueError as exc:
            assert "only 1 unique untested candidates" in str(exc)
        else:
            raise AssertionError("expected insufficient-pool failure")


def test_builder_filters_vector_arithmetic_with_field_inventory() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        write_json(
            root / ".local" / "data" / "all_wqb_fields.json",
            {
                "fields": [
                    {"id": "event_field", "type": "VECTOR"},
                    {"id": "matrix_field", "type": "MATRIX"},
                ]
            },
        )
        write_json(
            root / ".local" / "research" / "scans" / "continuous-alpha" / "source" / "scan.json",
            {
                "candidates": [
                    {"expression": "rank(event_field / 10)", "settings": {}, "behavior_family": "event"},
                    {"expression": "rank(matrix_field / 10)", "settings": {}, "behavior_family": "matrix"},
                ]
            },
        )

        config_path = build_validation_scan(root, run_tag="validation-1", budget=1)
        payload = json.loads(config_path.read_text(encoding="utf-8"))

        assert payload["candidates"][0]["expression"] == "rank(matrix_field / 10)"
        assert payload["validation"]["preflight_blocked_candidates"] == 1


def test_builder_retries_transport_failures_but_excludes_completed_alpha() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        candidates = [
            {"expression": "rank(field_a)", "settings": {}, "behavior_family": "a"},
            {"expression": "rank(field_b)", "settings": {}, "behavior_family": "b"},
        ]
        write_json(
            root / ".local" / "research" / "scans" / "continuous-alpha" / "source" / "scan.json",
            {"candidates": candidates},
        )
        write_json(
            root / ".local" / "data" / "runs" / "continuous-alpha" / "old" / "results.json",
            [
                {"expression": "rank(field_a)", "settings": {}, "error": "simulation_create_failed"},
                {"expression": "rank(field_b)", "settings": {}, "alpha_id": "A1", "metrics": {"sharpe": 0.5}},
            ],
        )

        config_path = build_validation_scan(root, run_tag="retry-1", budget=1)
        payload = json.loads(config_path.read_text(encoding="utf-8"))

        assert payload["candidates"][0]["expression"] == "rank(field_a)"
