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

    def memory_record_outcome(
        self,
        session_id: str,
        outcome_type: str,
        summary: str,
        memory_id: int | None = None,
        score: float | None = None,
        metadata: dict | None = None,
        namespace: str | None = None,
    ) -> dict:
        ns = self._ns(namespace)
        now = utc_now_iso()

        clean_session_id = session_id.strip()
        if not clean_session_id:
            raise ValueError("session_id is required")

        clean_outcome_type = outcome_type.strip()
        if not clean_outcome_type:
            raise ValueError("outcome_type is required")

        clean_summary = summary.strip()
        if not clean_summary:
            raise ValueError("summary is required")

        resolved_memory_id: int | None = None
        if memory_id is not None:
            resolved_memory_id = int(memory_id)
            if resolved_memory_id <= 0:
                raise ValueError("memory_id must be a positive integer")
            linked = self.db.get_memories_by_ids(namespace=ns, memory_ids=[resolved_memory_id])
            if not linked:
                raise ValueError(f"memory_id {resolved_memory_id} was not found in namespace '{ns}'")
            if str(linked[0].get("session_id", "")) != clean_session_id:
                raise ValueError(
                    f"memory_id {resolved_memory_id} belongs to session '{linked[0]['session_id']}', "
                    f"not '{clean_session_id}'"
                )

        resolved_score: float | None = None
        if score is not None:
            try:
                resolved_score = float(score)
            except (TypeError, ValueError) as exc:
                raise ValueError("score must be a number") from exc

        clean_metadata = metadata or {}
        self.db.upsert_session(
            namespace=ns,
            session_id=clean_session_id,
            started_at=now,
            metadata={"source": "mcp"},
        )
        outcome_id = self.db.insert_memory_outcome(
            namespace=ns,
            session_id=clean_session_id,
            memory_id=resolved_memory_id,
            outcome_type=clean_outcome_type,
            summary=clean_summary,
            score=resolved_score,
            created_at=now,
            metadata=clean_metadata,
        )
        return {
            "outcome_id": outcome_id,
            "namespace": ns,
            "session_id": clean_session_id,
            "memory_id": resolved_memory_id,
            "outcome_type": clean_outcome_type,
            "summary": clean_summary,
            "score": resolved_score,
            "metadata": clean_metadata,
            "created_at": now,
        }

    def memory_list_outcomes(
        self,
        session_id: str | None = None,
        memory_id: int | None = None,
        limit: int = 20,
        namespace: str | None = None,
    ) -> list[dict]:
        ns = self._ns(namespace)
        clean_session_id = (session_id or "").strip() or None
        resolved_memory_id: int | None = None
        if memory_id is not None:
            resolved_memory_id = int(memory_id)
            if resolved_memory_id <= 0:
                raise ValueError("memory_id must be a positive integer")
        resolved_limit = self._coerce_positive_int(limit, default=20)
        return self.db.list_memory_outcomes(
            namespace=ns,
            session_id=clean_session_id,
            memory_id=resolved_memory_id,
            limit=resolved_limit,
        )
