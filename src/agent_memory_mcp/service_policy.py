from __future__ import annotations

import uuid
from typing import Any

from agent_memory_mcp.integrity import build_policy_artifact, compute_audit_event_hash
from agent_memory_mcp.models import utc_now_iso
from agent_memory_mcp.service_constants import BASELINE_POLICY


class ServicePolicyMixin:
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
