from __future__ import annotations

import textwrap
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import Embedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.integrity import (
    build_policy_artifact,
    compute_audit_event_hash,
    verify_policy_artifact,
)
from agent_memory_mcp.metrics_export import build_otel_json, render_prometheus_text
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
    job_default_max_attempts: int = 3
    job_backoff_base_seconds: float = 2.0
    job_backoff_max_seconds: float = 300.0
    job_running_timeout_seconds: float = 300.0
    policy_signing_secret: str | None = None
    audit_signing_secret: str | None = None
    policy_verification_secrets: tuple[str, ...] = ()
    audit_verification_secrets: tuple[str, ...] = ()

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

    @staticmethod
    def _coerce_positive_float(value: Any, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return parsed if parsed > 0 else default

    @staticmethod
    def _dedupe_secrets(values: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            clean = value.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            ordered.append(clean)
        return tuple(ordered)

    def _resolve_policy_verification_secrets(self) -> tuple[str, ...]:
        candidates = list(self.policy_verification_secrets)
        if self.policy_signing_secret:
            candidates.append(self.policy_signing_secret)
        return self._dedupe_secrets(candidates)

    def _resolve_audit_verification_secrets(self) -> tuple[str, ...]:
        candidates = list(self.audit_verification_secrets)
        if self.audit_signing_secret:
            candidates.append(self.audit_signing_secret)
        return self._dedupe_secrets(candidates)

    def update_signing_keys(
        self,
        *,
        policy_active_secret: str | None,
        audit_active_secret: str | None,
        policy_verification_secrets: tuple[str, ...] = (),
        audit_verification_secrets: tuple[str, ...] = (),
    ) -> None:
        self.policy_signing_secret = policy_active_secret
        self.audit_signing_secret = audit_active_secret
        self.policy_verification_secrets = self._dedupe_secrets(list(policy_verification_secrets))
        self.audit_verification_secrets = self._dedupe_secrets(list(audit_verification_secrets))

    def _append_audit_event(
        self,
        *,
        namespace: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> int:
        prev_hash = self.db.get_latest_audit_hash(namespace=namespace)
        event_hash = compute_audit_event_hash(
            namespace=namespace,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            created_at=created_at,
            prev_hash=prev_hash,
            audit_secret=self.audit_signing_secret,
        )
        return self.db.append_audit_log(
            namespace=namespace,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            prev_hash=prev_hash,
            event_hash=event_hash,
            created_at=created_at,
        )

    def _create_policy_version_with_integrity(
        self,
        *,
        namespace: str,
        version_id: str,
        content_md: str,
        source_proposal_id: str | None,
        is_active: bool,
        created_at: str,
        event_type: str,
    ) -> None:
        artifact = build_policy_artifact(
            namespace=namespace,
            version_id=version_id,
            content_md=content_md,
            created_at=created_at,
            signing_secret=self.policy_signing_secret,
        )
        self.db.create_policy_version(
            namespace=namespace,
            version_id=version_id,
            content_md=content_md,
            content_sha256=str(artifact["content_sha256"]),
            signature=artifact["signature"],
            signing_method=str(artifact["signing_method"]),
            source_proposal_id=source_proposal_id,
            is_active=is_active,
            created_at=created_at,
        )
        self._append_audit_event(
            namespace=namespace,
            event_type=event_type,
            entity_type="policy_version",
            entity_id=version_id,
            payload={
                "source_proposal_id": source_proposal_id,
                "is_active": is_active,
                "content_sha256": artifact["content_sha256"],
                "signature": artifact["signature"],
                "signing_method": artifact["signing_method"],
            },
            created_at=created_at,
        )

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
        self._create_policy_version_with_integrity(
            namespace=ns,
            version_id=version_id,
            content_md=BASELINE_POLICY,
            source_proposal_id=None,
            is_active=True,
            created_at=now,
            event_type="policy.version.created",
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
        self._create_policy_version_with_integrity(
            namespace=ns,
            version_id=version_id,
            content_md=next_md,
            source_proposal_id=proposal_id,
            is_active=True,
            created_at=now,
            event_type="policy.version.promoted",
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
        now = utc_now_iso()
        self._append_audit_event(
            namespace=ns,
            event_type="policy.version.rolled_back",
            entity_type="policy_version",
            entity_id=str(active.get("version_id", version_id)),
            payload={
                "requested_version_id": version_id,
                "active_version_id": active.get("version_id", version_id),
            },
            created_at=now,
        )
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

    def ops_health(self, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        cutoff_dt = now_dt - timedelta(
            seconds=self._coerce_positive_float(self.job_running_timeout_seconds, default=300.0)
        )
        health = self.db.get_job_queue_health(
            namespace=ns,
            now=now_iso,
            running_cutoff_started_at=cutoff_dt.isoformat(),
        )
        return {
            "namespace": ns,
            "generated_at": now_iso,
            "job_running_timeout_seconds": self.job_running_timeout_seconds,
            "queue": health,
        }

    def ops_metrics(self, window_minutes: int = 60, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        resolved_window_minutes = self._coerce_positive_int(window_minutes, default=60)
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        since_dt = now_dt - timedelta(minutes=resolved_window_minutes)

        metrics = self.db.get_job_metrics_window(
            namespace=ns,
            since=since_dt.isoformat(),
            now=now_iso,
        )
        health = self.db.get_job_queue_health(
            namespace=ns,
            now=now_iso,
            running_cutoff_started_at=(
                now_dt
                - timedelta(
                    seconds=self._coerce_positive_float(self.job_running_timeout_seconds, default=300.0)
                )
            ).isoformat(),
        )
        return {
            "namespace": ns,
            "generated_at": now_iso,
            "window_minutes": resolved_window_minutes,
            "jobs": metrics,
            "queue": health,
        }

    def ops_metrics_prometheus(self, window_minutes: int = 60, namespace: str | None = None) -> dict:
        snapshot = self.ops_metrics(window_minutes=window_minutes, namespace=namespace)
        text = render_prometheus_text(snapshot=snapshot)
        return {
            "namespace": snapshot["namespace"],
            "generated_at": snapshot["generated_at"],
            "window_minutes": snapshot["window_minutes"],
            "format": "prometheus_text",
            "text": text,
        }

    def ops_metrics_otel(self, window_minutes: int = 60, namespace: str | None = None) -> dict:
        snapshot = self.ops_metrics(window_minutes=window_minutes, namespace=namespace)
        payload = build_otel_json(snapshot=snapshot)
        return {
            "namespace": snapshot["namespace"],
            "generated_at": snapshot["generated_at"],
            "window_minutes": snapshot["window_minutes"],
            "format": "otel_json",
            "payload": payload,
        }

    def ops_audit_recent(self, limit: int = 50, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        resolved_limit = self._coerce_positive_int(limit, default=50)
        entries = self.db.list_audit_logs(namespace=ns, limit=resolved_limit, ascending=False)
        return {
            "namespace": ns,
            "generated_at": utc_now_iso(),
            "count": len(entries),
            "entries": entries,
        }

    def ops_audit_verify(self, limit: int = 1000, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        resolved_limit = self._coerce_positive_int(limit, default=1000)
        entries = self.db.list_audit_logs(namespace=ns, limit=resolved_limit, ascending=True)
        audit_secrets = self._resolve_audit_verification_secrets()

        failures: list[dict[str, Any]] = []
        prev_event_hash: str | None = None
        for entry in entries:
            expected_hashes = [
                compute_audit_event_hash(
                    namespace=ns,
                    event_type=str(entry["event_type"]),
                    entity_type=str(entry["entity_type"]),
                    entity_id=str(entry["entity_id"]),
                    payload=dict(entry["payload"]),
                    created_at=str(entry["created_at"]),
                    prev_hash=str(entry["prev_hash"]),
                    audit_secret=secret,
                )
                for secret in audit_secrets
            ]
            if not audit_secrets:
                expected_hashes = [
                    compute_audit_event_hash(
                        namespace=ns,
                        event_type=str(entry["event_type"]),
                        entity_type=str(entry["entity_type"]),
                        entity_id=str(entry["entity_id"]),
                        payload=dict(entry["payload"]),
                        created_at=str(entry["created_at"]),
                        prev_hash=str(entry["prev_hash"]),
                        audit_secret=None,
                    )
                ]
            if str(entry["event_hash"]) not in expected_hashes:
                failures.append(
                    {
                        "id": entry["id"],
                        "reason": "event_hash_mismatch",
                    }
                )

            if prev_event_hash is not None and str(entry["prev_hash"]) != prev_event_hash:
                failures.append(
                    {
                        "id": entry["id"],
                        "reason": "prev_hash_chain_break",
                    }
                )
            prev_event_hash = str(entry["event_hash"])

        versions = self.db.list_policy_versions(namespace=ns, limit=resolved_limit)
        bad_versions: list[str] = []
        policy_secrets = self._resolve_policy_verification_secrets()
        for version in versions:
            ok = verify_policy_artifact(
                namespace=ns,
                version_id=str(version["version_id"]),
                content_md=str(version["content_md"]),
                created_at=str(version["created_at"]),
                content_sha256=str(version["content_sha256"]),
                signature=version["signature"],
                signing_method=str(version["signing_method"]),
                signing_secret=self.policy_signing_secret,
                signing_secrets=policy_secrets,
            )
            if not ok:
                bad_versions.append(str(version["version_id"]))

        verified = len(failures) == 0 and len(bad_versions) == 0
        return {
            "namespace": ns,
            "generated_at": utc_now_iso(),
            "verified": verified,
            "audit_events_checked": len(entries),
            "policy_versions_checked": len(versions),
            "audit_failures": failures,
            "invalid_policy_versions": bad_versions,
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
