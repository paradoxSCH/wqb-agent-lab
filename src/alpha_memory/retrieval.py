from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from src.alpha_memory.schema import MemoryNode
from src.memory_governance import is_retrievable_for_mode


_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class RewrittenQuery:
    raw: str
    intent: str
    terms: list[str]


@dataclass(frozen=True)
class RetrievedMemory:
    node: MemoryNode
    channels: list[str]
    score: float
    action_lane: str
    graph_path: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RetrievalTrace:
    steps: list[dict[str, Any]]


@dataclass(frozen=True)
class RetrievalResult:
    rewritten_query: RewrittenQuery
    memories: list[RetrievedMemory]
    trace: RetrievalTrace


def rewrite_query(query: str) -> RewrittenQuery:
    normalized = query.replace("-", " ").lower()
    terms = [term.strip() for term in _QUERY_TOKEN_RE.findall(normalized) if term.strip()]
    return RewrittenQuery(raw=query, intent=" ".join(terms), terms=terms)


def _action_lane_for_node(node: MemoryNode) -> str:
    if node.type == "kill_condition":
        return "block"
    if node.type == "repair_strategy":
        return "repair"
    if node.type == "proxy_mapping":
        return "probe"
    if node.type in {"behavior_thesis", "alpha_family"}:
        return "scale"
    return "holdout"


def retrieve_memory(store: object, query: str, top_k: int = 8, mode: str = "planner") -> RetrievalResult:
    rewritten_query = rewrite_query(query)
    fts_nodes = _search_fts(store, rewritten_query.terms)
    all_nodes = {
        node.id: node
        for node in store.list_nodes()
        if is_retrievable_for_mode(node, mode)
    }
    fts_ids = {node.id for node in fts_nodes if node.id in all_nodes}
    edges = store.list_edges()
    graph_paths: dict[str, list[str]] = {}

    for edge in edges:
        if edge.from_node_id in fts_ids and edge.to_node_id in all_nodes:
            graph_paths.setdefault(edge.to_node_id, [edge.from_node_id, edge.to_node_id])
        if edge.to_node_id in fts_ids and edge.from_node_id in all_nodes:
            graph_paths.setdefault(edge.from_node_id, [edge.to_node_id, edge.from_node_id])

    candidate_ids = set(fts_ids) | set(graph_paths)
    trace = RetrievalTrace(
        steps=[
            {
                "step": "rewrite",
                "raw": rewritten_query.raw,
                "intent": rewritten_query.intent,
                "terms": rewritten_query.terms,
            },
            {
                "step": "graph_expand",
                "seed_node_ids": sorted(fts_ids),
                "expanded_node_ids": sorted(set(graph_paths) - fts_ids),
            },
        ]
    )

    memories = []
    for node_id in candidate_ids:
        node = all_nodes[node_id]
        channel = "fts" if node_id in fts_ids else "graph"
        action_lane = _action_lane_for_node(node)
        score = node.confidence + (1.0 if action_lane != "holdout" else 0.2)
        memories.append(
            RetrievedMemory(
                node=node,
                channels=[channel],
                score=score,
                action_lane=action_lane,
                graph_path=[] if channel == "fts" else graph_paths[node_id],
            )
        )

    memories.sort(key=lambda item: (-item.score, item.node.id))
    return RetrievalResult(rewritten_query=rewritten_query, memories=memories[:top_k], trace=trace)


def _search_fts(store: object, terms: list[str]) -> list[MemoryNode]:
    if not terms:
        return []

    fts_query = " OR ".join(terms)
    conn = store.connect()
    try:
        rows = conn.execute(
            """
            SELECT n.*
            FROM memory_nodes_fts AS fts
            JOIN memory_nodes AS n ON n.id = fts.id
            WHERE memory_nodes_fts MATCH ?
            ORDER BY bm25(memory_nodes_fts)
            """,
            (fts_query,),
        ).fetchall()
        return [MemoryNode.from_row(row) for row in rows]
    finally:
        conn.close()
