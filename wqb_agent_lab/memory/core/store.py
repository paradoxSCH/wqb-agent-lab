from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import tempfile

from wqb_agent_lab.memory.core.schema import MemoryEdge, MemoryNode


_FTS_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_HAN_RUN_RE = re.compile(r"[\u3400-\u9fff]+")


def _han_ngrams(value: str, sizes: tuple[int, ...] = (2, 3)) -> list[str]:
    grams: list[str] = []
    for run in _HAN_RUN_RE.findall(value):
        for size in sizes:
            if len(run) < size:
                grams.append(run)
            else:
                grams.extend(run[index:index + size] for index in range(len(run) - size + 1))
    return grams


def _fts_document(*parts: str) -> str:
    source = " ".join(parts)
    grams = _han_ngrams(source)
    return " ".join([source, *grams])


class SQLiteMemoryStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        conn = self.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS memory_nodes (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source_artifacts TEXT NOT NULL,
                    evidence_refs TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    promotion_state TEXT NOT NULL,
                    decay_score REAL NOT NULL,
                    forgetting_state TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    embedding_ref TEXT NOT NULL,
                    version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_edges (
                    id TEXT PRIMARY KEY,
                    from_node_id TEXT NOT NULL,
                    to_node_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_refs TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    FOREIGN KEY (from_node_id) REFERENCES memory_nodes(id) ON DELETE CASCADE,
                    FOREIGN KEY (to_node_id) REFERENCES memory_nodes(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS memory_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (event_type, subject_id, payload_json)
                );

                CREATE TABLE IF NOT EXISTS retrieval_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    node_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS memory_nodes_fts USING fts5(
                    id UNINDEXED,
                    title,
                    summary,
                    tags
                );
                """
            )
            conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (1,))
            conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (2,))
            conn.commit()
        finally:
            conn.close()

    def schema_version(self) -> int:
        conn = self.connect()
        try:
            row = conn.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()
            return int(row["version"])
        finally:
            conn.close()

    def table_names(self) -> list[str]:
        conn = self.connect()
        try:
            rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type IN ('table', 'virtual table')
                ORDER BY name
                """
            ).fetchall()
            return [str(row["name"]) for row in rows]
        finally:
            conn.close()

    def upsert_node(self, node: MemoryNode) -> None:
        row = node.to_row()
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "id")
        conn = self.connect()
        try:
            conn.execute(
                f"""
                INSERT INTO memory_nodes ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(id) DO UPDATE SET {assignments}
                """,
                [row[column] for column in columns],
            )
            conn.execute("DELETE FROM memory_nodes_fts WHERE id = ?", (node.id,))
            conn.execute(
                "INSERT INTO memory_nodes_fts (id, title, summary, tags) VALUES (?, ?, ?, ?)",
                (
                    node.id,
                    _fts_document(node.title),
                    _fts_document(node.summary),
                    _fts_document(" ".join(node.tags)),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def upsert_edge(self, edge: MemoryEdge) -> None:
        row = edge.to_row()
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "id")
        conn = self.connect()
        try:
            conn.execute(
                f"""
                INSERT INTO memory_edges ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(id) DO UPDATE SET {assignments}
                """,
                [row[column] for column in columns],
            )
            conn.commit()
        finally:
            conn.close()

    def list_nodes(self) -> list[MemoryNode]:
        conn = self.connect()
        try:
            rows = conn.execute("SELECT * FROM memory_nodes ORDER BY id").fetchall()
            return [MemoryNode.from_row(row) for row in rows]
        finally:
            conn.close()

    def list_edges(self) -> list[MemoryEdge]:
        conn = self.connect()
        try:
            rows = conn.execute("SELECT * FROM memory_edges ORDER BY id").fetchall()
            return [MemoryEdge.from_row(row) for row in rows]
        finally:
            conn.close()

    def record_event(self, event_type: str, subject_id: str, payload: object) -> None:
        payload_json = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        conn = self.connect()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO memory_events (event_type, subject_id, payload_json)
                VALUES (?, ?, ?)
                """,
                (event_type, subject_id, payload_json),
            )
            conn.commit()
        finally:
            conn.close()

    def count_events(self) -> int:
        conn = self.connect()
        try:
            row = conn.execute("SELECT COUNT(*) AS count FROM memory_events").fetchone()
            return int(row["count"])
        finally:
            conn.close()

    def search_fts(self, query: str) -> list[MemoryNode]:
        fts_query = self._normalize_fts_query(query)
        if not fts_query:
            return []

        conn = self.connect()
        try:
            rows = conn.execute(
                """
                SELECT n.*
                FROM memory_nodes_fts AS fts
                JOIN memory_nodes AS n ON n.id = fts.id
                WHERE memory_nodes_fts MATCH ?
                ORDER BY bm25(memory_nodes_fts)
                """,
                (fts_query,),
            ).fetchall()
            return [MemoryNode.from_row(row) for row in rows]
        finally:
            conn.close()

    def export_jsonl(self, output_path: Path | str) -> None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            nodes = [MemoryNode.from_row(row) for row in conn.execute("SELECT * FROM memory_nodes ORDER BY id")]
            edges = [MemoryEdge.from_row(row) for row in conn.execute("SELECT * FROM memory_edges ORDER BY id")]
            events = self._list_events(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        temp_file = tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            encoding="utf-8",
            newline="\n",
        )
        temp_path = Path(temp_file.name)
        try:
            with temp_file as output:
                for node in nodes:
                    output.write(json.dumps({"kind": "node", **vars(node)}, ensure_ascii=True, sort_keys=True) + "\n")
                for edge in edges:
                    output.write(json.dumps({"kind": "edge", **vars(edge)}, ensure_ascii=True, sort_keys=True) + "\n")
                for event in events:
                    output.write(json.dumps({"kind": "event", **event}, ensure_ascii=True, sort_keys=True) + "\n")
            temp_path.replace(path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def integrity_check(self) -> dict[str, bool | int]:
        conn = self.connect()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS dangling_edges
                FROM memory_edges AS edge
                LEFT JOIN memory_nodes AS from_node ON from_node.id = edge.from_node_id
                LEFT JOIN memory_nodes AS to_node ON to_node.id = edge.to_node_id
                WHERE from_node.id IS NULL OR to_node.id IS NULL
                """
            ).fetchone()
            dangling_edges = int(row["dangling_edges"])
            return {"ok": dangling_edges == 0, "dangling_edges": dangling_edges}
        finally:
            conn.close()

    def rebuild_indexes(self) -> None:
        conn = self.connect()
        try:
            conn.execute("DELETE FROM memory_nodes_fts")
            rows = conn.execute("SELECT id, title, summary, tags FROM memory_nodes ORDER BY id").fetchall()
            conn.executemany(
                "INSERT INTO memory_nodes_fts (id, title, summary, tags) VALUES (?, ?, ?, ?)",
                [
                    (
                        row["id"],
                        _fts_document(row["title"]),
                        _fts_document(row["summary"]),
                        _fts_document(" ".join(json.loads(row["tags"]))),
                    )
                    for row in rows
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def _normalize_fts_query(self, query: str) -> str:
        tokens = _FTS_TOKEN_RE.findall(query)
        expanded: list[str] = []
        for token in tokens:
            expanded.append(token)
            expanded.extend(_han_ngrams(token))
        return " OR ".join(dict.fromkeys(expanded))

    def _list_events(self, conn: sqlite3.Connection) -> list[dict[str, object]]:
        rows = conn.execute(
            """
            SELECT event_type, subject_id, payload_json, created_at
            FROM memory_events
            ORDER BY id
            """
        ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "subject_id": row["subject_id"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
