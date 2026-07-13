"""Build a balanced, history-aware validation scan from local candidate configs."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import copy
import json
from pathlib import Path
from typing import Any

from src.output_evaluation.validators import validate_expression_candidates


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _candidate_key(expression: str, settings: dict[str, Any]) -> tuple[str, str]:
    return expression.strip(), json.dumps(settings, sort_keys=True, ensure_ascii=False)


def _walk_rows(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            yield from _walk_rows(item)
    elif isinstance(payload, dict):
        if payload.get("expression") and isinstance(payload.get("settings"), dict):
            yield payload
        for value in payload.values():
            if isinstance(value, (dict, list)):
                yield from _walk_rows(value)


def _historical_keys(root: Path) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    runs_root = root / ".local" / "data" / "runs" / "continuous-alpha"
    if not runs_root.exists():
        return keys
    for path in runs_root.rglob("*.json"):
        for row in _walk_rows(_read_json(path)):
            if row.get("alpha_id") or row.get("metrics") or row.get("checks"):
                keys.add(_candidate_key(str(row["expression"]), dict(row["settings"])))
    return keys


def _source_candidates(root: Path) -> list[dict[str, Any]]:
    source_root = root / ".local" / "research" / "scans" / "continuous-alpha"
    paths = sorted(source_root.rglob("*.json"), key=lambda path: (-path.stat().st_mtime, path.as_posix()))
    rows: list[dict[str, Any]] = []
    for path in paths:
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        base_settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
        for item in payload.get("candidates") or []:
            if not isinstance(item, dict) or not item.get("expression"):
                continue
            settings = copy.deepcopy(base_settings)
            if isinstance(item.get("settings"), dict):
                settings.update(item["settings"])
            rows.append(
                {
                    "expression": str(item["expression"]),
                    "settings": settings,
                    "note": str(item.get("note") or ""),
                    "behavior_family": str(item.get("behavior_family") or "unclassified"),
                    "source_config": path.relative_to(root).as_posix(),
                }
            )
    return rows


def _field_types(root: Path) -> dict[str, str]:
    payload = _read_json(root / ".local" / "data" / "all_wqb_fields.json")
    if not isinstance(payload, dict):
        return {}
    return {
        str(row["id"]): str(row.get("type") or "")
        for row in payload.get("fields") or []
        if isinstance(row, dict) and row.get("id")
    }


def build_validation_scan(root: Path, *, run_tag: str, budget: int) -> Path:
    root = Path(root).resolve()
    if budget <= 0:
        raise ValueError("budget must be positive")

    excluded = _historical_keys(root)
    source_candidates = _source_candidates(root)
    field_types = _field_types(root)
    blocked_indexes: set[int] = set()
    if field_types:
        evaluation = validate_expression_candidates("validation_scan_pool", source_candidates, field_types=field_types)
        blocked_indexes = {
            int(diagnosis.evidence["row_index"])
            for diagnosis in evaluation.diagnoses
            if "row_index" in diagnosis.evidence
        }
    seen = set(excluded)
    by_family: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for index, candidate in enumerate(source_candidates):
        if index in blocked_indexes:
            continue
        key = _candidate_key(candidate["expression"], candidate["settings"])
        if key in seen:
            continue
        seen.add(key)
        by_family[candidate["behavior_family"]].append(candidate)

    available = sum(len(queue) for queue in by_family.values())
    if available < budget:
        raise ValueError(f"only {available} unique untested candidates are available for budget {budget}")

    selected: list[dict[str, Any]] = []
    families = deque(sorted(by_family))
    while len(selected) < budget:
        family = families.popleft()
        queue = by_family[family]
        if queue:
            selected.append(queue.popleft())
        if queue:
            families.append(family)

    output = f".local/data/runs/continuous-alpha/{run_tag}/simulation_results.json"
    payload = {
        "output": output,
        "continue_on_pass": True,
        "max_concurrency": 3,
        "candidates": selected,
        "validation": {
            "run_tag": run_tag,
            "simulation_budget": budget,
            "unique_untested_pool": available,
            "excluded_historical_combinations": len(excluded),
            "preflight_blocked_candidates": len(blocked_indexes),
            "auto_submit": False,
            "selection": "round_robin_behavior_family",
        },
    }
    config_path = root / ".local" / "research" / "scans" / "continuous-alpha" / run_tag / "scan_config_round1.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--run-tag", required=True)
    parser.add_argument("--budget", type=int, required=True)
    args = parser.parse_args()
    path = build_validation_scan(Path(args.workspace_root), run_tag=args.run_tag, budget=args.budget)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
