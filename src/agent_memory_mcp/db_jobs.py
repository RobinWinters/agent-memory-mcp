from __future__ import annotations

import json
from datetime import datetime
from typing import Any


class DatabaseJobsMixin:
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
