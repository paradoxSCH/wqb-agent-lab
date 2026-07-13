from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Mapping


MEMORY_LAYERS = ("short_term", "long_term", "knowledge_graph")

NODE_TYPES = (
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
)

EDGE_RELATIONS = (
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
)

WQB_ACTION_LANES = ("probe", "scale", "repair", "block", "submit", "holdout")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _json_loads(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


@dataclass(frozen=True)
class MemoryNode:
    id: str
    type: str
    layer: str
    title: str
    summary: str
    source_artifacts: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    confidence: float = 0.0
    status: str = "active"
    promotion_state: str = "none"
    decay_score: float = 0.0
    forgetting_state: str = "active"
    tags: list[str] = field(default_factory=list)
    embedding_ref: str = ""
    version: int = 1

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["source_artifacts"] = _json_dumps(self.source_artifacts)
        row["evidence_refs"] = _json_dumps(self.evidence_refs)
        row["tags"] = _json_dumps(self.tags)
        return row

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> MemoryNode:
        values = dict(row)
        values["source_artifacts"] = list(_json_loads(values["source_artifacts"]))
        values["evidence_refs"] = list(_json_loads(values["evidence_refs"]))
        values["tags"] = list(_json_loads(values["tags"]))
        values["confidence"] = float(values["confidence"])
        values["decay_score"] = float(values["decay_score"])
        values["version"] = int(values["version"])
        return cls(**values)


@dataclass(frozen=True)
class MemoryEdge:
    id: str
    from_node_id: str
    to_node_id: str
    relation: str
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    status: str = "active"
    version: int = 1

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["evidence_refs"] = _json_dumps(self.evidence_refs)
        return row

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> MemoryEdge:
        values = dict(row)
        values["evidence_refs"] = list(_json_loads(values["evidence_refs"]))
        values["confidence"] = float(values["confidence"])
        values["version"] = int(values["version"])
        return cls(**values)
