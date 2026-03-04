from __future__ import annotations

import json
from typing import Any


class DatabaseMemoryMixin:
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

    def list_events_for_namespace(
        self,
        namespace: str,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        resolved_after_id = max(0, int(after_id))
        rows = self.conn.execute(
            """
            SELECT id, namespace, session_id, role, content, created_at, metadata_json
            FROM events
            WHERE namespace=? AND id > ?
            ORDER BY id ASC
            """,
            (namespace, resolved_after_id),
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

    def get_latest_event_id(self, namespace: str) -> int:
        row = self.conn.execute(
            """
            SELECT COALESCE(MAX(id), 0) AS max_id
            FROM events
            WHERE namespace=?
            """,
            (namespace,),
        ).fetchone()
        if row is None:
            return 0
        return int(row["max_id"] or 0)

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

    def get_latest_memory_id(self, namespace: str) -> int:
        row = self.conn.execute(
            """
            SELECT COALESCE(MAX(id), 0) AS max_id
            FROM memories
            WHERE namespace=?
            """,
            (namespace,),
        ).fetchone()
        if row is None:
            return 0
        return int(row["max_id"] or 0)

    def insert_memory_outcome(
        self,
        namespace: str,
        session_id: str,
        outcome_type: str,
        summary: str,
        created_at: str,
        metadata: dict[str, Any],
        memory_id: int | None = None,
        score: float | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO memory_outcomes(
                namespace,
                session_id,
                memory_id,
                outcome_type,
                summary,
                score,
                created_at,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                namespace,
                session_id,
                memory_id,
                outcome_type,
                summary,
                score,
                created_at,
                json.dumps(metadata),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_memory_outcomes(
        self,
        namespace: str,
        session_id: str | None = None,
        memory_id: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        where = ["namespace=?"]
        params: list[Any] = [namespace]
        if session_id:
            where.append("session_id=?")
            params.append(session_id)
        if memory_id is not None:
            where.append("memory_id=?")
            params.append(int(memory_id))
        params.append(max(1, int(limit)))
        rows = self.conn.execute(
            f"""
            SELECT
                id,
                namespace,
                session_id,
                memory_id,
                outcome_type,
                summary,
                score,
                created_at,
                metadata_json
            FROM memory_outcomes
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            {
                "id": row["id"],
                "namespace": row["namespace"],
                "session_id": row["session_id"],
                "memory_id": row["memory_id"],
                "outcome_type": row["outcome_type"],
                "summary": row["summary"],
                "score": row["score"],
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def get_latest_outcome_id(self, namespace: str) -> int:
        row = self.conn.execute(
            """
            SELECT COALESCE(MAX(id), 0) AS max_id
            FROM memory_outcomes
            WHERE namespace=?
            """,
            (namespace,),
        ).fetchone()
        if row is None:
            return 0
        return int(row["max_id"] or 0)

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
