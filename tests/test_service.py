from __future__ import annotations

from pathlib import Path

from agent_memory_mcp.db import Database
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.vector_index import SimpleVectorIndex


def make_service(tmp_path: Path) -> MemoryPolicyService:
    db_path = tmp_path / "test.db"
    db = Database(str(db_path))
    return MemoryPolicyService(db=db, index=SimpleVectorIndex(dimensions=128))


def test_memory_pipeline(tmp_path: Path) -> None:
    svc = make_service(tmp_path)

    svc.append_event("s1", "user", "Need better eval gates")
    svc.append_event("s1", "assistant", "Add safety checks and rollback")

    distilled = svc.distill_session("s1")
    assert distilled["memory_id"] > 0

    results = svc.memory_search("rollback eval safety", k=3)
    assert results
    assert results[0]["session_id"] == "s1"


def test_policy_pipeline(tmp_path: Path) -> None:
    svc = make_service(tmp_path)

    proposal = svc.policy_propose(
        delta_md="""
        Require eval before promotion.
        Add automated rollback policy checks.
        Include eval score threshold >= 0.7.
        """,
        evidence_refs=["session:s1", "memory:1"],
    )

    eval_result = svc.policy_evaluate(proposal["proposal_id"])
    assert eval_result["passed"] is True

    promoted = svc.policy_promote(proposal["proposal_id"])
    assert promoted["is_active"] is True

    active = svc.policy_get()
    assert proposal["proposal_id"] in active["content_md"]

    rollback = svc.policy_rollback(active["version_id"])
    assert rollback["version_id"] == active["version_id"]
