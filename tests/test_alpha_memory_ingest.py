from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from src.alpha_memory.ingest import ingest_runs
from src.alpha_memory.store import SQLiteMemoryStore


class AlphaMemoryIngestTests(unittest.TestCase):
    def test_ingest_run_ledger_and_results_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "daily-20260601"
            run_dir.mkdir(parents=True)
            self._write_json(
                run_dir / "daily_budget_ledger.json",
                {
                    "daily_run_tag": "daily-20260601",
                    "date": "2026-06-01",
                    "daily_budget": 1000,
                    "spent_simulations": 420,
                    "current_stage": "scale_winners_partial",
                    "stage_order": ["direction_probe", "scale_winners"],
                },
            )
            self._write_json(
                run_dir / "scale_winners_results.json",
                [
                    {
                        "alpha_id": "A1",
                        "expression": "rank(ts_mean(cashflow, 60)) - rank(close)",
                        "sharpe": 1.9,
                        "fitness": 1.2,
                        "self_corr": 0.22,
                        "status": "near-pass",
                        "behavior_thesis": "quality_value_mispricing",
                    }
                ],
            )
            store = SQLiteMemoryStore(root / ".local" / "data" / "memory" / "alpha_memory.db")
            store.initialize()

            first = ingest_runs(store, root / ".local" / "data" / "runs" / "continuous-alpha")
            first_nodes = [node.to_row() for node in store.list_nodes()]
            first_edges = [edge.to_row() for edge in store.list_edges()]
            time.sleep(1.1)
            second = ingest_runs(store, root / ".local" / "data" / "runs" / "continuous-alpha")
            second_nodes = [node.to_row() for node in store.list_nodes()]
            second_edges = [edge.to_row() for edge in store.list_edges()]

            self.assertEqual(first.nodes_written, second.nodes_written)
            self.assertEqual(first.edges_written, second.edges_written)
            self.assertGreaterEqual(first.nodes_written, 4)
            self.assertGreaterEqual(first.edges_written, 3)
            self.assertEqual(len(store.list_nodes()), first.nodes_written)
            self.assertEqual(len(store.list_edges()), first.edges_written)
            self.assertEqual(first_nodes, second_nodes)
            self.assertEqual(first_edges, second_edges)

    def test_same_alpha_id_in_different_runs_creates_distinct_candidate_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / ".local" / "data" / "runs" / "continuous-alpha"
            self._write_run_with_candidate(runs_root, "daily-20260601", "2026-06-01")
            self._write_run_with_candidate(runs_root, "daily-20260602", "2026-06-02")
            store = SQLiteMemoryStore(root / ".local" / "data" / "memory" / "alpha_memory.db")
            store.initialize()

            ingest_runs(store, runs_root)

            candidate_nodes = [node for node in store.list_nodes() if node.type == "candidate"]
            self.assertEqual(len(candidate_nodes), 2)
            self.assertEqual({node.title for node in candidate_nodes}, {"A1"})
            self.assertEqual(len({node.id for node in candidate_nodes}), 2)

    def test_nested_metrics_and_checks_drive_confidence_and_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / "daily-20260603"
            run_dir.mkdir(parents=True)
            self._write_json(
                run_dir / "daily_budget_ledger.json",
                {
                    "daily_run_tag": "daily-20260603",
                    "date": "2026-06-03",
                    "spent_simulations": 25,
                    "current_stage": "scale_winners",
                    "stage_order": [],
                },
            )
            self._write_json(
                run_dir / "scale_winners_results.json",
                [
                    {
                        "alpha_id": "A1",
                        "expression": "rank(ts_mean(cashflow, 60)) - rank(close)",
                        "metrics": {"fitness": 1.2, "sharpe": 1.9},
                        "checks": {"status": "PASS", "self_corr": 0.22, "duplicate": False},
                        "behavior_thesis": "quality_value_mispricing",
                    }
                ],
            )
            store = SQLiteMemoryStore(root / ".local" / "data" / "memory" / "alpha_memory.db")
            store.initialize()

            ingest_runs(store, root / ".local" / "data" / "runs" / "continuous-alpha")

            candidate = [node for node in store.list_nodes() if node.type == "candidate"][0]
            self.assertIn("scale", candidate.tags)
            self.assertEqual(candidate.confidence, 1.2)

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_run_with_candidate(self, runs_root: Path, run_tag: str, date: str) -> None:
        run_dir = runs_root / run_tag
        run_dir.mkdir(parents=True)
        self._write_json(
            run_dir / "daily_budget_ledger.json",
            {
                "daily_run_tag": run_tag,
                "date": date,
                "spent_simulations": 10,
                "current_stage": "direction_probe",
                "stage_order": [],
            },
        )
        self._write_json(
            run_dir / "direction_probe_results.json",
            [
                {
                    "alpha_id": "A1",
                    "expression": f"rank(close) + {date[-2:]}",
                    "fitness": 0.7,
                    "status": "holdout",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
