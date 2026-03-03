from __future__ import annotations

import json
from typing import Any


class DatabasePolicyMixin:
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
        content_sha256: str,
        signature: str | None,
        signing_method: str,
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
            INSERT INTO policy_versions(
                namespace,
                version_id,
                content_md,
                content_sha256,
                signature,
                signing_method,
                source_proposal_id,
                is_active,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                namespace,
                version_id,
                content_md,
                content_sha256,
                signature,
                signing_method,
                source_proposal_id,
                int(is_active),
                created_at,
            ),
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
            SELECT
                namespace,
                version_id,
                content_md,
                content_sha256,
                signature,
                signing_method,
                source_proposal_id,
                is_active,
                created_at
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
            "content_sha256": row["content_sha256"],
            "signature": row["signature"],
            "signing_method": row["signing_method"],
            "source_proposal_id": row["source_proposal_id"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }

    def get_policy_version(self, namespace: str, version_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT
                namespace,
                version_id,
                content_md,
                content_sha256,
                signature,
                signing_method,
                source_proposal_id,
                is_active,
                created_at
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
            "content_sha256": row["content_sha256"],
            "signature": row["signature"],
            "signing_method": row["signing_method"],
            "source_proposal_id": row["source_proposal_id"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }

    def list_policy_versions(self, namespace: str, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                namespace,
                version_id,
                content_md,
                content_sha256,
                signature,
                signing_method,
                source_proposal_id,
                is_active,
                created_at
            FROM policy_versions
            WHERE namespace=?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (namespace, max(1, int(limit))),
        ).fetchall()
        return [
            {
                "namespace": row["namespace"],
                "version_id": row["version_id"],
                "content_md": row["content_md"],
                "content_sha256": row["content_sha256"],
                "signature": row["signature"],
                "signing_method": row["signing_method"],
                "source_proposal_id": row["source_proposal_id"],
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
