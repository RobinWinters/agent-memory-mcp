from __future__ import annotations

from agent_memory_mcp.models import utc_now_iso


class ServiceMemoryMixin:
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
