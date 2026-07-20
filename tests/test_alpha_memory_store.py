from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wqb_agent_lab.memory.core.schema import MemoryEdge, MemoryNode
from wqb_agent_lab.memory.core.store import SQLiteMemoryStore


class SQLiteMemoryStoreTests(unittest.TestCase):
    def test_init_creates_tables_and_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db")
            store.initialize()

            self.assertEqual(store.schema_version(), 2)
            self.assertIn("schema_migrations", store.table_names())
            self.assertIn("memory_nodes", store.table_names())
            self.assertIn("memory_edges", store.table_names())
            self.assertIn("memory_events", store.table_names())
            self.assertIn("retrieval_traces", store.table_names())
            self.assertIn("memory_nodes_fts", store.table_names())

    def test_upsert_node_edge_and_event_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db")
            store.initialize()
            node = MemoryNode(
                id="run-example",
                type="run",
                layer="short_term",
                title="Example run",
                summary="Run produced one near-pass candidate.",
                source_artifacts=[".local/data/runs/example/daily_budget_ledger.json"],
                evidence_refs=["ledger:example"],
            )
            edge = MemoryEdge(
                id="edge-example",
                from_node_id="run-example",
                to_node_id="run-example",
                relation="supports",
                evidence_refs=["self"],
            )

            store.upsert_node(node)
            store.upsert_node(node)
            store.upsert_edge(edge)
            store.upsert_edge(edge)
            store.record_event("ingest", "run-example", {"artifact": "ledger", "step": 1})
            store.record_event("ingest", "run-example", {"step": 1, "artifact": "ledger"})

            self.assertEqual(len(store.list_nodes()), 1)
            self.assertEqual(len(store.list_edges()), 1)
            self.assertEqual(store.count_events(), 1)

    def test_export_jsonl_and_integrity_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SQLiteMemoryStore(root / "memory.db")
            store.initialize()
            store.upsert_node(MemoryNode(id="n1", type="run", layer="short_term", title="Run", summary="Summary"))
            export_path = root / "memory_graph.jsonl"

            store.export_jsonl(export_path)
            lines = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(lines[0]["kind"], "node")
            self.assertEqual(lines[0]["id"], "n1")
            self.assertTrue(export_path.exists())
            self.assertTrue(all("kind" in line for line in lines))
            self.assertEqual(store.integrity_check()["ok"], True)

    def test_search_fts_handles_punctuation_and_empty_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db")
            store.initialize()
            store.upsert_node(
                MemoryNode(
                    id="n1",
                    type="run",
                    layer="short_term",
                    title="wq alpha reversal",
                    summary="Run produced one near-pass candidate for ops reversal.",
                )
            )

            cases = {
                "near-pass": ["n1"],
                "wq:alpha": ["n1"],
                "ops/reversal": ["n1"],
                "one near-pass candidate": ["n1"],
                "": [],
            }

            for query, expected_ids in cases.items():
                with self.subTest(query=query):
                    self.assertEqual([node.id for node in store.search_fts(query)], expected_ids)

    def test_rebuild_indexes_restores_fts_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db")
            store.initialize()
            store.upsert_node(MemoryNode(id="n1", type="behavior_thesis", layer="long_term", title="Quality value", summary="cashflow quality valuation repair"))

            conn = store.connect()
            try:
                conn.execute("DELETE FROM memory_nodes_fts")
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(store.search_fts("cashflow"), [])

            store.rebuild_indexes()
            results = store.search_fts("cashflow")

            self.assertEqual(results[0].id, "n1")


if __name__ == "__main__":
    unittest.main()
