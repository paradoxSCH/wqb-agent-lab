from __future__ import annotations

import unittest

from wqb_agent_lab.memory.core.schema import (
    EDGE_RELATIONS,
    MEMORY_LAYERS,
    NODE_TYPES,
    WQB_ACTION_LANES,
    MemoryEdge,
    MemoryNode,
)


class AlphaMemorySchemaTests(unittest.TestCase):
    def test_schema_contains_required_layers_types_relations_and_lanes(self) -> None:
        self.assertEqual(MEMORY_LAYERS, ("short_term", "long_term", "knowledge_graph"))
        self.assertEqual(
            NODE_TYPES,
            (
                "run",
                "stage",
                "candidate",
                "alpha_family",
                "behavior_thesis",
                "dataset_field",
                "operator_skeleton",
                "failure_mode",
                "repair_strategy",
                "submission_decision",
                "reflection",
                "research_hypothesis",
                "proxy_mapping",
                "kill_condition",
                "budget_decision",
                "adversarial_review",
                "research_taste",
            ),
        )
        self.assertEqual(
            EDGE_RELATIONS,
            (
                "depends_on",
                "supports",
                "contradicts",
                "repairs",
                "duplicates",
                "promotes_to",
                "decays_due_to",
                "blocks",
                "retrieved_for",
                "influenced_plan",
                "maps_to_proxy",
                "has_kill_condition",
                "passes_independence_check",
                "fails_independence_check",
                "allocated_budget_to",
                "triaged_as",
            ),
        )
        self.assertEqual(WQB_ACTION_LANES, ("probe", "scale", "repair", "block", "submit", "holdout"))

    def test_memory_node_minimal_constructor_uses_defaults(self) -> None:
        node = MemoryNode(id="n1", type="run", layer="short_term", title="Run", summary="Summary")

        self.assertEqual(node.source_artifacts, [])
        self.assertEqual(node.evidence_refs, [])
        self.assertEqual(node.confidence, 0.0)
        self.assertEqual(node.status, "active")
        self.assertEqual(node.promotion_state, "none")
        self.assertEqual(node.decay_score, 0.0)
        self.assertEqual(node.forgetting_state, "active")
        self.assertEqual(node.tags, [])
        self.assertEqual(node.embedding_ref, "")
        self.assertEqual(node.version, 1)

    def test_memory_edge_minimal_constructor_uses_defaults(self) -> None:
        edge = MemoryEdge(id="e1", from_node_id="n1", to_node_id="n2", relation="supports")

        self.assertEqual(edge.confidence, 0.0)
        self.assertEqual(edge.evidence_refs, [])
        self.assertEqual(edge.status, "active")
        self.assertEqual(edge.version, 1)

    def test_memory_node_round_trips_to_row(self) -> None:
        node = MemoryNode(
            id="node-1",
            type="research_hypothesis",
            layer="short_term",
            title="Quality value repair",
            summary="Quality-value mispricing mapped to cashflow proxy.",
            source_artifacts=[".local/data/runs/example/daily_budget_ledger.json"],
            evidence_refs=["run:example"],
            confidence=0.82,
            status="active",
            promotion_state="candidate",
            decay_score=0.0,
            forgetting_state="active",
            tags=["quality", "value"],
            embedding_ref="emb-node-1",
            version=1,
        )

        row = node.to_row()
        restored = MemoryNode.from_row(row)

        self.assertEqual(restored, node)

    def test_memory_edge_round_trips_to_row(self) -> None:
        edge = MemoryEdge(
            id="edge-1",
            from_node_id="hypothesis-1",
            to_node_id="proxy-1",
            relation="maps_to_proxy",
            confidence=0.77,
            evidence_refs=["artifact:plan"],
            status="active",
            version=1,
        )

        row = edge.to_row()
        restored = MemoryEdge.from_row(row)

        self.assertEqual(restored, edge)


if __name__ == "__main__":
    unittest.main()
