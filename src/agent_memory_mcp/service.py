from __future__ import annotations

import textwrap
import uuid
from dataclasses import dataclass

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import Embedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.models import utc_now_iso

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


@dataclass
class MemoryPolicyService:
    db: Database
    embedder: Embedder
    evaluator: PolicyEvaluator
    default_namespace: str = "default"

    def _ns(self, namespace: str | None) -> str:
        if namespace and namespace.strip():
            return namespace.strip()
        return self.default_namespace

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

    def distill_session(self, session_id: str, max_lines: int = 6, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        events = self.db.list_events(namespace=ns, session_id=session_id)
        if not events:
            raise ValueError(f"session '{session_id}' has no events in namespace '{ns}'")

        lines: list[str] = []
        for event in events[-max_lines:]:
            snippet = event["content"].strip().replace("\n", " ")[:160]
            lines.append(f"- {event['role']}: {snippet}")

        summary = (
            f"Session {session_id} (namespace={ns}) distilled from {len(events)} events.\n"
            "Key excerpts:\n"
            + "\n".join(lines)
        )

        now = utc_now_iso()
        embedding = self.embedder.embed(summary)
        memory_id = self.db.insert_memory(
            namespace=ns,
            session_id=session_id,
            content=summary,
            embedding=embedding,
            created_at=now,
            metadata={
                "kind": "session_distill",
                "event_count": len(events),
                "embedding_backend": self.embedder.backend_name,
                "embedding_dimensions": len(embedding),
            },
        )

        return {
            "memory_id": memory_id,
            "namespace": ns,
            "session_id": session_id,
            "summary": summary,
            "created_at": now,
        }

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        return sum(x * y for x, y in zip(a, b, strict=True))

    def memory_search(self, query: str, k: int = 5, namespace: str | None = None) -> list[dict]:
        ns = self._ns(namespace)
        memories = self.db.list_memories(namespace=ns)
        if not memories:
            return []

        qvec = self.embedder.embed(query)
        scored = []
        for memory in memories:
            if len(memory["embedding"]) != len(qvec):
                continue
            score = self._cosine_similarity(qvec, memory["embedding"])
            scored.append((score, memory))
        scored.sort(key=lambda item: item[0], reverse=True)

        top = scored[: max(k, 1)]
        return [
            {
                "memory_id": memory["id"],
                "namespace": ns,
                "session_id": memory["session_id"],
                "score": round(score, 4),
                "content": memory["content"],
                "metadata": memory["metadata"],
            }
            for score, memory in top
        ]

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

    def policy_evaluate(self, proposal_id: str, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        proposal = self.db.get_policy_proposal(namespace=ns, proposal_id=proposal_id)
        if proposal is None:
            raise ValueError(f"proposal '{proposal_id}' not found in namespace '{ns}'")

        eval_result = self.evaluator.evaluate(
            delta_md=proposal["delta_md"],
            evidence_refs=proposal["evidence_refs"],
        )

        now = utc_now_iso()
        eval_id = self.db.add_policy_evaluation(
            namespace=ns,
            proposal_id=proposal_id,
            score=eval_result["score"],
            passed=eval_result["passed"],
            report=eval_result["report"],
            checks=eval_result["checks"],
            created_at=now,
        )
        self.db.set_proposal_status(namespace=ns, proposal_id=proposal_id, status="evaluated")

        return {
            "evaluation_id": eval_id,
            "namespace": ns,
            "proposal_id": proposal_id,
            "score": eval_result["score"],
            "passed": eval_result["passed"],
            "report": eval_result["report"],
            "checks": eval_result["checks"],
            "regression": eval_result["regression"],
            "created_at": now,
        }

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
