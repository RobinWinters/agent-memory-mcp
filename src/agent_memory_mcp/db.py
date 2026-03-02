from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


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
                started_at TEXT NOT NULL,
                ended_at TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS policy_proposals (
                proposal_id TEXT PRIMARY KEY,
                delta_md TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id TEXT NOT NULL,
                score REAL NOT NULL,
                passed INTEGER NOT NULL,
                report TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(proposal_id) REFERENCES policy_proposals(proposal_id)
            );

            CREATE TABLE IF NOT EXISTS policy_versions (
                version_id TEXT PRIMARY KEY,
                content_md TEXT NOT NULL,
                source_proposal_id TEXT,
                is_active INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(source_proposal_id) REFERENCES policy_proposals(proposal_id)
            );

            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_policy_eval_proposal ON policy_evaluations(proposal_id, created_at);
            """
        )
        self.conn.commit()

    def upsert_session(self, session_id: str, started_at: str, metadata: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO sessions(session_id, started_at, metadata_json)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                metadata_json=excluded.metadata_json
            """,
            (session_id, started_at, json.dumps(metadata)),
        )
        self.conn.commit()

    def end_session(self, session_id: str, ended_at: str) -> None:
        self.conn.execute("UPDATE sessions SET ended_at=? WHERE session_id=?", (ended_at, session_id))
        self.conn.commit()

    def append_event(
        self,
        session_id: str,
        role: str,
        content: str,
        created_at: str,
        metadata: dict[str, Any],
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO events(session_id, role, content, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, content, created_at, json.dumps(metadata)),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, session_id, role, content, created_at, metadata_json FROM events WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
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
        session_id: str,
        content: str,
        embedding: list[float],
        created_at: str,
        metadata: dict[str, Any],
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO memories(session_id, content, embedding_json, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, content, json.dumps(embedding), created_at, json.dumps(metadata)),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_memories(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, session_id, content, embedding_json, created_at, metadata_json FROM memories ORDER BY id ASC"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "content": row["content"],
                "embedding": json.loads(row["embedding_json"]),
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def create_policy_proposal(
        self,
        proposal_id: str,
        delta_md: str,
        evidence_refs: list[str],
        status: str,
        created_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO policy_proposals(proposal_id, delta_md, evidence_json, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (proposal_id, delta_md, json.dumps(evidence_refs), status, created_at),
        )
        self.conn.commit()

    def set_proposal_status(self, proposal_id: str, status: str) -> None:
        self.conn.execute("UPDATE policy_proposals SET status=? WHERE proposal_id=?", (status, proposal_id))
        self.conn.commit()

    def get_policy_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT proposal_id, delta_md, evidence_json, status, created_at FROM policy_proposals WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "proposal_id": row["proposal_id"],
            "delta_md": row["delta_md"],
            "evidence_refs": json.loads(row["evidence_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def add_policy_evaluation(
        self,
        proposal_id: str,
        score: float,
        passed: bool,
        report: str,
        created_at: str,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO policy_evaluations(proposal_id, score, passed, report, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (proposal_id, score, int(passed), report, created_at),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def latest_evaluation(self, proposal_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, proposal_id, score, passed, report, created_at
            FROM policy_evaluations
            WHERE proposal_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "proposal_id": row["proposal_id"],
            "score": row["score"],
            "passed": bool(row["passed"]),
            "report": row["report"],
            "created_at": row["created_at"],
        }

    def create_policy_version(
        self,
        version_id: str,
        content_md: str,
        source_proposal_id: str | None,
        is_active: bool,
        created_at: str,
    ) -> None:
        if is_active:
            self.conn.execute("UPDATE policy_versions SET is_active=0 WHERE is_active=1")
        self.conn.execute(
            """
            INSERT INTO policy_versions(version_id, content_md, source_proposal_id, is_active, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (version_id, content_md, source_proposal_id, int(is_active), created_at),
        )
        self.conn.commit()

    def set_active_policy_version(self, version_id: str) -> bool:
        exists = self.conn.execute(
            "SELECT 1 FROM policy_versions WHERE version_id=?",
            (version_id,),
        ).fetchone()
        if exists is None:
            return False
        self.conn.execute("UPDATE policy_versions SET is_active=0 WHERE is_active=1")
        self.conn.execute("UPDATE policy_versions SET is_active=1 WHERE version_id=?", (version_id,))
        self.conn.commit()
        return True

    def get_active_policy_version(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT version_id, content_md, source_proposal_id, is_active, created_at
            FROM policy_versions
            WHERE is_active=1
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return {
            "version_id": row["version_id"],
            "content_md": row["content_md"],
            "source_proposal_id": row["source_proposal_id"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }

    def get_policy_version(self, version_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT version_id, content_md, source_proposal_id, is_active, created_at
            FROM policy_versions
            WHERE version_id=?
            """,
            (version_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "version_id": row["version_id"],
            "content_md": row["content_md"],
            "source_proposal_id": row["source_proposal_id"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }

    def close(self) -> None:
        self.conn.close()
