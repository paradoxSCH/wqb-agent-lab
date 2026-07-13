from __future__ import annotations

import argparse
from pathlib import Path

from src.alpha_memory.ingest import ingest_runs
from src.alpha_memory.store import SQLiteMemoryStore


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _path_arg(value: str | None, default_relative: str) -> Path:
    if value is None:
        return _repo_root() / default_relative
    return Path(value).expanduser()


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest alpha memory run artifacts into SQLite.")
    parser.add_argument("--runs-root", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    store = SQLiteMemoryStore(_path_arg(args.db, ".local/data/memory/alpha_memory.db"))
    store.initialize()
    result = ingest_runs(store, _path_arg(args.runs_root, ".local/data/runs/continuous-alpha"))
    print(f"nodes={result.nodes_written} edges={result.edges_written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
