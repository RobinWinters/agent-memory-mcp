from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from qdrant_client import QdrantClient
from qdrant_client.http import models

from agent_memory_mcp.db import Database


@dataclass(frozen=True)
class VectorHit:
    memory_id: int
    score: float


class MemoryVectorStore(Protocol):
    backend_name: str

    def upsert(
        self,
        *,
        memory_id: int,
        namespace: str,
        session_id: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None:
        raise NotImplementedError

    def search(self, *, namespace: str, query_vector: list[float], k: int) -> list[VectorHit]:
        raise NotImplementedError


class LocalMemoryVectorStore:
    backend_name = "sqlite"

    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self,
        *,
        memory_id: int,
        namespace: str,
        session_id: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None:
        _ = memory_id, namespace, session_id, vector, metadata

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        return sum(x * y for x, y in zip(a, b, strict=True))

    def search(self, *, namespace: str, query_vector: list[float], k: int) -> list[VectorHit]:
        memories = self.db.list_memories(namespace=namespace)
        if not memories:
            return []

        scored: list[tuple[float, int]] = []
        for memory in memories:
            embedding = memory["embedding"]
            if len(embedding) != len(query_vector):
                continue
            score = self._cosine_similarity(query_vector, embedding)
            scored.append((score, int(memory["id"])))

        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[: max(k, 1)]
        return [VectorHit(memory_id=memory_id, score=score) for score, memory_id in top]


class QdrantMemoryVectorStore:
    backend_name = "qdrant"

    def __init__(
        self,
        *,
        url: str,
        collection: str,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        auto_create_collection: bool = True,
        client: QdrantClient | None = None,
    ) -> None:
        self.collection = collection
        self.auto_create_collection = auto_create_collection
        self.client = client or QdrantClient(url=url, api_key=api_key, timeout=timeout_seconds)
        self._vector_size: int | None = None

    def _ensure_collection(self, vector_size: int) -> None:
        if self._vector_size == vector_size:
            return

        exists = self.client.collection_exists(self.collection)
        if not exists:
            if not self.auto_create_collection:
                raise ValueError(f"Qdrant collection '{self.collection}' does not exist")
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
            )
        self._vector_size = vector_size

    def upsert(
        self,
        *,
        memory_id: int,
        namespace: str,
        session_id: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None:
        self._ensure_collection(len(vector))
        payload = {
            "namespace": namespace,
            "session_id": session_id,
            "metadata": metadata,
        }
        self.client.upsert(
            collection_name=self.collection,
            points=[models.PointStruct(id=memory_id, vector=vector, payload=payload)],
            wait=True,
        )

    def search(self, *, namespace: str, query_vector: list[float], k: int) -> list[VectorHit]:
        self._ensure_collection(len(query_vector))
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="namespace",
                    match=models.MatchValue(value=namespace),
                )
            ]
        )

        results = self.client.search(
            collection_name=self.collection,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=max(k, 1),
            with_payload=False,
        )

        hits: list[VectorHit] = []
        for item in results:
            if item.id is None:
                continue
            hits.append(VectorHit(memory_id=int(item.id), score=float(item.score or 0.0)))
        return hits


def build_vector_store(
    *,
    backend: str,
    db: Database,
    qdrant_url: str,
    qdrant_collection: str,
    qdrant_api_key: str | None,
    qdrant_timeout_seconds: float,
    qdrant_auto_create_collection: bool,
) -> MemoryVectorStore:
    normalized = (backend or "sqlite").strip().lower()
    if normalized == "qdrant":
        return QdrantMemoryVectorStore(
            url=qdrant_url,
            collection=qdrant_collection,
            api_key=qdrant_api_key,
            timeout_seconds=qdrant_timeout_seconds,
            auto_create_collection=qdrant_auto_create_collection,
        )
    return LocalMemoryVectorStore(db=db)
