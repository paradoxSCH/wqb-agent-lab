from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Protocol

from src.alpha_memory.schema import MemoryEdge, MemoryNode
from src.memory_governance import is_retrievable_for_mode


_QUERY_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_QUERY_ALIASES = {
    "\u8fc7\u5ea6\u53cd\u5e94": ("overreaction", "reversal", "mean_reversion"),
    "\u951a\u5b9a": ("anchoring", "reference_point"),
    "\u5904\u7f6e\u6548\u5e94": ("disposition_effect", "gain_loss_asymmetry"),
    "\u6ce8\u610f\u529b": ("attention", "salience"),
    "\u635f\u5931\u538c\u6076": ("loss_aversion", "prospect_theory"),
}


@dataclass(frozen=True)
class RewrittenQuery:
    raw: str
    intent: str
    terms: list[str]
    expanded_terms: list[str] = field(default_factory=list)


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


class MemoryStore(Protocol):
    def list_nodes(self) -> list[MemoryNode]: ...

    def list_edges(self) -> list[MemoryEdge]: ...

    def connect(self) -> Any: ...

    def search_fts(self, query: str) -> list[MemoryNode]: ...


def rewrite_query(query: str) -> RewrittenQuery:
    normalized = query.replace("-", " ").lower()
    terms = [term.strip() for term in _QUERY_TOKEN_RE.findall(normalized) if term.strip()]
    expanded_terms = list(terms)
    for alias, expansions in _QUERY_ALIASES.items():
        if alias in normalized:
            expanded_terms.extend((alias, *expansions))
    expanded_terms = list(dict.fromkeys(expanded_terms))
    return RewrittenQuery(raw=query, intent=" ".join(expanded_terms), terms=terms, expanded_terms=expanded_terms)


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


def retrieve_memory(store: MemoryStore, query: str, top_k: int = 8, mode: str = "planner") -> RetrievalResult:
    rewritten_query = rewrite_query(query)
    fts_nodes = _search_fts(store, rewritten_query.expanded_terms)
    all_nodes = {
        node.id: node
        for node in store.list_nodes()
        if is_retrievable_for_mode(node, mode)
    }
    fts_ids = {node.id for node in fts_nodes if node.id in all_nodes}
    lexical_scores = {
        node.id: 1.0 / rank
        for rank, node in enumerate(fts_nodes, start=1)
        if node.id in all_nodes
    }
    edges = store.list_edges()
    graph_paths: dict[str, list[str]] = {}
    graph_scores: dict[str, float] = {}

    for edge in edges:
        if edge.from_node_id in fts_ids and edge.to_node_id in all_nodes:
            graph_paths.setdefault(edge.to_node_id, [edge.from_node_id, edge.to_node_id])
            graph_scores[edge.to_node_id] = max(
                graph_scores.get(edge.to_node_id, 0.0),
                lexical_scores[edge.from_node_id] * max(0.0, float(edge.confidence)) * 0.5,
            )
        if edge.to_node_id in fts_ids and edge.from_node_id in all_nodes:
            graph_paths.setdefault(edge.from_node_id, [edge.to_node_id, edge.from_node_id])
            graph_scores[edge.from_node_id] = max(
                graph_scores.get(edge.from_node_id, 0.0),
                lexical_scores[edge.to_node_id] * max(0.0, float(edge.confidence)) * 0.5,
            )

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
            {
                "step": "rerank",
                "weights": {"query_relevance": 2.0, "confidence": 1.0, "actionability": 1.0},
            },
        ]
    )

    memories = []
    for node_id in candidate_ids:
        node = all_nodes[node_id]
        channel = "fts" if node_id in fts_ids else "graph"
        action_lane = _action_lane_for_node(node)
        query_relevance = lexical_scores.get(node_id, graph_scores.get(node_id, 0.0))
        score = (2.0 * query_relevance) + node.confidence + (1.0 if action_lane != "holdout" else 0.2)
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


def _search_fts(store: MemoryStore, terms: list[str]) -> list[MemoryNode]:
    if not terms:
        return []
    return store.search_fts(" ".join(terms))
