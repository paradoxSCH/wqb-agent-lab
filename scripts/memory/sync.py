"""Synchronize run artifacts into governed alpha memory."""

from __future__ import annotations

import argparse
from pathlib import Path

from wqb_agent_lab.memory.sync import sync_run_memory


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync WQB workflow artifacts into alpha memory.")
    parser.add_argument("--workspace-root", default=".", help="Workspace root containing .local/data/ and configs/.")
    parser.add_argument("--run-dir", required=True, help="Run directory containing daily_budget_ledger.json.")
    parser.add_argument("--db", default=None, help="Optional alpha memory SQLite db path.")
    args = parser.parse_args()

    root = Path(args.workspace_root)
    db_path = Path(args.db) if args.db else None
    result = sync_run_memory(root, Path(args.run_dir), db_path=db_path)
    print(
        f"memory_sync_report={result.report_path} "
        f"nodes={result.nodes_written} edges={result.edges_written} events={result.events_recorded}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
