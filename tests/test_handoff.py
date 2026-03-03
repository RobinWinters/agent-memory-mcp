from __future__ import annotations

from pathlib import Path

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import HashEmbedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.vector_store import LocalMemoryVectorStore


def make_service(db_path: Path) -> MemoryPolicyService:
    db = Database(str(db_path))
    return MemoryPolicyService(
        db=db,
        embedder=HashEmbedder(dimensions=64),
        evaluator=PolicyEvaluator(pass_threshold=0.7),
        vector_store=LocalMemoryVectorStore(db=db),
        default_namespace="default",
        job_default_max_attempts=2,
        job_backoff_base_seconds=0.001,
        job_backoff_max_seconds=0.001,
        job_running_timeout_seconds=1.0,
    )


def seed_memory_and_policy(svc: MemoryPolicyService, namespace: str) -> None:
    svc.append_event("s1", "user", "Need stronger eval gates before promote.", namespace=namespace)
    svc.append_event("s1", "assistant", "Always keep rollback path and audit logs.", namespace=namespace)
    svc.distill_session("s1", namespace=namespace)

    proposal = svc.policy_propose(
        delta_md="""
        ## Safety Gates
        - Require eval before promotion.
        - Require rollback support.
        - Keep regression checks enabled.
        """,
        evidence_refs=["session:s1", "memory:1"],
        namespace=namespace,
    )
    eval_result = svc.policy_evaluate(proposal_id=str(proposal["proposal_id"]), namespace=namespace)
    assert eval_result["passed"] is True
    svc.policy_promote(proposal_id=str(proposal["proposal_id"]), namespace=namespace)


def test_handoff_export_payload(tmp_path: Path) -> None:
    namespace = "source-a"
    svc = make_service(tmp_path / "source.db")
    seed_memory_and_policy(svc, namespace=namespace)

    handoff = svc.memory_handoff_export(
        query=None,
        k=5,
        include_policy=True,
        include_events=True,
        max_events_per_session=10,
        namespace=namespace,
    )

    assert handoff["schema"] == "agent-memory-handoff.v1"
    assert handoff["namespace"] == namespace
    assert handoff["policy"] is not None
    assert "content_md" in handoff["policy"]
    assert handoff["stats"]["memory_count"] >= 1
    assert handoff["stats"]["session_count"] >= 1
    assert handoff["stats"]["event_count"] >= 2
    assert handoff["memories"]
    assert handoff["sessions"]
    assert "Agent Handoff Context" in handoff["prompt_md"]
    assert "Active Policy" in handoff["prompt_md"]
    assert "Distilled Memories" in handoff["prompt_md"]


def test_handoff_import_into_new_service(tmp_path: Path) -> None:
    source_ns = "source-a"
    target_ns = "target-b"
    source = make_service(tmp_path / "source.db")
    target = make_service(tmp_path / "target.db")

    seed_memory_and_policy(source, namespace=source_ns)
    handoff = source.memory_handoff_export(
        k=10,
        include_policy=True,
        include_events=True,
        namespace=source_ns,
    )

    imported = target.memory_handoff_import(
        handoff=handoff,
        session_id_prefix="xfer",
        import_policy=True,
        import_events=True,
        namespace=target_ns,
    )

    assert imported["namespace"] == target_ns
    assert imported["source_namespace"] == source_ns
    assert imported["imported_memories"] >= 1
    assert imported["imported_events"] >= 2
    assert imported["imported_policy_version_id"] is not None

    results = target.memory_search(query="eval rollback audit", k=3, namespace=target_ns)
    assert results
    assert all(item["namespace"] == target_ns for item in results)

    active = target.policy_get(namespace=target_ns)
    assert "Require eval before promotion" in active["content_md"]

    imported_events = target.db.list_events(namespace=target_ns, session_id="s1")
    assert len(imported_events) >= 2
