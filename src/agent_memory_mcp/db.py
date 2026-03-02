from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


NAMESPACED_TABLES = [
    "sessions",
    "events",
    "memories",
    "policy_proposals",
    "policy_evaluations",
    "policy_versions",
]


class Database:
    def __init__(self, db_path: str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL DEFAULT 'default',
                started_at TEXT NOT NULL,
                ended_at TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL DEFAULT 'default',
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL DEFAULT 'default',
                session_id TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_proposals (
                proposal_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL DEFAULT 'default',
                delta_md TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL DEFAULT 'default',
                proposal_id TEXT NOT NULL,
                score REAL NOT NULL,
                passed INTEGER NOT NULL,
                report TEXT NOT NULL,
                checks_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_versions (
                version_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL DEFAULT 'default',
                content_md TEXT NOT NULL,
                source_proposal_id TEXT,
                is_active INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

        self._migrate_existing_tables()

        self.conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_sessions_namespace ON sessions(namespace, session_id);
            CREATE INDEX IF NOT EXISTS idx_events_ns_session ON events(namespace, session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_memories_ns_session ON memories(namespace, session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_policy_prop_ns ON policy_proposals(namespace, proposal_id);
            CREATE INDEX IF NOT EXISTS idx_policy_eval_ns_proposal ON policy_evaluations(namespace, proposal_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_policy_ver_ns_active ON policy_versions(namespace, is_active, created_at);
            """
        )
        self.conn.commit()

    def _migrate_existing_tables(self) -> None:
        for table in NAMESPACED_TABLES:
            if not self._table_has_column(table, "namespace"):
                self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN namespace TEXT NOT NULL DEFAULT 'default'"
                )

        if not self._table_has_column("policy_evaluations", "checks_json"):
            self.conn.execute(
                "ALTER TABLE policy_evaluations ADD COLUMN checks_json TEXT NOT NULL DEFAULT '[]'"
            )

        self.conn.commit()

    def _table_has_column(self, table_name: str, column_name: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(row["name"] == column_name for row in rows)

    @staticmethod
    def _session_pk(namespace: str, session_id: str) -> str:
        return f"{namespace}::{session_id}"

    def upsert_session(
        self,
        namespace: str,
        session_id: str,
        started_at: str,
        metadata: dict[str, Any],
    ) -> None:
        session_pk = self._session_pk(namespace=namespace, session_id=session_id)
        self.conn.execute(
            """
            INSERT INTO sessions(session_id, namespace, started_at, metadata_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                namespace=excluded.namespace,
                metadata_json=excluded.metadata_json
            """,
            (session_pk, namespace, started_at, json.dumps(metadata)),
        )
        self.conn.commit()

    def append_event(
        self,
        namespace: str,
        session_id: str,
        role: str,
        content: str,
        created_at: str,
        metadata: dict[str, Any],
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO events(namespace, session_id, role, content, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (namespace, session_id, role, content, created_at, json.dumps(metadata)),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_events(self, namespace: str, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, namespace, session_id, role, content, created_at, metadata_json
            FROM events
            WHERE namespace=? AND session_id=?
            ORDER BY id ASC
            """,
            (namespace, session_id),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "namespace": row["namespace"],
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def insert_memory(
        self,
        namespace: str,
        session_id: str,
        content: str,
        embedding: list[float],
        created_at: str,
        metadata: dict[str, Any],
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO memories(namespace, session_id, content, embedding_json, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (namespace, session_id, content, json.dumps(embedding), created_at, json.dumps(metadata)),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_memories(self, namespace: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, namespace, session_id, content, embedding_json, created_at, metadata_json
            FROM memories
            WHERE namespace=?
            ORDER BY id ASC
            """,
            (namespace,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "namespace": row["namespace"],
                "session_id": row["session_id"],
                "content": row["content"],
                "embedding": json.loads(row["embedding_json"]),
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def get_memories_by_ids(self, namespace: str, memory_ids: list[int]) -> list[dict[str, Any]]:
        if not memory_ids:
            return []
        placeholders = ",".join("?" for _ in memory_ids)
        params: list[Any] = [namespace, *memory_ids]
        rows = self.conn.execute(
            f"""
            SELECT id, namespace, session_id, content, embedding_json, created_at, metadata_json
            FROM memories
            WHERE namespace=? AND id IN ({placeholders})
            """,
            params,
        ).fetchall()
        by_id: dict[int, dict[str, Any]] = {
            int(row["id"]): {
                "id": row["id"],
                "namespace": row["namespace"],
                "session_id": row["session_id"],
                "content": row["content"],
                "embedding": json.loads(row["embedding_json"]),
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        }
        ordered: list[dict[str, Any]] = []
        for memory_id in memory_ids:
            item = by_id.get(int(memory_id))
            if item is not None:
                ordered.append(item)
        return ordered

    def create_policy_proposal(
        self,
        namespace: str,
        proposal_id: str,
        delta_md: str,
        evidence_refs: list[str],
        status: str,
        created_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO policy_proposals(namespace, proposal_id, delta_md, evidence_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (namespace, proposal_id, delta_md, json.dumps(evidence_refs), status, created_at),
        )
        self.conn.commit()

    def set_proposal_status(self, namespace: str, proposal_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE policy_proposals SET status=? WHERE namespace=? AND proposal_id=?",
            (status, namespace, proposal_id),
        )
        self.conn.commit()

    def get_policy_proposal(self, namespace: str, proposal_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT namespace, proposal_id, delta_md, evidence_json, status, created_at
            FROM policy_proposals
            WHERE namespace=? AND proposal_id=?
            """,
            (namespace, proposal_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "namespace": row["namespace"],
            "proposal_id": row["proposal_id"],
            "delta_md": row["delta_md"],
            "evidence_refs": json.loads(row["evidence_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def add_policy_evaluation(
        self,
        namespace: str,
        proposal_id: str,
        score: float,
        passed: bool,
        report: str,
        checks: list[dict[str, Any]],
        created_at: str,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO policy_evaluations(namespace, proposal_id, score, passed, report, checks_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (namespace, proposal_id, score, int(passed), report, json.dumps(checks), created_at),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def latest_evaluation(self, namespace: str, proposal_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, namespace, proposal_id, score, passed, report, checks_json, created_at
            FROM policy_evaluations
            WHERE namespace=? AND proposal_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (namespace, proposal_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "namespace": row["namespace"],
            "proposal_id": row["proposal_id"],
            "score": row["score"],
            "passed": bool(row["passed"]),
            "report": row["report"],
            "checks": json.loads(row["checks_json"]),
            "created_at": row["created_at"],
        }

    def create_policy_version(
        self,
        namespace: str,
        version_id: str,
        content_md: str,
        source_proposal_id: str | None,
        is_active: bool,
        created_at: str,
    ) -> None:
        if is_active:
            self.conn.execute(
                "UPDATE policy_versions SET is_active=0 WHERE namespace=? AND is_active=1",
                (namespace,),
            )
        self.conn.execute(
            """
            INSERT INTO policy_versions(namespace, version_id, content_md, source_proposal_id, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (namespace, version_id, content_md, source_proposal_id, int(is_active), created_at),
        )
        self.conn.commit()

    def set_active_policy_version(self, namespace: str, version_id: str) -> bool:
        exists = self.conn.execute(
            "SELECT 1 FROM policy_versions WHERE namespace=? AND version_id=?",
            (namespace, version_id),
        ).fetchone()
        if exists is None:
            return False
        self.conn.execute(
            "UPDATE policy_versions SET is_active=0 WHERE namespace=? AND is_active=1",
            (namespace,),
        )
        self.conn.execute(
            "UPDATE policy_versions SET is_active=1 WHERE namespace=? AND version_id=?",
            (namespace, version_id),
        )
        self.conn.commit()
        return True

    def get_active_policy_version(self, namespace: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT namespace, version_id, content_md, source_proposal_id, is_active, created_at
            FROM policy_versions
            WHERE namespace=? AND is_active=1
            LIMIT 1
            """,
            (namespace,),
        ).fetchone()
        if row is None:
            return None
        return {
            "namespace": row["namespace"],
            "version_id": row["version_id"],
            "content_md": row["content_md"],
            "source_proposal_id": row["source_proposal_id"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }

    def get_policy_version(self, namespace: str, version_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT namespace, version_id, content_md, source_proposal_id, is_active, created_at
            FROM policy_versions
            WHERE namespace=? AND version_id=?
            """,
            (namespace, version_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "namespace": row["namespace"],
            "version_id": row["version_id"],
            "content_md": row["content_md"],
            "source_proposal_id": row["source_proposal_id"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }

    def close(self) -> None:
        self.conn.close()
