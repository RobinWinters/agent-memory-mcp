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
        embedder=HashEmbedder(dimensions=64),
        evaluator=PolicyEvaluator(pass_threshold=0.7),
        vector_store=LocalMemoryVectorStore(db=db),
        default_namespace="default",
        job_default_max_attempts=2,
        job_backoff_base_seconds=0.001,
        job_backoff_max_seconds=0.001,
        job_running_timeout_seconds=1.0,
    )


def seed_jobs(svc: MemoryPolicyService) -> None:
    svc.append_event("s1", "user", "hello")
    svc.append_event("s1", "assistant", "world")

    svc.jobs_submit("memory.distill", {"session_id": "s1", "max_lines": 3, "max_attempts": 2})
    svc.jobs_submit("memory.distill", {"session_id": "missing", "max_lines": 3, "max_attempts": 1})
    svc.jobs_run_pending(limit=10)


def test_prometheus_export_contains_key_metrics(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    seed_jobs(svc)

    export = svc.ops_metrics_prometheus(window_minutes=60)
    text = str(export["text"])

    assert export["format"] == "prometheus_text"
    assert "agent_memory_jobs_success_rate" in text
    assert "agent_memory_queue_jobs" in text
    assert 'namespace="default"' in text


def test_otel_export_shape(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    seed_jobs(svc)

    export = svc.ops_metrics_otel(window_minutes=60)
    assert export["format"] == "otel_json"

    payload = dict(export["payload"])
    assert "resource" in payload
    assert "scope_metrics" in payload

    scopes = list(payload["scope_metrics"])
    assert scopes
    metrics = list(scopes[0]["metrics"])
    assert metrics

    names = {item["name"] for item in metrics}
    assert "agent_memory.jobs.created_total" in names
    assert "agent_memory.queue.running_total" in names
