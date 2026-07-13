from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from src.alpha_memory.hypothesis import classify_wqb_action_lane
from src.alpha_memory.schema import MemoryEdge, MemoryNode
from src.alpha_memory.store import SQLiteMemoryStore


@dataclass(frozen=True)
class IngestResult:
    nodes_written: int
    edges_written: int


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _stable_id(*parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


def _node_id(node_type: str, *parts: object) -> str:
    return f"{node_type}-{_stable_id(node_type, *parts)}"


def _edge_id(relation: str, from_id: str, to_id: str) -> str:
    return f"{relation}-{_stable_id(relation, from_id, to_id)}"


def ingest_runs(store: SQLiteMemoryStore, runs_root: Path, *, run_dirs: Iterable[Path] | None = None) -> IngestResult:
    nodes: dict[str, MemoryNode] = {}
    edges: dict[str, MemoryEdge] = {}

    ledger_paths = (
        [Path(run_dir) / "daily_budget_ledger.json" for run_dir in run_dirs]
        if run_dirs is not None
        else sorted(runs_root.glob("*/daily_budget_ledger.json"))
    )

    for ledger_path in ledger_paths:
        ledger = _read_json(ledger_path, {})
        if not isinstance(ledger, dict):
            continue

        run_tag = str(ledger.get("daily_run_tag") or ledger_path.parent.name)
        current_stage = str(ledger.get("current_stage") or "unknown")
        spent = ledger.get("spent_simulations", 0)
        timestamp = _timestamp_from_ledger(ledger)
        ledger_ref = ledger_path.as_posix()
        run_id = _node_id("run", run_tag)
        nodes[run_id] = MemoryNode(
            id=run_id,
            type="run",
            layer="short_term",
            title=run_tag,
            summary=f"Run spent {spent} simulations; current_stage={current_stage}.",
            source_artifacts=[ledger_ref],
            evidence_refs=[f"ledger:{run_tag}"],
            created_at=timestamp,
            updated_at=timestamp,
            confidence=1.0,
            tags=["run", current_stage],
        )

        stage_order = ledger.get("stage_order", [])
        if isinstance(stage_order, list):
            for stage in stage_order:
                stage_name = str(stage)
                stage_id = _node_id("stage", run_tag, stage_name)
                nodes[stage_id] = MemoryNode(
                    id=stage_id,
                    type="stage",
                    layer="short_term",
                    title=stage_name,
                    summary=f"Stage {stage_name} belongs to run {run_tag}.",
                    source_artifacts=[ledger_ref],
                    evidence_refs=[f"ledger:{run_tag}", f"stage:{stage_name}"],
                    created_at=timestamp,
                    updated_at=timestamp,
                    confidence=1.0,
                    tags=["stage", stage_name],
                )
                edge_id = _edge_id("depends_on", stage_id, run_id)
                edges[edge_id] = MemoryEdge(
                    id=edge_id,
                    from_node_id=stage_id,
                    to_node_id=run_id,
                    relation="depends_on",
                    confidence=1.0,
                    evidence_refs=[ledger_ref],
                    created_at=timestamp,
                    updated_at=timestamp,
                )

        for result_path in sorted(ledger_path.parent.glob("*_results.json")):
            result_payload = _read_json(result_path, [])
            if not isinstance(result_payload, list):
                continue
            for index, item in enumerate(result_payload):
                if not isinstance(item, dict):
                    continue
                candidate_id = _candidate_node_id(run_tag, result_path, index, item)
                expression = str(item.get("expression") or "")
                behavior_thesis = str(item.get("behavior_thesis") or "").strip()
                lane = _lane_for_candidate(item)
                result_ref = result_path.as_posix()
                candidate_tags = ["candidate", lane]
                if behavior_thesis:
                    candidate_tags.append(behavior_thesis)

                nodes[candidate_id] = MemoryNode(
                    id=candidate_id,
                    type="candidate",
                    layer="short_term",
                    title=str(item.get("alpha_id") or f"{result_path.stem}:{index}"),
                    summary=expression,
                    source_artifacts=[result_ref],
                    evidence_refs=[result_ref],
                    created_at=timestamp,
                    updated_at=timestamp,
                    confidence=_confidence_from_fitness(_candidate_fitness(item)),
                    tags=candidate_tags,
                )
                triage_edge_id = _edge_id("triaged_as", candidate_id, run_id)
                edges[triage_edge_id] = MemoryEdge(
                    id=triage_edge_id,
                    from_node_id=candidate_id,
                    to_node_id=run_id,
                    relation="triaged_as",
                    confidence=1.0,
                    evidence_refs=[result_ref, lane],
                    created_at=timestamp,
                    updated_at=timestamp,
                )

                if behavior_thesis:
                    thesis_id = _node_id("behavior_thesis", behavior_thesis)
                    nodes[thesis_id] = MemoryNode(
                        id=thesis_id,
                        type="behavior_thesis",
                        layer="long_term",
                        title=behavior_thesis,
                        summary=f"Behavior thesis observed in run artifacts: {behavior_thesis}.",
                        source_artifacts=[result_ref],
                        evidence_refs=[result_ref],
                        created_at=timestamp,
                        updated_at=timestamp,
                        confidence=1.0,
                        tags=["behavior_thesis", behavior_thesis],
                    )
                    support_edge_id = _edge_id("supports", candidate_id, thesis_id)
                    edges[support_edge_id] = MemoryEdge(
                        id=support_edge_id,
                        from_node_id=candidate_id,
                        to_node_id=thesis_id,
                        relation="supports",
                        confidence=1.0,
                        evidence_refs=[result_ref],
                        created_at=timestamp,
                        updated_at=timestamp,
                    )

    for node in nodes.values():
        store.upsert_node(node)
    for edge in edges.values():
        store.upsert_edge(edge)

    store.record_event(
        "ingest_runs",
        runs_root.as_posix(),
        {"nodes": len(nodes), "edges": len(edges)},
    )
    return IngestResult(nodes_written=len(nodes), edges_written=len(edges))


def _candidate_node_id(run_tag: str, result_path: Path, index: int, item: dict[str, Any]) -> str:
    alpha_id = item.get("alpha_id")
    if alpha_id:
        return _node_id("candidate", run_tag, str(alpha_id))
    return _node_id("candidate", run_tag, result_path.stem, index)


def _timestamp_from_ledger(ledger: dict[str, Any]) -> str:
    date = ledger.get("date")
    if isinstance(date, str) and date.strip():
        return f"{date.strip()}T00:00:00+00:00"
    return "1970-01-01T00:00:00+00:00"


def _flat_or_nested(item: dict[str, Any], flat_key: str, nested_key: str, nested_field: str) -> Any:
    if flat_key in item:
        return item.get(flat_key)
    nested = item.get(nested_key)
    if isinstance(nested, dict):
        return nested.get(nested_field)
    return None


def _candidate_fitness(item: dict[str, Any]) -> Any:
    return _flat_or_nested(item, "fitness", "metrics", "fitness")


def _candidate_self_corr(item: dict[str, Any]) -> Any:
    return _flat_or_nested(item, "self_corr", "checks", "self_corr")


def _candidate_duplicate(item: dict[str, Any]) -> Any:
    return _flat_or_nested(item, "duplicate", "checks", "duplicate")


def _candidate_status(item: dict[str, Any]) -> str:
    status = _flat_or_nested(item, "status", "checks", "status")
    if not isinstance(status, str):
        return ""
    return status.strip().lower().replace("_", "-")


def _confidence_from_fitness(value: Any) -> float:
    if isinstance(value, bool):
        return 0.5
    if isinstance(value, (int, float)):
        return float(value)
    return 0.5


def _lane_for_candidate(item: dict[str, Any]) -> str:
    status = _candidate_status(item)
    return classify_wqb_action_lane(
        {
            "submit_ready": bool(item.get("submit_ready")),
            "near_pass": status == "near-pass",
            "pass": status == "pass",
            "self_corr": _candidate_self_corr(item),
            "duplicate": bool(_candidate_duplicate(item)),
        }
    )
