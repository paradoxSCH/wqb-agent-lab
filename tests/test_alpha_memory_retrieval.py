from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.alpha_memory.retrieval import retrieve_memory
from src.alpha_memory.schema import MemoryEdge, MemoryNode
from src.alpha_memory.store import SQLiteMemoryStore


class AlphaMemoryRetrievalTests(unittest.TestCase):
    def test_retrieve_returns_actionable_trace_with_graph_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db")
            store.initialize()
            thesis = MemoryNode(id="thesis-qv", type="behavior_thesis", layer="long_term", title="quality value", summary="quality value cashflow repair", tags=["quality", "value"])
            proxy = MemoryNode(id="proxy-cashflow", type="proxy_mapping", layer="knowledge_graph", title="cashflow proxy", summary="cashflow quality maps to valuation repair", tags=["cashflow"])
            kill = MemoryNode(id="kill-corr", type="kill_condition", layer="knowledge_graph", title="self corr kill", summary="block when self-corr is high", tags=["self-corr"])
            store.upsert_node(thesis)
            store.upsert_node(proxy)
            store.upsert_node(kill)
            store.upsert_edge(MemoryEdge(id="e1", from_node_id="thesis-qv", to_node_id="proxy-cashflow", relation="maps_to_proxy"))
            store.upsert_edge(MemoryEdge(id="e2", from_node_id="thesis-qv", to_node_id="kill-corr", relation="has_kill_condition"))

            result = retrieve_memory(store, "quality value cashflow repair", top_k=5)

            self.assertEqual(result.rewritten_query.intent, "quality value cashflow repair")
            self.assertGreaterEqual(len(result.memories), 2)
            self.assertIn("proxy-cashflow", [item.node.id for item in result.memories])
            self.assertTrue(any(item.action_lane in {"repair", "scale", "block", "probe"} for item in result.memories))
            self.assertTrue(result.trace.steps)

    def test_planner_retrieval_excludes_deprecated_and_forgotten_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db")
            store.initialize()
            active = MemoryNode(
                id="active-quality",
                type="alpha_family",
                layer="long_term",
                title="quality value active",
                summary="quality value useful memory",
                tags=["quality"],
                status="active",
                forgetting_state="active",
            )
            deprecated = MemoryNode(
                id="deprecated-quality",
                type="alpha_family",
                layer="long_term",
                title="quality value deprecated",
                summary="quality value stale memory",
                tags=["quality"],
                status="deprecated",
                forgetting_state="quarantined",
            )
            blocked = MemoryNode(
                id="blocked-quality",
                type="alpha_family",
                layer="long_term",
                title="quality value blocked",
                summary="quality value blocked memory",
                tags=["quality"],
                status="blocked",
                forgetting_state="forgotten",
            )
            store.upsert_node(active)
            store.upsert_node(deprecated)
            store.upsert_node(blocked)

            planner_result = retrieve_memory(store, "quality value", top_k=10)
            risk_result = retrieve_memory(store, "quality value", top_k=10, mode="risk_review")
            audit_result = retrieve_memory(store, "quality value", top_k=10, mode="audit")

            self.assertEqual([item.node.id for item in planner_result.memories], ["active-quality"])
            self.assertIn("deprecated-quality", [item.node.id for item in risk_result.memories])
            self.assertNotIn("blocked-quality", [item.node.id for item in risk_result.memories])
            self.assertIn("blocked-quality", [item.node.id for item in audit_result.memories])


if __name__ == "__main__":
    unittest.main()
