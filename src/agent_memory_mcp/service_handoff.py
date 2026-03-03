from __future__ import annotations

import uuid
from typing import Any

from agent_memory_mcp.models import utc_now_iso


class ServiceHandoffMixin:
    @staticmethod
    def _build_handoff_prompt(
        *,
        namespace: str,
        generated_at: str,
        memories: list[dict[str, Any]],
        policy: dict[str, Any] | None,
    ) -> str:
        lines: list[str] = [
            "# Agent Handoff Context",
            f"- Namespace: {namespace}",
            f"- Generated at: {generated_at}",
            "",
            "Use this context to continue work in a new model/agent/IDE without starting from scratch.",
            "Treat this as historical guidance and re-validate before high-risk actions.",
            "",
            "## Active Policy",
        ]

        if policy and str(policy.get("content_md", "")).strip():
            lines.append(str(policy["content_md"]).strip())
        else:
            lines.append("(No active policy exported.)")

        lines.append("")
        lines.append("## Distilled Memories")
        if not memories:
            lines.append("(No memories exported.)")
            return "\n".join(lines).strip()

        for index, memory in enumerate(memories, start=1):
            session_id = str(memory.get("session_id", "")).strip() or "unknown"
            created_at = str(memory.get("created_at", "")).strip() or "unknown"
            content = str(memory.get("content", "")).strip()
            lines.extend(
                [
                    f"### Memory {index}",
                    f"- session_id: {session_id}",
                    f"- created_at: {created_at}",
                    content or "(empty)",
                    "",
                ]
            )

        return "\n".join(lines).strip()

    def memory_handoff_export(
        self,
        query: str | None = None,
        k: int = 20,
        include_policy: bool = True,
        include_events: bool = False,
        max_events_per_session: int = 20,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        ns = self._ns(namespace)
        resolved_k = self._coerce_positive_int(k, default=20)
        resolved_max_events = self._coerce_positive_int(max_events_per_session, default=20)
        now = utc_now_iso()
        clean_query = (query or "").strip()

        memories: list[dict[str, Any]] = []
        if clean_query:
            hits = self.memory_search(query=clean_query, k=resolved_k, namespace=ns)
            for hit in hits:
                memories.append(
                    {
                        "memory_id": int(hit["memory_id"]),
                        "session_id": str(hit["session_id"]),
                        "content": str(hit["content"]),
                        "created_at": None,
                        "metadata": dict(hit.get("metadata", {})),
                        "score": float(hit["score"]),
                    }
                )
        else:
            all_memories = self.db.list_memories(namespace=ns)
            all_memories.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
            for item in all_memories[:resolved_k]:
                memories.append(
                    {
                        "memory_id": int(item["id"]),
                        "session_id": str(item["session_id"]),
                        "content": str(item["content"]),
                        "created_at": str(item["created_at"]),
                        "metadata": dict(item.get("metadata", {})),
                    }
                )

        policy_payload: dict[str, Any] | None = None
        if include_policy:
            active = self.db.get_active_policy_version(namespace=ns)
            if active is not None:
                policy_payload = {
                    "version_id": str(active.get("version_id", "")),
                    "content_md": str(active.get("content_md", "")),
                    "created_at": str(active.get("created_at", "")),
                    "content_sha256": str(active.get("content_sha256", "")),
                    "signing_method": str(active.get("signing_method", "")),
                    "source_proposal_id": active.get("source_proposal_id"),
                }

        sessions: list[dict[str, Any]] = []
        event_count = 0
        if include_events and memories:
            seen_session_ids: set[str] = set()
            for memory in memories:
                session_id = str(memory.get("session_id", "")).strip()
                if not session_id or session_id in seen_session_ids:
                    continue
                seen_session_ids.add(session_id)
                raw_events = self.db.list_events(namespace=ns, session_id=session_id)
                trimmed = raw_events[-resolved_max_events:] if resolved_max_events > 0 else raw_events
                events: list[dict[str, Any]] = []
                for event in trimmed:
                    events.append(
                        {
                            "id": int(event["id"]),
                            "role": str(event["role"]),
                            "content": str(event["content"]),
                            "created_at": str(event["created_at"]),
                            "metadata": dict(event.get("metadata", {})),
                        }
                    )
                event_count += len(events)
                sessions.append(
                    {
                        "session_id": session_id,
                        "event_count": len(events),
                        "events": events,
                    }
                )

        prompt_md = self._build_handoff_prompt(
            namespace=ns,
            generated_at=now,
            memories=memories,
            policy=policy_payload,
        )

        return {
            "schema": "agent-memory-handoff.v1",
            "generated_at": now,
            "namespace": ns,
            "query": clean_query or None,
            "k": resolved_k,
            "policy": policy_payload,
            "memories": memories,
            "sessions": sessions,
            "prompt_md": prompt_md,
            "stats": {
                "memory_count": len(memories),
                "session_count": len(sessions),
                "event_count": event_count,
            },
        }

    def memory_handoff_import(
        self,
        handoff: dict[str, Any],
        session_id_prefix: str = "imported",
        import_policy: bool = False,
        import_events: bool = False,
        max_events_per_session: int = 200,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(handoff, dict):
            raise ValueError("handoff must be a JSON object")

        ns = self._ns(namespace)
        clean_prefix = session_id_prefix.strip() or "imported"
        resolved_max_events = self._coerce_positive_int(max_events_per_session, default=200)
        now = utc_now_iso()

        schema = str(handoff.get("schema", "")).strip()
        source_namespace = str(handoff.get("namespace", "")).strip() or None
        memories_raw = handoff.get("memories", [])
        sessions_raw = handoff.get("sessions", [])
        policy_raw = handoff.get("policy")

        if not isinstance(memories_raw, list):
            memories_raw = []
        if not isinstance(sessions_raw, list):
            sessions_raw = []

        imported_memory_ids: list[int] = []
        skipped_memories = 0
        for index, raw in enumerate(memories_raw, start=1):
            if not isinstance(raw, dict):
                skipped_memories += 1
                continue

            content = str(raw.get("content", "")).strip()
            if not content:
                skipped_memories += 1
                continue

            session_id = str(raw.get("session_id", "")).strip() or f"{clean_prefix}-{index}"
            created_at = str(raw.get("created_at", "")).strip() or now
            metadata_raw = raw.get("metadata", {})
            metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
            metadata["kind"] = "handoff_import"
            metadata["source_schema"] = schema or "unknown"
            if source_namespace:
                metadata.setdefault("source_namespace", source_namespace)
            source_memory_id = raw.get("memory_id")
            if source_memory_id is not None:
                metadata.setdefault("source_memory_id", source_memory_id)

            embedding = self.embedder.embed(content)
            memory_id = self.db.insert_memory(
                namespace=ns,
                session_id=session_id,
                content=content,
                embedding=embedding,
                created_at=created_at,
                metadata=metadata,
            )
            self.vector_store.upsert(
                memory_id=memory_id,
                namespace=ns,
                session_id=session_id,
                vector=embedding,
                metadata=metadata,
            )
            imported_memory_ids.append(memory_id)

        imported_events = 0
        skipped_events = 0
        if import_events:
            for session_index, raw_session in enumerate(sessions_raw, start=1):
                if not isinstance(raw_session, dict):
                    continue
                session_id = str(raw_session.get("session_id", "")).strip() or f"{clean_prefix}-events-{session_index}"
                self.db.upsert_session(
                    namespace=ns,
                    session_id=session_id,
                    started_at=now,
                    metadata={"source": "handoff_import"},
                )

                raw_events = raw_session.get("events", [])
                if not isinstance(raw_events, list):
                    continue
                trimmed = raw_events[-resolved_max_events:] if resolved_max_events > 0 else raw_events
                for raw_event in trimmed:
                    if not isinstance(raw_event, dict):
                        skipped_events += 1
                        continue
                    role = str(raw_event.get("role", "user")).strip() or "user"
                    content = str(raw_event.get("content", "")).strip()
                    if not content:
                        skipped_events += 1
                        continue
                    created_at = str(raw_event.get("created_at", "")).strip() or now
                    metadata_raw = raw_event.get("metadata", {})
                    metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
                    metadata.setdefault("imported_from_handoff", True)
                    self.db.append_event(
                        namespace=ns,
                        session_id=session_id,
                        role=role,
                        content=content,
                        created_at=created_at,
                        metadata=metadata,
                    )
                    imported_events += 1

        imported_policy_version_id: str | None = None
        if import_policy and isinstance(policy_raw, dict):
            content_md = str(policy_raw.get("content_md", "")).strip()
            if content_md:
                version_id = f"import-{uuid.uuid4().hex[:12]}"
                source_proposal_id = str(policy_raw.get("source_proposal_id") or f"handoff:{source_namespace or 'unknown'}")
                self._create_policy_version_with_integrity(
                    namespace=ns,
                    version_id=version_id,
                    content_md=content_md,
                    source_proposal_id=source_proposal_id,
                    is_active=True,
                    created_at=now,
                    event_type="policy.version.imported",
                )
                imported_policy_version_id = version_id

        return {
            "namespace": ns,
            "source_namespace": source_namespace,
            "schema": schema or None,
            "imported_memories": len(imported_memory_ids),
            "imported_memory_ids": imported_memory_ids,
            "skipped_memories": skipped_memories,
            "imported_events": imported_events,
            "skipped_events": skipped_events,
            "imported_policy_version_id": imported_policy_version_id,
        }
