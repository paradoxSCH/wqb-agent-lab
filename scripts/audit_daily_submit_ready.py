from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.kimi_daily_workflow import read_json, relative_path
from src.wqb_agent_lab.workflow import ResearchWorkflow


def _date_from_run_tag(run_tag: str) -> str:
    match = re.search(r"(\d{8})$", run_tag)
    if not match:
        raise ValueError(f"Cannot infer YYYYMMDD date from run tag: {run_tag}")
    value = match.group(1)
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _alpha_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("alpha_id") or "") for row in rows if row.get("alpha_id")}


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") or {}
    return {
        "alpha_id": row.get("alpha_id"),
        "score": row.get("score"),
        "sharpe": metrics.get("sharpe"),
        "fitness": metrics.get("fitness"),
        "turnover": metrics.get("turnover"),
        "self_corr": row.get("self_corr"),
        "validation_source": row.get("validation_source"),
        "requires_live_recheck": row.get("requires_live_recheck"),
        "pending_checks": row.get("pending_checks") or [],
        "units_warning": bool(row.get("units_warning")),
        "source_path": row.get("source_path") or row.get("live_check_path"),
        "expression": row.get("expression"),
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Daily Submit-Ready Audit",
        "",
        f"Run: `{payload['run_tag']}`",
        f"Generated at: `{payload['generated_at']}`",
        f"Existing summary count: `{payload['existing_summary_count']}`",
        f"Recomputed ready count: `{payload['recomputed_ready_count']}`",
        f"Missed by existing summary: `{payload['missed_count']}`",
        "",
    ]
    if payload["missed_candidates"]:
        lines.extend(["## Missed Candidates", ""])
        for row in payload["missed_candidates"]:
            lines.append(
                f"- `{row['alpha_id']}` S={row['sharpe']} F={row['fitness']} T={row['turnover']} "
                f"self_corr={row['self_corr']} source={row['validation_source']} "
                f"recheck={row['requires_live_recheck']} score={row['score']}"
            )
    else:
        lines.append("No missed candidates were found.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit daily workflow submit-ready candidates missed by an existing summary.")
    parser.add_argument("--workspace-root", default=".", help="Workspace root containing .local/data/ and configs/.")
    parser.add_argument("--workflow-config", default=".local/research/workflows/production.json")
    parser.add_argument("--run-tag", required=True, help="Daily run tag under .local/data/runs/continuous-alpha/.")
    parser.add_argument("--date", help="Daily run date YYYY-MM-DD. Defaults to the date suffix in --run-tag.")
    parser.add_argument("--output", help="JSON output path. Defaults to the run directory.")
    parser.add_argument("--markdown-output", help="Markdown output path. Defaults beside the JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.workspace_root).resolve()
    run_date = _parse_date(args.date or _date_from_run_tag(args.run_tag))
    workflow = ResearchWorkflow(root, workflow_config=Path(args.workflow_config), run_date=run_date, execute_scans=False)
    if workflow.run_tag != args.run_tag:
        raise SystemExit(f"Workflow config produced run tag {workflow.run_tag}, expected {args.run_tag}")

    run_dir = root / ".local" / "data" / "runs" / "continuous-alpha" / args.run_tag
    existing_summary_path = run_dir / "submit_summary_budget_complete.json"
    existing_summary = read_json(existing_summary_path, {})
    existing_rows = existing_summary.get("submit_ready") if isinstance(existing_summary, dict) else []
    if not isinstance(existing_rows, list):
        existing_rows = []
    existing_ids = _alpha_ids(existing_rows)

    recomputed = workflow.collect_submit_ready()
    missed = [row for row in recomputed if str(row.get("alpha_id") or "") not in existing_ids]
    payload = {
        "run_tag": args.run_tag,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "existing_summary": relative_path(existing_summary_path, root) if existing_summary_path.exists() else None,
        "existing_summary_count": len(existing_rows),
        "recomputed_ready_count": len(recomputed),
        "missed_count": len(missed),
        "missed_candidates": [_compact(row) for row in missed],
        "top_recomputed_candidates": [_compact(row) for row in recomputed[:20]],
    }

    output_path = Path(args.output) if args.output else run_dir / "missed_submit_ready_audit.json"
    if not output_path.is_absolute():
        output_path = root / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    md_path = Path(args.markdown_output) if args.markdown_output else output_path.with_suffix(".md")
    if not md_path.is_absolute():
        md_path = root / md_path
    _write_markdown(md_path, payload)

    print(f"existing_summary_count={payload['existing_summary_count']}")
    print(f"recomputed_ready_count={payload['recomputed_ready_count']}")
    print(f"missed_count={payload['missed_count']}")
    print(f"wrote {relative_path(output_path, root)}")
    print(f"wrote {relative_path(md_path, root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
