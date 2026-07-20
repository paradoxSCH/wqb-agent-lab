"""Compatibility import for the canonical installed namespace."""

from wqb_agent_lab.memory import MemoryEdge, MemoryNode, assess_evidence, evaluate_forgetting, resolve_action_permission

__all__ = [
    "MemoryEdge",
    "MemoryNode",
    "assess_evidence",
    "evaluate_forgetting",
    "resolve_action_permission",
]
