from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mcp.server.fastmcp import FastMCP

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import HashEmbedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.metrics_http import MetricsHTTPBridge
from agent_memory_mcp.server_tools_jobs import register_jobs_tools
from agent_memory_mcp.server_tools_memory import register_memory_tools
from agent_memory_mcp.server_tools_ops import register_ops_tools
from agent_memory_mcp.server_tools_policy import register_policy_tools
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.vector_store import LocalMemoryVectorStore
from agent_memory_mcp.worker import WorkerLoop


def make_service(db_path: Path) -> MemoryPolicyService:
    db = Database(str(db_path))
    return MemoryPolicyService(
        db=db,
        embedder=HashEmbedder(dimensions=64),
        evaluator=PolicyEvaluator(pass_threshold=0.7),
        vector_store=LocalMemoryVectorStore(db=db),
        default_namespace="default",
        job_default_max_attempts=3,
        job_backoff_base_seconds=0.001,
        job_backoff_max_seconds=0.001,
        job_running_timeout_seconds=1.0,
    )


def make_mcp(service: MemoryPolicyService) -> FastMCP:
    mcp = FastMCP("agent-memory-integration-test")

    def authorize(namespace: str | None, _scope: str, _api_key: str | None) -> str:
        return (namespace or "default").strip() or "default"

    def get_service() -> MemoryPolicyService:
        return service

    def get_keyring() -> None:
        return None

    def require_keyring() -> Any:
        raise ValueError("keyring is not configured")

    def apply_runtime_security(_force: bool) -> dict[str, Any]:
        return {"reloaded": False, "source": "test"}

    register_memory_tools(mcp, authorize=authorize, get_service=get_service)
    register_policy_tools(mcp, authorize=authorize, get_service=get_service)
    register_jobs_tools(mcp, authorize=authorize, get_service=get_service)
    register_ops_tools(
        mcp,
        authorize=authorize,
        get_service=get_service,
        get_keyring=get_keyring,
        require_keyring=require_keyring,
        apply_runtime_security=apply_runtime_security,
    )
    return mcp


@contextmanager
def running_bridge(bridge: MetricsHTTPBridge) -> Iterator[str]:
    server = bridge.build_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    try:
        yield base_url
    finally:
        server.shutdown()
        thread.join(timeout=2.0)
        server.server_close()


def http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    request = Request(url=url, method="GET", headers=headers or {})
    try:
        with urlopen(request, timeout=5.0) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()), exc.read()


def parse_sse_events(body: bytes) -> list[dict[str, Any]]:
    text = body.decode("utf-8")
    chunks = [chunk for chunk in text.split("\n\n") if chunk.strip()]
    events: list[dict[str, Any]] = []
    for chunk in chunks:
        event_name = "message"
        event_id = ""
        data_lines: list[str] = []
        for line in chunk.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("id:"):
                event_id = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
        payload = json.loads("\n".join(data_lines)) if data_lines else {}
        events.append({"id": event_id, "event": event_name, "data": payload})
    return events


def mcp_call_json(mcp: FastMCP, name: str, arguments: dict[str, Any]) -> Any:
    async def _call() -> Any:
        result = await mcp.call_tool(name, arguments)
        if isinstance(result, tuple) and len(result) == 2:
            _, structured = result
            if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
                return structured["result"]
            return structured
        if isinstance(result, list) and result:
            text_payload = getattr(result[0], "text", "")
            return json.loads(text_payload)
        raise AssertionError(f"unexpected tool result shape for {name}: {type(result)!r}")

    return asyncio.run(_call())


