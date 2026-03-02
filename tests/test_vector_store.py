from __future__ import annotations

from dataclasses import dataclass

from qdrant_client.http import models

from agent_memory_mcp.db import Database
from agent_memory_mcp.vector_store import LocalMemoryVectorStore, QdrantMemoryVectorStore


@dataclass
class FakeScoredPoint:
    id: int | None
    score: float | None


class FakeQdrantClient:
    def __init__(self) -> None:
        self.exists = False
        self.created: tuple[str, int] | None = None
        self.upserted_points: list[models.PointStruct] = []

    def collection_exists(self, collection: str) -> bool:
        _ = collection
        return self.exists

    def create_collection(self, collection_name: str, vectors_config: models.VectorParams) -> None:
        self.exists = True
        self.created = (collection_name, vectors_config.size)

    def upsert(self, collection_name: str, points: list[models.PointStruct], wait: bool) -> None:
        _ = collection_name, wait
        self.upserted_points.extend(points)

    def search(
        self,
        collection_name: str,
        query_vector: list[float],
        query_filter: models.Filter,
        limit: int,
        with_payload: bool,
    ) -> list[FakeScoredPoint]:
        _ = collection_name, query_vector, limit, with_payload
        assert query_filter.must is not None
        return [FakeScoredPoint(id=33, score=0.91), FakeScoredPoint(id=12, score=0.42)]


def test_local_store_search(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    store = LocalMemoryVectorStore(db=db)

    db.insert_memory(
        namespace="n1",
        session_id="s1",
        content="hello",
        embedding=[1.0, 0.0],
        created_at="2026-03-02T00:00:00Z",
        metadata={},
    )
    db.insert_memory(
        namespace="n1",
        session_id="s2",
        content="world",
        embedding=[0.0, 1.0],
        created_at="2026-03-02T00:00:00Z",
        metadata={},
    )

    hits = store.search(namespace="n1", query_vector=[1.0, 0.0], k=2)
    assert len(hits) == 2
    assert hits[0].score >= hits[1].score


def test_qdrant_store_create_upsert_search() -> None:
    fake = FakeQdrantClient()
    store = QdrantMemoryVectorStore(
        url="http://localhost:6333",
        collection="memories",
        auto_create_collection=True,
        client=fake,
    )

    store.upsert(
        memory_id=77,
        namespace="tenant-a",
        session_id="s1",
        vector=[0.1, 0.2, 0.3],
        metadata={"kind": "session_distill"},
    )

    assert fake.created == ("memories", 3)
    assert len(fake.upserted_points) == 1
    point = fake.upserted_points[0]
    assert point.id == 77

    hits = store.search(namespace="tenant-a", query_vector=[0.1, 0.2, 0.3], k=5)
    assert [hit.memory_id for hit in hits] == [33, 12]
