from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

from src.alpha_memory.retrieval import retrieve_memory
from src.alpha_memory.store import SQLiteMemoryStore


_REQUIRED_TABLES = frozenset(
    {
        "memory_nodes",
        "memory_edges",
        "memory_events",
        "retrieval_traces",
        "memory_nodes_fts",
    }
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _path_arg(value: str | None, default_relative: str) -> Path:
    if value is None:
        return _repo_root() / default_relative
    return Path(value).expanduser()


def _store_or_error(value: str | None) -> SQLiteMemoryStore | None:
    db_path = _path_arg(value, ".local/data/memory/alpha_memory.db")
    if not db_path.exists():
        print(f"database does not exist: {db_path}", file=sys.stderr)
        return None
    store = SQLiteMemoryStore(db_path)
    try:
        missing = sorted(_REQUIRED_TABLES - set(store.table_names()))
    except sqlite3.Error as exc:
        print(f"database is not an initialized alpha memory database schema: {db_path}: {exc}", file=sys.stderr)
        return None
    if missing:
        print(
            f"database is not an initialized alpha memory database schema: {db_path}; "
            f"missing tables: {', '.join(missing)}",
            file=sys.stderr,
        )
        return None
    return store


def main() -> int:
    parser = argparse.ArgumentParser(description="Query local alpha memory.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    store = _store_or_error(args.db)
    if store is None:
        return 2

    result = retrieve_memory(store, args.query)
    for memory in result.memories:
        print(f"{memory.node.id}\t{memory.action_lane}\t{memory.node.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
