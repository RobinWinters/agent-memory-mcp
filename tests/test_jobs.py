from __future__ import annotations

from pathlib import Path

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import HashEmbedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.vector_store import LocalMemoryVectorStore


def make_service(tmp_path: Path) -> MemoryPolicyService:
    db = Database(str(tmp_path / "test.db"))
    return MemoryPolicyService(
        db=db,
        embedder=HashEmbedder(dimensions=128),
        evaluator=PolicyEvaluator(pass_threshold=0.7),
        vector_store=LocalMemoryVectorStore(db=db),
        default_namespace="default",
    )


def test_async_distill_job_flow(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    svc.append_event("s1", "user", "Need async distill")
    svc.append_event("s1", "assistant", "Queue it and run later")

    queued = svc.distill_session("s1", async_mode=True)
    assert queued["status"] == "queued"

    status = svc.jobs_status(queued["job_id"])
    assert status["status"] == "queued"

    run = svc.jobs_run_pending(limit=1)
    assert run["processed"] == 1
    assert run["succeeded"] == 1

    result = svc.jobs_result(queued["job_id"])
    assert result["status"] == "succeeded"
    assert result["result"]["memory_id"] > 0


def test_async_policy_evaluate_flow(tmp_path: Path) -> None:
    svc = make_service(tmp_path)

    proposal = svc.policy_propose(
        delta_md="""
        ## Async Eval
        - Require eval before promotion.
        - Require rollback support.
        - Include regression threshold checks.
        """,
        evidence_refs=["memory:1", "session:s1"],
    )

    queued = svc.policy_evaluate(proposal["proposal_id"], async_mode=True)
    assert queued["status"] == "queued"

    run = svc.jobs_run_pending(limit=1)
    assert run["succeeded"] == 1

    result = svc.jobs_result(queued["job_id"])
    assert result["status"] == "succeeded"
    assert result["result"]["proposal_id"] == proposal["proposal_id"]


def test_job_failure_capture(tmp_path: Path) -> None:
    svc = make_service(tmp_path)

    queued = svc.jobs_submit(
        job_type="memory.distill",
        payload={"session_id": "missing-session", "max_lines": 6},
    )

    run = svc.jobs_run_pending(limit=1)
    assert run["failed"] == 1

    result = svc.jobs_result(queued["job_id"])
    assert result["status"] == "failed"
    assert "has no events" in (result["error"] or "")
