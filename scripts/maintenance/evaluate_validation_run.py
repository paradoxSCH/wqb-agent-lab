"""Aggregate multi-part live validation runs without mixing transport failures into quality rates."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import mean, median
from typing import Any

from wqb_agent_lab.runtime.scan import is_pass


def summarize_rows(rows: list[dict[str, Any]], *, target: int) -> dict[str, Any]:
    successful_by_alpha: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("alpha_id") and isinstance(row.get("metrics"), dict):
            successful_by_alpha.setdefault(str(row["alpha_id"]), row)
    successful = list(successful_by_alpha.values())
    errors = [row for row in rows if row.get("error")]
    passes = [row for row in successful if is_pass(row.get("metrics") or {}, row.get("checks") or [])]
    checks = Counter(
        str(check.get("name") or "UNKNOWN")
        for row in successful
        for check in row.get("checks") or []
        if check.get("result") in {"FAIL", "ERROR"}
    )
    family_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in successful:
        family = str(row.get("behavior_family") or str(row.get("note") or "unclassified").split(":", 1)[0])
        family_rows[family].append(row)

    families: dict[str, dict[str, Any]] = {}
    for family, items in sorted(family_rows.items()):
        family_passes = [item for item in items if is_pass(item.get("metrics") or {}, item.get("checks") or [])]
        families[family] = {
            "simulations": len(items),
            "pass_count": len(family_passes),
            "pass_rate": round(len(family_passes) / len(items), 4),
            "mean_sharpe": round(mean(float(item["metrics"].get("sharpe") or 0) for item in items), 4),
            "mean_fitness": round(mean(float(item["metrics"].get("fitness") or 0) for item in items), 4),
        }

    sharpes = [float(row["metrics"].get("sharpe") or 0) for row in successful]
    fitnesses = [float(row["metrics"].get("fitness") or 0) for row in successful]
    return {
        "target_simulations": target,
        "target_reconciled": len(successful) == target,
        "successful_simulations": len(successful),
        "unique_alpha_ids": len({str(row["alpha_id"]) for row in successful}),
        "unique_expressions": len({str(row.get("expression") or "") for row in successful}),
        "historical_error_requests": len(errors),
        "pass_count": len(passes),
        "pass_rate": round(len(passes) / len(successful), 4) if successful else 0.0,
        "median_sharpe": round(median(sharpes), 4) if sharpes else 0.0,
        "mean_sharpe": round(mean(sharpes), 4) if sharpes else 0.0,
        "median_fitness": round(median(fitnesses), 4) if fitnesses else 0.0,
        "mean_fitness": round(mean(fitnesses), 4) if fitnesses else 0.0,
        "failed_check_counts": dict(checks.most_common()),
        "families": families,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--tag", action="append", required=True)
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.workspace_root).resolve()
    rows: list[dict[str, Any]] = []
    for tag in args.tag:
        path = root / ".local" / "data" / "runs" / "continuous-alpha" / tag / "simulation_results.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                rows.extend(item for item in payload if isinstance(item, dict))
    summary = summarize_rows(rows, target=args.target)
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["target_reconciled"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
