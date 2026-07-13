"""Run the legacy experimental continuous scheduler."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.wq.workflows import ContinuousAlphaScheduler, resolve_state_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Advance the legacy experimental workflow from iteration_state.json"
    )
    parser.add_argument("--run-tag", help="Workflow run tag under .local/data/runs/continuous-alpha/")
    parser.add_argument("--state", help="Explicit path to iteration_state.json")
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="Workspace root containing .local/ and scan runner entry points",
    )
    parser.add_argument(
        "--workflow-config",
        default=None,
        help="Canonical workflow config used by planning; relative paths resolve from workspace root",
    )
    parser.add_argument(
        "--max-stages",
        type=int,
        default=0,
        help="Maximum number of workflow stages to execute in this invocation (0 means no explicit cap)",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Keep advancing across iterations until blocked instead of stopping after one completed iteration",
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan and update nothing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace_root = Path(args.workspace_root).resolve()
    state_path = resolve_state_path(workspace_root, args.run_tag, args.state)
    scheduler = ContinuousAlphaScheduler(
        workspace_root,
        state_path,
        dry_run=args.dry_run,
        workflow_config=Path(args.workflow_config) if args.workflow_config else None,
    )
    messages = scheduler.run(max_stages=args.max_stages, continuous=args.continuous)
    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
