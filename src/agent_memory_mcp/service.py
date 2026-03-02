from __future__ import annotations

import textwrap
import uuid
from dataclasses import dataclass
from typing import Any

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import Embedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.models import utc_now_iso
from agent_memory_mcp.vector_store import MemoryVectorStore

BASELINE_POLICY = textwrap.dedent(
    """
    # AGENT Policy

    ## Core
    - Treat raw session data as immutable evidence.
    - Never promote policy changes without evaluation.
    - Keep safety constraints ahead of style or speed.

    ## Memory
    - Save sessions as structured records first.
    - Generate markdown as a derivative artifact, not canonical source.
    - Support rollback for every promoted policy change.
    """
).strip()

SUPPORTED_JOB_TYPES = {"memory.distill", "policy.evaluate"}


@dataclass
class MemoryPolicyService:
    db: Database
    embedder: Embedder
    evaluator: PolicyEvaluator
    vector_store: MemoryVectorStore
    default_namespace: str = "default"

    def _ns(self, namespace: str | None) -> str:
        if namespace and namespace.strip():
            return namespace.strip()
        return self.default_namespace

    @staticmethod
    def _coerce_positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return parsed if parsed > 0 else default

    def append_event(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
        namespace: str | None = None,
    ) -> dict:
        ns = self._ns(namespace)
        now = utc_now_iso()
        clean_metadata = metadata or {}
        self.db.upsert_session(
            namespace=ns,
            session_id=session_id,
            started_at=now,
            metadata={"source": "mcp"},
        )
        event_id = self.db.append_event(
            namespace=ns,
            session_id=session_id,
            role=role,
            content=content,
            created_at=now,
            metadata=clean_metadata,
        )
        return {"event_id": event_id, "namespace": ns, "session_id": session_id, "created_at": now}

    def _distill_session_sync(self, session_id: str, max_lines: int, namespace: str) -> dict:
        events = self.db.list_events(namespace=namespace, session_id=session_id)
        if not events:
            raise ValueError(f"session '{session_id}' has no events in namespace '{namespace}'")

        lines: list[str] = []
        for event in events[-max_lines:]:
            snippet = event["content"].strip().replace("\n", " ")[:160]
            lines.append(f"- {event['role']}: {snippet}")

        summary = (
            f"Session {session_id} (namespace={namespace}) distilled from {len(events)} events.\n"
            "Key excerpts:\n"
            + "\n".join(lines)
        )

        now = utc_now_iso()
        embedding = self.embedder.embed(summary)
        metadata = {
            "kind": "session_distill",
            "event_count": len(events),
            "embedding_backend": self.embedder.backend_name,
            "embedding_dimensions": len(embedding),
            "vector_store_backend": self.vector_store.backend_name,
        }
        memory_id = self.db.insert_memory(
            namespace=namespace,
            session_id=session_id,
            content=summary,
            embedding=embedding,
            created_at=now,
            metadata=metadata,
        )
        self.vector_store.upsert(
            memory_id=memory_id,
            namespace=namespace,
            session_id=session_id,
            vector=embedding,
            metadata=metadata,
        )

        return {
            "memory_id": memory_id,
            "namespace": namespace,
            "session_id": session_id,
            "summary": summary,
            "created_at": now,
        }

    def distill_session(
        self,
        session_id: str,
        max_lines: int = 6,
        namespace: str | None = None,
        async_mode: bool = False,
    ) -> dict:
        ns = self._ns(namespace)
        resolved_max_lines = self._coerce_positive_int(max_lines, default=6)
        if async_mode:
            return self.jobs_submit(
                job_type="memory.distill",
                payload={"session_id": session_id, "max_lines": resolved_max_lines},
                namespace=ns,
            )
        return self._distill_session_sync(session_id=session_id, max_lines=resolved_max_lines, namespace=ns)

    def memory_search(self, query: str, k: int = 5, namespace: str | None = None) -> list[dict]:
        ns = self._ns(namespace)
        query_vector = self.embedder.embed(query)
        hits = self.vector_store.search(namespace=ns, query_vector=query_vector, k=k)
        if not hits:
            return []

        ids = [hit.memory_id for hit in hits]
        memories = self.db.get_memories_by_ids(namespace=ns, memory_ids=ids)
        by_id = {int(memory["id"]): memory for memory in memories}

        results: list[dict] = []
        for hit in hits:
            memory = by_id.get(hit.memory_id)
            if memory is None:
                continue
            results.append(
                {
                    "memory_id": memory["id"],
                    "namespace": ns,
                    "session_id": memory["session_id"],
                    "score": round(hit.score, 4),
                    "content": memory["content"],
                    "metadata": memory["metadata"],
                }
            )
        return results

    def policy_get(self, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        active = self.db.get_active_policy_version(namespace=ns)
        if active:
            return active

        now = utc_now_iso()
        version_id = f"baseline-{uuid.uuid4().hex[:10]}"
        self.db.create_policy_version(
            namespace=ns,
            version_id=version_id,
            content_md=BASELINE_POLICY,
            source_proposal_id=None,
            is_active=True,
            created_at=now,
        )
        return self.db.get_active_policy_version(namespace=ns) or {}

    def policy_propose(
        self,
        delta_md: str,
        evidence_refs: list[str] | None = None,
        namespace: str | None = None,
    ) -> dict:
        ns = self._ns(namespace)
        proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        self.db.create_policy_proposal(
            namespace=ns,
            proposal_id=proposal_id,
            delta_md=delta_md.strip(),
            evidence_refs=evidence_refs or [],
            status="proposed",
            created_at=now,
        )
        return self.db.get_policy_proposal(namespace=ns, proposal_id=proposal_id) or {
            "namespace": ns,
            "proposal_id": proposal_id,
        }

    def _policy_evaluate_sync(self, proposal_id: str, namespace: str) -> dict:
        proposal = self.db.get_policy_proposal(namespace=namespace, proposal_id=proposal_id)
        if proposal is None:
            raise ValueError(f"proposal '{proposal_id}' not found in namespace '{namespace}'")

        eval_result = self.evaluator.evaluate(
            delta_md=proposal["delta_md"],
            evidence_refs=proposal["evidence_refs"],
        )

        now = utc_now_iso()
        eval_id = self.db.add_policy_evaluation(
            namespace=namespace,
            proposal_id=proposal_id,
            score=eval_result["score"],
            passed=eval_result["passed"],
            report=eval_result["report"],
            checks=eval_result["checks"],
            created_at=now,
        )
        self.db.set_proposal_status(namespace=namespace, proposal_id=proposal_id, status="evaluated")

        return {
            "evaluation_id": eval_id,
            "namespace": namespace,
            "proposal_id": proposal_id,
            "score": eval_result["score"],
            "passed": eval_result["passed"],
            "report": eval_result["report"],
            "checks": eval_result["checks"],
            "regression": eval_result["regression"],
            "created_at": now,
        }

    def policy_evaluate(
        self,
        proposal_id: str,
        namespace: str | None = None,
        async_mode: bool = False,
    ) -> dict:
        ns = self._ns(namespace)
        if async_mode:
            return self.jobs_submit(
                job_type="policy.evaluate",
                payload={"proposal_id": proposal_id},
                namespace=ns,
            )
        return self._policy_evaluate_sync(proposal_id=proposal_id, namespace=ns)

    def policy_promote(self, proposal_id: str, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        proposal = self.db.get_policy_proposal(namespace=ns, proposal_id=proposal_id)
        if proposal is None:
            raise ValueError(f"proposal '{proposal_id}' not found in namespace '{ns}'")

        latest = self.db.latest_evaluation(namespace=ns, proposal_id=proposal_id)
        if latest is None or not latest["passed"]:
            raise ValueError("proposal must have a passing evaluation before promotion")

        current = self.policy_get(namespace=ns)["content_md"]
        next_md = (
            f"{current}\n\n"
            f"## Delta {proposal_id}\n"
            f"{proposal['delta_md'].strip()}\n"
        ).strip()

        version_id = f"ver-{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        self.db.create_policy_version(
            namespace=ns,
            version_id=version_id,
            content_md=next_md,
            source_proposal_id=proposal_id,
            is_active=True,
            created_at=now,
        )
        self.db.set_proposal_status(namespace=ns, proposal_id=proposal_id, status="promoted")

        return {
            "namespace": ns,
            "version_id": version_id,
            "proposal_id": proposal_id,
            "is_active": True,
            "created_at": now,
        }

    def policy_rollback(self, version_id: str, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        ok = self.db.set_active_policy_version(namespace=ns, version_id=version_id)
        if not ok:
            raise ValueError(f"policy version '{version_id}' not found in namespace '{ns}'")
        active = self.db.get_active_policy_version(namespace=ns) or {}
        return {
            "namespace": ns,
            "version_id": active.get("version_id", version_id),
            "is_active": active.get("is_active", True),
            "created_at": active.get("created_at"),
        }

    def jobs_submit(self, job_type: str, payload: dict[str, Any], namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        normalized_job_type = job_type.strip()
        if normalized_job_type not in SUPPORTED_JOB_TYPES:
            raise ValueError(f"unsupported job_type '{job_type}'")

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
            created_at=now,
        )
        return {
            "job_id": job_id,
            "namespace": ns,
            "job_type": normalized_job_type,
            "status": "queued",
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
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
        }

    def jobs_run_pending(self, limit: int = 1, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        resolved_limit = self._coerce_positive_int(limit, default=1)
        jobs: list[dict[str, Any]] = []

        for _ in range(resolved_limit):
            now = utc_now_iso()
            job = self.db.claim_next_queued_job(namespace=ns, now=now)
            if job is None:
                break

            job_id = int(job["job_id"])
            job_type = str(job["job_type"])
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
                    }
                )
            except Exception as exc:
                finished_at = utc_now_iso()
                self.db.finish_job_failure(
                    namespace=ns,
                    job_id=job_id,
                    error_text=str(exc),
                    now=finished_at,
                )
                jobs.append(
                    {
                        "job_id": job_id,
                        "job_type": job_type,
                        "status": "failed",
                        "error": str(exc),
                    }
                )

        succeeded = sum(1 for item in jobs if item["status"] == "succeeded")
        failed = sum(1 for item in jobs if item["status"] == "failed")
        return {
            "namespace": ns,
            "processed": len(jobs),
            "succeeded": succeeded,
            "failed": failed,
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
