from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


NAMESPACED_TABLES = [
    "sessions",
    "events",
    "memories",
    "policy_proposals",
    "policy_evaluations",
    "policy_versions",
    "jobs",
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

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL DEFAULT 'default',
                job_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error_text TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                next_run_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
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
            CREATE INDEX IF NOT EXISTS idx_jobs_ns_status_created ON jobs(namespace, status, created_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_ns_status_next_run ON jobs(namespace, status, next_run_at, id);
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

        if not self._table_has_column("jobs", "attempt_count"):
            self.conn.execute("ALTER TABLE jobs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")

        if not self._table_has_column("jobs", "max_attempts"):
            self.conn.execute("ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3")

        if not self._table_has_column("jobs", "next_run_at"):
            self.conn.execute("ALTER TABLE jobs ADD COLUMN next_run_at TEXT")
            self.conn.execute(
                """
                UPDATE jobs
                SET next_run_at = COALESCE(created_at, updated_at)
                WHERE next_run_at IS NULL OR next_run_at = ''
                """
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

    def create_job(
        self,
        namespace: str,
        job_type: str,
        payload: dict[str, Any],
        max_attempts: int,
        created_at: str,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO jobs(
                namespace,
                job_type,
                payload_json,
                status,
                result_json,
                error_text,
                attempt_count,
                max_attempts,
                next_run_at,
                created_at,
                updated_at,
                started_at,
                finished_at
            )
            VALUES (?, ?, ?, 'queued', NULL, NULL, 0, ?, ?, ?, ?, NULL, NULL)
            """,
            (namespace, job_type, json.dumps(payload), max_attempts, created_at, created_at, created_at),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def get_job(self, namespace: str, job_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT
                id,
                namespace,
                job_type,
                payload_json,
                status,
                result_json,
                error_text,
                attempt_count,
                max_attempts,
                next_run_at,
                created_at,
                updated_at,
                started_at,
                finished_at
            FROM jobs
            WHERE namespace=? AND id=?
            """,
            (namespace, job_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "job_id": int(row["id"]),
            "namespace": row["namespace"],
            "job_type": row["job_type"],
            "payload": json.loads(row["payload_json"]),
            "status": row["status"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error_text"],
            "attempt_count": int(row["attempt_count"]),
            "max_attempts": int(row["max_attempts"]),
            "next_run_at": row["next_run_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    def claim_next_queued_job(self, namespace: str, now: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id
            FROM jobs
            WHERE namespace=? AND status='queued' AND next_run_at <= ?
            ORDER BY next_run_at ASC, id ASC
            LIMIT 1
            """,
            (namespace, now),
        ).fetchone()
        if row is None:
            return None

        job_id = int(row["id"])
        cursor = self.conn.execute(
            """
            UPDATE jobs
            SET status='running', started_at=?, updated_at=?, attempt_count=attempt_count+1
            WHERE id=? AND namespace=? AND status='queued'
            """,
            (now, now, job_id, namespace),
        )
        self.conn.commit()
        if cursor.rowcount != 1:
            return None
        return self.get_job(namespace=namespace, job_id=job_id)

    def finish_job_success(
        self,
        namespace: str,
        job_id: int,
        result: dict[str, Any],
        now: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE jobs
            SET status='succeeded', result_json=?, error_text=NULL, updated_at=?, finished_at=?, next_run_at=?
            WHERE id=? AND namespace=?
            """,
            (json.dumps(result), now, now, now, job_id, namespace),
        )
        self.conn.commit()

    def requeue_job(
        self,
        namespace: str,
        job_id: int,
        error_text: str,
        next_run_at: str,
        now: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE jobs
            SET status='queued', result_json=NULL, error_text=?, updated_at=?, finished_at=NULL, started_at=NULL, next_run_at=?
            WHERE id=? AND namespace=? AND status='running'
            """,
            (error_text, now, next_run_at, job_id, namespace),
        )
        self.conn.commit()

    def dead_letter_job(
        self,
        namespace: str,
        job_id: int,
        error_text: str,
        now: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE jobs
            SET status='dead', result_json=NULL, error_text=?, updated_at=?, finished_at=?
            WHERE id=? AND namespace=?
            """,
            (error_text, now, now, job_id, namespace),
        )
        self.conn.commit()

    def recover_stuck_running_jobs(self, namespace: str, cutoff_started_at: str, now: str) -> dict[str, int]:
        requeue_cursor = self.conn.execute(
            """
            UPDATE jobs
            SET status='queued',
                updated_at=?,
                started_at=NULL,
                finished_at=NULL,
                next_run_at=?,
                error_text='Recovered stuck running job'
            WHERE namespace=?
              AND status='running'
              AND started_at IS NOT NULL
              AND started_at <= ?
              AND attempt_count < max_attempts
            """,
            (now, now, namespace, cutoff_started_at),
        )

        dead_cursor = self.conn.execute(
            """
            UPDATE jobs
            SET status='dead',
                updated_at=?,
                finished_at=?,
                error_text='Stuck running job exceeded max attempts'
            WHERE namespace=?
              AND status='running'
              AND started_at IS NOT NULL
              AND started_at <= ?
              AND attempt_count >= max_attempts
            """,
            (now, now, namespace, cutoff_started_at),
        )
        self.conn.commit()
        return {
            "requeued": int(requeue_cursor.rowcount),
            "dead_lettered": int(dead_cursor.rowcount),
        }

    def get_job_queue_health(self, namespace: str, now: str, running_cutoff_started_at: str) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS total_jobs,
                SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS queued_total,
                SUM(CASE WHEN status='queued' AND next_run_at <= ? THEN 1 ELSE 0 END) AS queued_ready,
                SUM(CASE WHEN status='queued' AND next_run_at > ? THEN 1 ELSE 0 END) AS queued_delayed,
                SUM(CASE WHEN status='queued' AND attempt_count > 0 THEN 1 ELSE 0 END) AS queued_retries,
                SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running_total,
                SUM(CASE WHEN status='running' AND started_at IS NOT NULL AND started_at <= ? THEN 1 ELSE 0 END) AS running_stuck,
                SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS succeeded_total,
                SUM(CASE WHEN status='dead' THEN 1 ELSE 0 END) AS dead_total
            FROM jobs
            WHERE namespace=?
            """,
            (now, now, running_cutoff_started_at, namespace),
        ).fetchone()

        oldest_queued = self.conn.execute(
            """
            SELECT created_at
            FROM jobs
            WHERE namespace=? AND status='queued'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (namespace,),
        ).fetchone()
        oldest_running = self.conn.execute(
            """
            SELECT started_at
            FROM jobs
            WHERE namespace=? AND status='running' AND started_at IS NOT NULL
            ORDER BY started_at ASC
            LIMIT 1
            """,
            (namespace,),
        ).fetchone()

        by_type_rows = self.conn.execute(
            """
            SELECT job_type, status, COUNT(*) AS count_value
            FROM jobs
            WHERE namespace=?
            GROUP BY job_type, status
            ORDER BY job_type, status
            """,
            (namespace,),
        ).fetchall()
        by_type: dict[str, dict[str, int]] = {}
        for item in by_type_rows:
            job_type = str(item["job_type"])
            status = str(item["status"])
            count_value = int(item["count_value"])
            bucket = by_type.setdefault(job_type, {})
            bucket[status] = count_value

        return {
            "total_jobs": int(row["total_jobs"] or 0),
            "queued_total": int(row["queued_total"] or 0),
            "queued_ready": int(row["queued_ready"] or 0),
            "queued_delayed": int(row["queued_delayed"] or 0),
            "queued_retries": int(row["queued_retries"] or 0),
            "running_total": int(row["running_total"] or 0),
            "running_stuck": int(row["running_stuck"] or 0),
            "succeeded_total": int(row["succeeded_total"] or 0),
            "dead_total": int(row["dead_total"] or 0),
            "oldest_queued_created_at": oldest_queued["created_at"] if oldest_queued is not None else None,
            "oldest_running_started_at": oldest_running["started_at"] if oldest_running is not None else None,
            "by_type": by_type,
        }

    def get_job_metrics_window(self, namespace: str, since: str, now: str) -> dict[str, Any]:
        created_total_row = self.conn.execute(
            """
            SELECT COUNT(*) AS count_value
            FROM jobs
            WHERE namespace=? AND created_at >= ? AND created_at <= ?
            """,
            (namespace, since, now),
        ).fetchone()
        completed_rows = self.conn.execute(
            """
            SELECT job_type, status, attempt_count, created_at, started_at, finished_at
            FROM jobs
            WHERE namespace=?
              AND finished_at IS NOT NULL
              AND finished_at >= ?
              AND finished_at <= ?
              AND status IN ('succeeded', 'dead')
            ORDER BY finished_at ASC
            """,
            (namespace, since, now),
        ).fetchall()

        completed_total = len(completed_rows)
        succeeded_count = sum(1 for row in completed_rows if str(row["status"]) == "succeeded")
        dead_count = sum(1 for row in completed_rows if str(row["status"]) == "dead")
        retry_events = sum(max(int(row["attempt_count"]) - 1, 0) for row in completed_rows)

        queue_latencies: list[float] = []
        run_latencies: list[float] = []
        end_to_end_latencies: list[float] = []
        by_type: dict[str, dict[str, int]] = {}

        def _seconds_between(start_iso: str | None, end_iso: str | None) -> float | None:
            if not start_iso or not end_iso:
                return None
            try:
                start_dt = datetime.fromisoformat(start_iso)
                end_dt = datetime.fromisoformat(end_iso)
            except ValueError:
                return None
            return max(0.0, (end_dt - start_dt).total_seconds())

        for row in completed_rows:
            job_type = str(row["job_type"])
            status = str(row["status"])
            stats = by_type.setdefault(job_type, {"succeeded": 0, "dead": 0})
            stats[status] = stats.get(status, 0) + 1

            queue_seconds = _seconds_between(str(row["created_at"]), row["started_at"])
            run_seconds = _seconds_between(row["started_at"], row["finished_at"])
            e2e_seconds = _seconds_between(str(row["created_at"]), row["finished_at"])

            if queue_seconds is not None:
                queue_latencies.append(queue_seconds)
            if run_seconds is not None:
                run_latencies.append(run_seconds)
            if e2e_seconds is not None:
                end_to_end_latencies.append(e2e_seconds)

        def _avg(values: list[float]) -> float:
            if not values:
                return 0.0
            return sum(values) / len(values)

        avg_attempts = 0.0
        if completed_rows:
            avg_attempts = sum(int(row["attempt_count"]) for row in completed_rows) / len(completed_rows)

        success_rate = 0.0
        if completed_total > 0:
            success_rate = succeeded_count / completed_total

        return {
            "window_start": since,
            "window_end": now,
            "created_total": int(created_total_row["count_value"] or 0),
            "completed_total": completed_total,
            "succeeded": succeeded_count,
            "dead": dead_count,
            "success_rate": success_rate,
            "retry_events": retry_events,
            "avg_attempt_count": avg_attempts,
            "avg_queue_latency_seconds": _avg(queue_latencies),
            "avg_run_latency_seconds": _avg(run_latencies),
            "avg_end_to_end_latency_seconds": _avg(end_to_end_latencies),
            "completed_by_type": by_type,
        }

    def close(self) -> None:
        self.conn.close()