def test_golden_path_integration(tmp_path: Path) -> None:
    namespace = "tenant-a"
    session_id = "sess-golden"
    db_path = tmp_path / "test.db"
    service = make_service(db_path)
    mcp = make_mcp(service)
    worker = WorkerLoop(
        service=service,
        namespaces=(namespace,),
        batch_size=10,
        poll_seconds=0.01,
        stop_event=threading.Event(),
    )

    mcp_call_json(
        mcp,
        "memory.append",
        {
            "session_id": session_id,
            "role": "user",
            "content": "Need better policy guardrails and rollback discipline.",
            "namespace": namespace,
        },
    )
    mcp_call_json(
        mcp,
        "memory.append",
        {
            "session_id": session_id,
            "role": "assistant",
            "content": "Add async evaluation gates, retries, and observability.",
            "namespace": namespace,
        },
    )

    queued_distill = mcp_call_json(
        mcp,
        "memory.distill",
        {
            "session_id": session_id,
            "max_lines": 5,
            "async_mode": True,
            "namespace": namespace,
        },
    )
    assert queued_distill["status"] == "queued"
    distill_job_id = int(queued_distill["job_id"])

    distill_cycle = worker.run_cycle()
    assert distill_cycle["processed"] == 1
    assert distill_cycle["succeeded"] == 1

    distill_status = mcp_call_json(
        mcp,
        "jobs.status",
        {"job_id": distill_job_id, "namespace": namespace},
    )
    assert distill_status["status"] == "succeeded"

    distill_result = mcp_call_json(
        mcp,
        "jobs.result",
        {"job_id": distill_job_id, "namespace": namespace},
    )
    assert distill_result["status"] == "succeeded"
    assert int(distill_result["result"]["memory_id"]) > 0

    search_results = mcp_call_json(
        mcp,
        "memory.search",
        {"query": "rollback and evaluation gates", "k": 3, "namespace": namespace},
    )
    assert isinstance(search_results, list)
    assert search_results
    assert all(item["namespace"] == namespace for item in search_results)

    proposal = mcp_call_json(
        mcp,
        "policy.propose",
        {
            "delta_md": (
                "## Safe Promotion Rules\n"
                "- Require eval before promotion.\n"
                "- Require rollback path.\n"
                "- Keep regression threshold checks."
            ),
            "evidence_refs": [f"session:{session_id}", "memory:1"],
            "namespace": namespace,
        },
    )
    proposal_id = str(proposal["proposal_id"])

    queued_eval = mcp_call_json(
        mcp,
        "policy.evaluate",
        {"proposal_id": proposal_id, "async_mode": True, "namespace": namespace},
    )
    assert queued_eval["status"] == "queued"
    eval_job_id = int(queued_eval["job_id"])

    eval_cycle = worker.run_cycle()
    assert eval_cycle["processed"] == 1
    assert eval_cycle["succeeded"] == 1

    eval_result = mcp_call_json(
        mcp,
        "jobs.result",
        {"job_id": eval_job_id, "namespace": namespace},
    )
    assert eval_result["status"] == "succeeded"
    assert eval_result["result"]["passed"] is True

    promoted = mcp_call_json(
        mcp,
        "policy.promote",
        {"proposal_id": proposal_id, "namespace": namespace},
    )
    assert promoted["is_active"] is True

    active_policy = mcp_call_json(
        mcp,
        "policy.get",
        {"namespace": namespace},
    )
    assert proposal_id in active_policy["content_md"]

    ops_health = mcp_call_json(mcp, "ops.health", {"namespace": namespace})
    assert ops_health["namespace"] == namespace
    assert "queue" in ops_health
    assert "running_total" in ops_health["queue"]

    ops_metrics = mcp_call_json(
        mcp,
        "ops.metrics",
        {"namespace": namespace, "window_minutes": 30},
    )
    assert ops_metrics["namespace"] == namespace
    assert int(ops_metrics["window_minutes"]) == 30
    assert "jobs" in ops_metrics

    bridge = MetricsHTTPBridge(
        host="127.0.0.1",
        port=0,
        default_namespace=namespace,
        service_factory=lambda: make_service(db_path),
        default_window_minutes=30,
        default_stream_interval_seconds=0.01,
        default_stream_include_metrics=True,
        token="integration-token",
    )
    with running_bridge(bridge) as base_url:
        status_no_auth, _, _ = http_get(f"{base_url}/health")
        assert status_no_auth == 401

        auth_headers = {"Authorization": "Bearer integration-token"}

        status_health, health_headers, health_body = http_get(
            f"{base_url}/health?namespace={namespace}",
            headers=auth_headers,
        )
        assert status_health == 200
        assert "application/json" in health_headers.get("Content-Type", "")
        http_health = json.loads(health_body.decode("utf-8"))
        assert http_health["namespace"] == namespace

        status_metrics, metrics_headers, metrics_body = http_get(
            f"{base_url}/metrics?namespace={namespace}&window_minutes=30",
            headers=auth_headers,
        )
        assert status_metrics == 200
        assert "text/plain" in metrics_headers.get("Content-Type", "")
        assert f'namespace="{namespace}"' in metrics_body.decode("utf-8")

        status_stream, stream_headers, stream_body = http_get(
            (
                f"{base_url}/stream/jobs"
                f"?namespace={namespace}&window_minutes=30"
                "&interval_seconds=0.01&include_metrics=true&max_events=2"
            ),
            headers=auth_headers,
        )
        assert status_stream == 200
        assert "text/event-stream" in stream_headers.get("Content-Type", "")
        stream_events = parse_sse_events(stream_body)
        assert len(stream_events) == 2
        assert stream_events[0]["event"] == "jobs.snapshot"
        assert stream_events[0]["id"] == "1"
        payload = stream_events[0]["data"]
        assert payload["namespace"] == namespace
        assert payload["window_minutes"] == 30
        assert "health" in payload
        assert "metrics" in payload
