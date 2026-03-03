from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from agent_memory_mcp.models import utc_now_iso
from agent_memory_mcp.service_constants import SUPPORTED_JOB_TYPES


class ServiceJobsMixin:
    def jobs_submit(self, job_type: str, payload: dict[str, Any], namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        normalized_job_type = job_type.strip()
        if normalized_job_type not in SUPPORTED_JOB_TYPES:
            raise ValueError(f"unsupported job_type '{job_type}'")

        max_attempts = self._coerce_positive_int(
            payload.get("max_attempts", self.job_default_max_attempts),
            default=self.job_default_max_attempts,
        )

        if normalized_job_type == "memory.distill":
            session_id = str(payload.get("session_id", "")).strip()
            if not session_id:
                raise ValueError("payload.session_id is required")
            normalized_payload: dict[str, Any] = {
                "session_id": session_id,
                "max_lines": self._coerce_positive_int(payload.get("max_lines", 6), default=6),
            }
        elif normalized_job_type == "policy.evaluate":
            proposal_id = str(payload.get("proposal_id", "")).strip()
            if not proposal_id:
                raise ValueError("payload.proposal_id is required")
            normalized_payload = {"proposal_id": proposal_id}
        else:
            raise ValueError(f"unsupported job_type '{job_type}'")

        now = utc_now_iso()
        job_id = self.db.create_job(
            namespace=ns,
            job_type=normalized_job_type,
            payload=normalized_payload,
            max_attempts=max_attempts,
            created_at=now,
        )
        return {
            "job_id": job_id,
            "namespace": ns,
            "job_type": normalized_job_type,
            "status": "queued",
            "attempt_count": 0,
            "max_attempts": max_attempts,
            "next_run_at": now,
            "created_at": now,
        }

    def jobs_status(self, job_id: int, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        resolved_job_id = self._coerce_positive_int(job_id, default=-1)
        if resolved_job_id < 1:
            raise ValueError("job_id must be a positive integer")
        job = self.db.get_job(namespace=ns, job_id=resolved_job_id)
        if job is None:
            raise ValueError(f"job '{resolved_job_id}' not found in namespace '{ns}'")
        return {
            "job_id": job["job_id"],
            "namespace": job["namespace"],
            "job_type": job["job_type"],
            "status": job["status"],
            "error": job["error"],
            "attempt_count": job["attempt_count"],
            "max_attempts": job["max_attempts"],
            "next_run_at": job["next_run_at"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
        }

    def jobs_result(self, job_id: int, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        resolved_job_id = self._coerce_positive_int(job_id, default=-1)
        if resolved_job_id < 1:
            raise ValueError("job_id must be a positive integer")
        job = self.db.get_job(namespace=ns, job_id=resolved_job_id)
        if job is None:
            raise ValueError(f"job '{resolved_job_id}' not found in namespace '{ns}'")
        return {
            "job_id": job["job_id"],
            "namespace": job["namespace"],
            "job_type": job["job_type"],
            "status": job["status"],
            "result": job["result"],
            "error": job["error"],
            "attempt_count": job["attempt_count"],
            "max_attempts": job["max_attempts"],
            "next_run_at": job["next_run_at"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
        }

    def jobs_run_pending(self, limit: int = 1, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        resolved_limit = self._coerce_positive_int(limit, default=1)
        jobs: list[dict[str, Any]] = []

        recovery_now = utc_now_iso()
        recovery_now_dt = datetime.fromisoformat(recovery_now)
        cutoff_dt = recovery_now_dt - timedelta(
            seconds=self._coerce_positive_float(self.job_running_timeout_seconds, default=300.0)
        )
        recovery = self.db.recover_stuck_running_jobs(
            namespace=ns,
            cutoff_started_at=cutoff_dt.isoformat(),
            now=recovery_now,
        )

        for _ in range(resolved_limit):
            now = utc_now_iso()
            job = self.db.claim_next_queued_job(namespace=ns, now=now)
            if job is None:
                break

            job_id = int(job["job_id"])
            job_type = str(job["job_type"])
            attempt_count = int(job.get("attempt_count", 1))
            max_attempts = int(job.get("max_attempts", self.job_default_max_attempts))
            try:
                result = self._execute_job(job=job)
                finished_at = utc_now_iso()
                self.db.finish_job_success(
                    namespace=ns,
                    job_id=job_id,
                    result=result,
                    now=finished_at,
                )
                jobs.append(
                    {
                        "job_id": job_id,
                        "job_type": job_type,
                        "status": "succeeded",
                        "attempt_count": attempt_count,
                        "max_attempts": max_attempts,
                    }
                )
            except Exception as exc:
                finished_at = utc_now_iso()
                error_text = str(exc)
                if attempt_count >= max_attempts:
                    self.db.dead_letter_job(
                        namespace=ns,
                        job_id=job_id,
                        error_text=error_text,
                        now=finished_at,
                    )
                    jobs.append(
                        {
                            "job_id": job_id,
                            "job_type": job_type,
                            "status": "dead",
                            "error": error_text,
                            "attempt_count": attempt_count,
                            "max_attempts": max_attempts,
                        }
                    )
                else:
                    delay_seconds = min(
                        self._coerce_positive_float(self.job_backoff_max_seconds, default=300.0),
                        self._coerce_positive_float(self.job_backoff_base_seconds, default=2.0)
                        * (2 ** max(attempt_count - 1, 0)),
                    )
                    finished_dt = datetime.fromisoformat(finished_at)
                    next_run_at = (finished_dt + timedelta(seconds=delay_seconds)).isoformat()
                    self.db.requeue_job(
                        namespace=ns,
                        job_id=job_id,
                        error_text=error_text,
                        next_run_at=next_run_at,
                        now=finished_at,
                    )
                    jobs.append(
                        {
                            "job_id": job_id,
                            "job_type": job_type,
                            "status": "retried",
                            "error": error_text,
                            "attempt_count": attempt_count,
                            "max_attempts": max_attempts,
                            "next_run_at": next_run_at,
                        }
                    )

        succeeded = sum(1 for item in jobs if item["status"] == "succeeded")
        failed = sum(1 for item in jobs if item["status"] in {"dead", "retried"})
        dead = sum(1 for item in jobs if item["status"] == "dead")
        retried = sum(1 for item in jobs if item["status"] == "retried")
        return {
            "namespace": ns,
            "processed": len(jobs),
            "succeeded": succeeded,
            "failed": failed,
            "retried": retried,
            "dead": dead,
            "recovered_stuck": recovery,
            "jobs": jobs,
        }

    def _execute_job(self, job: dict[str, Any]) -> dict:
        job_type = str(job["job_type"])
        namespace = str(job["namespace"])
        payload = dict(job["payload"])

        if job_type == "memory.distill":
            session_id = str(payload.get("session_id", "")).strip()
            if not session_id:
                raise ValueError("job payload missing session_id")
            max_lines = self._coerce_positive_int(payload.get("max_lines", 6), default=6)
            return self._distill_session_sync(session_id=session_id, max_lines=max_lines, namespace=namespace)

        if job_type == "policy.evaluate":
            proposal_id = str(payload.get("proposal_id", "")).strip()
            if not proposal_id:
                raise ValueError("job payload missing proposal_id")
            return self._policy_evaluate_sync(proposal_id=proposal_id, namespace=namespace)

        raise ValueError(f"unsupported job_type '{job_type}'")
