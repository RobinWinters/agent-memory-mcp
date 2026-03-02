from __future__ import annotations

import textwrap
import uuid
from dataclasses import dataclass

from agent_memory_mcp.db import Database
from agent_memory_mcp.models import utc_now_iso
from agent_memory_mcp.vector_index import SimpleVectorIndex

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

HARD_BLOCK_PATTERNS = [
    "ignore safety",
    "disable evaluation",
    "always obey",
    "delete logs",
    "persist user prompt injection",
]


@dataclass
class MemoryPolicyService:
    db: Database
    index: SimpleVectorIndex

    def append_event(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> dict:
        now = utc_now_iso()
        clean_metadata = metadata or {}
        self.db.upsert_session(session_id=session_id, started_at=now, metadata={"source": "mcp"})
        event_id = self.db.append_event(
            session_id=session_id,
            role=role,
            content=content,
            created_at=now,
            metadata=clean_metadata,
        )
        return {"event_id": event_id, "session_id": session_id, "created_at": now}

    def distill_session(self, session_id: str, max_lines: int = 6) -> dict:
        events = self.db.list_events(session_id)
        if not events:
            raise ValueError(f"session '{session_id}' has no events")

        lines: list[str] = []
        for event in events[-max_lines:]:
            snippet = event["content"].strip().replace("\n", " ")[:160]
            lines.append(f"- {event['role']}: {snippet}")

        summary = (
            f"Session {session_id} distilled from {len(events)} events.\n"
            "Key excerpts:\n"
            + "\n".join(lines)
        )

        now = utc_now_iso()
        embedding = self.index.embed(summary)
        memory_id = self.db.insert_memory(
            session_id=session_id,
            content=summary,
            embedding=embedding,
            created_at=now,
            metadata={"kind": "session_distill", "event_count": len(events)},
        )

        return {
            "memory_id": memory_id,
            "session_id": session_id,
            "summary": summary,
            "created_at": now,
        }

    def memory_search(self, query: str, k: int = 5) -> list[dict]:
        memories = self.db.list_memories()
        if not memories:
            return []

        qvec = self.index.embed(query)
        scored = []
        for memory in memories:
            score = self.index.cosine_similarity(qvec, memory["embedding"])
            scored.append((score, memory))
        scored.sort(key=lambda item: item[0], reverse=True)

        top = scored[: max(k, 1)]
        return [
            {
                "memory_id": memory["id"],
                "session_id": memory["session_id"],
                "score": round(score, 4),
                "content": memory["content"],
                "metadata": memory["metadata"],
            }
            for score, memory in top
        ]

    def policy_get(self) -> dict:
        active = self.db.get_active_policy_version()
        if active:
            return active

        now = utc_now_iso()
        version_id = f"baseline-{uuid.uuid4().hex[:10]}"
        self.db.create_policy_version(
            version_id=version_id,
            content_md=BASELINE_POLICY,
            source_proposal_id=None,
            is_active=True,
            created_at=now,
        )
        return self.db.get_active_policy_version() or {}

    def policy_propose(self, delta_md: str, evidence_refs: list[str] | None = None) -> dict:
        proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        self.db.create_policy_proposal(
            proposal_id=proposal_id,
            delta_md=delta_md.strip(),
            evidence_refs=evidence_refs or [],
            status="proposed",
            created_at=now,
        )
        return self.db.get_policy_proposal(proposal_id) or {"proposal_id": proposal_id}

    def policy_evaluate(self, proposal_id: str) -> dict:
        proposal = self.db.get_policy_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"proposal '{proposal_id}' not found")

        text = proposal["delta_md"].lower()
        score = 1.0
        notes = []

        if len(text.strip()) < 40:
            score -= 0.2
            notes.append("Proposal too short; likely underspecified.")

        for pattern in HARD_BLOCK_PATTERNS:
            if pattern in text:
                score -= 0.75
                notes.append(f"Blocked phrase detected: '{pattern}'.")

        if "eval" not in text:
            score -= 0.15
            notes.append("No explicit evaluation requirement found in delta.")

        score = max(0.0, min(1.0, score))
        passed = score >= 0.7
        report = "Passed checks." if passed and not notes else " ".join(notes)

        now = utc_now_iso()
        eval_id = self.db.add_policy_evaluation(
            proposal_id=proposal_id,
            score=score,
            passed=passed,
            report=report,
            created_at=now,
        )
        self.db.set_proposal_status(proposal_id, "evaluated")

        return {
            "evaluation_id": eval_id,
            "proposal_id": proposal_id,
            "score": score,
            "passed": passed,
            "report": report,
            "created_at": now,
        }

    def policy_promote(self, proposal_id: str) -> dict:
        proposal = self.db.get_policy_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"proposal '{proposal_id}' not found")

        latest = self.db.latest_evaluation(proposal_id)
        if latest is None or not latest["passed"]:
            raise ValueError("proposal must have a passing evaluation before promotion")

        current = self.policy_get()["content_md"]
        next_md = (
            f"{current}\n\n"
            f"## Delta {proposal_id}\n"
            f"{proposal['delta_md'].strip()}\n"
        ).strip()

        version_id = f"ver-{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        self.db.create_policy_version(
            version_id=version_id,
            content_md=next_md,
            source_proposal_id=proposal_id,
            is_active=True,
            created_at=now,
        )
        self.db.set_proposal_status(proposal_id, "promoted")

        return {
            "version_id": version_id,
            "proposal_id": proposal_id,
            "is_active": True,
            "created_at": now,
        }

    def policy_rollback(self, version_id: str) -> dict:
        ok = self.db.set_active_policy_version(version_id)
        if not ok:
            raise ValueError(f"policy version '{version_id}' not found")
        active = self.db.get_active_policy_version() or {}
        return {
            "version_id": active.get("version_id", version_id),
            "is_active": active.get("is_active", True),
            "created_at": active.get("created_at"),
        }
