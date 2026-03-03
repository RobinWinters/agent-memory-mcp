from __future__ import annotations

import json
from typing import Any


class DatabaseAuditMixin:
    def get_latest_audit_hash(self, namespace: str) -> str:
        row = self.conn.execute(
            """
            SELECT event_hash
            FROM audit_logs
            WHERE namespace=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (namespace,),
        ).fetchone()
        if row is None:
            return ""
        return str(row["event_hash"])

    def append_audit_log(
        self,
        namespace: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        prev_hash: str,
        event_hash: str,
        created_at: str,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO audit_logs(
                namespace,
                event_type,
                entity_type,
                entity_id,
                payload_json,
                prev_hash,
                event_hash,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                namespace,
                event_type,
                entity_type,
                entity_id,
                json.dumps(payload, sort_keys=True),
                prev_hash,
                event_hash,
                created_at,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_audit_logs(self, namespace: str, limit: int = 100, ascending: bool = False) -> list[dict[str, Any]]:
        order = "ASC" if ascending else "DESC"
        rows = self.conn.execute(
            f"""
            SELECT id, namespace, event_type, entity_type, entity_id, payload_json, prev_hash, event_hash, created_at
            FROM audit_logs
            WHERE namespace=?
            ORDER BY id {order}
            LIMIT ?
            """,
            (namespace, max(1, int(limit))),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "namespace": row["namespace"],
                "event_type": row["event_type"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "payload": json.loads(row["payload_json"]),
                "prev_hash": row["prev_hash"],
                "event_hash": row["event_hash"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
