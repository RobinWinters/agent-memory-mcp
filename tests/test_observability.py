from __future__ import annotations

from pathlib import Path

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import HashEmbedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.models import utc_now_iso
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
        job_default_max_attempts=3,
        job_backoff_base_seconds=0.001,
        job_backoff_max_seconds=0.001,
        job_running_timeout_seconds=1.0,
    )


def test_ops_health_detects_stuck_running(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    svc.append_event("s1", "user", "x")
    svc.append_event("s1", "assistant", "y")

    queued = svc.jobs_submit(
        job_type="memory.distill",
        payload={"session_id": "s1", "max_lines": 3},
    )
    claimed = svc.db.claim_next_queued_job(namespace="default", now=utc_now_iso())
    assert claimed is not None

    svc.db.conn.execute(
        "UPDATE jobs SET started_at=?, updated_at=? WHERE id=?",
        ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", queued["job_id"]),
    )
    svc.db.conn.commit()

    health = svc.ops_health()
    assert health["queue"]["running_total"] == 1
    assert health["queue"]["running_stuck"] == 1


def test_ops_metrics_reports_success_and_dead(tmp_path: Path) -> None:
    svc = make_service(tmp_path)

    svc.append_event("s-ok", "user", "hello")
    svc.append_event("s-ok", "assistant", "world")

    ok_job = svc.jobs_submit(
        job_type="memory.distill",
        payload={"session_id": "s-ok", "max_lines": 3, "max_attempts": 2},
    )
    bad_job = svc.jobs_submit(
        job_type="memory.distill",
        payload={"session_id": "missing", "max_lines": 3, "max_attempts": 1},
    )

    run = svc.jobs_run_pending(limit=5)
    assert run["processed"] >= 2

    ok_result = svc.jobs_result(ok_job["job_id"])
    bad_result = svc.jobs_result(bad_job["job_id"])

    assert ok_result["status"] == "succeeded"
    assert bad_result["status"] == "dead"

    metrics = svc.ops_metrics(window_minutes=60)
    jobs = metrics["jobs"]

    assert jobs["completed_total"] >= 2
    assert jobs["succeeded"] >= 1
    assert jobs["dead"] >= 1
    assert 0.0 <= jobs["success_rate"] <= 1.0
    assert jobs["avg_end_to_end_latency_seconds"] >= 0.0
    assert "memory.distill" in jobs["completed_by_type"]
