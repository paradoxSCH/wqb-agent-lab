from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from .engine import DEFAULT_WORKFLOW_CONFIG, ResearchWorkflow


def parse_date(value: str | None) -> date | None:
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the daily alpha research workflow.")
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--workflow-config", default=str(DEFAULT_WORKFLOW_CONFIG))
    parser.add_argument("--date", help="Daily run date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument(
        "--budget-mode",
        choices=["conservative", "standard", "aggressive", "expanded_1500"],
    )
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--stop-after-summary", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--execute-scans", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=900)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workflow = ResearchWorkflow(
        Path(args.workspace_root),
        workflow_config=Path(args.workflow_config),
        run_date=parse_date(args.date),
        budget_mode=args.budget_mode,
        execute_scans=args.execute_scans,
        dry_run=args.dry_run,
    )
    if args.daemon:
        workflow.run_daemon(
            poll_seconds=args.poll_seconds,
            continue_next_day=not args.stop_after_summary,
        )
        return 0
    if not args.run_once and not args.summary_only:
        workflow.run_until_budget_complete(poll_seconds=args.poll_seconds)
        return 0
    for message in workflow.run_once(summary_only=args.summary_only):
        print(message)
    return 0
