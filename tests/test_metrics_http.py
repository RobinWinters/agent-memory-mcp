from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import HashEmbedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.metrics_http import MetricsHTTPBridge
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


def parse_sse_events(body: bytes) -> list[dict[str, object]]:
    text = body.decode("utf-8")
    chunks = [chunk for chunk in text.split("\n\n") if chunk.strip()]
    events: list[dict[str, object]] = []
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
        payload = json.loads("\n".join(data_lines)) if data_lines else None
        events.append({"id": event_id, "event": event_name, "data": payload})
    return events


def test_metrics_http_endpoints(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    seed_jobs(svc)

    bridge = MetricsHTTPBridge(
        host="127.0.0.1",
        port=0,
        default_namespace="default",
        service_factory=lambda: make_service(tmp_path),
        default_window_minutes=60,
        token=None,
    )
    with running_bridge(bridge) as base_url:
        status_metrics, headers_metrics, body_metrics = http_get(f"{base_url}/metrics")
        assert status_metrics == 200
        assert "text/plain" in headers_metrics.get("Content-Type", "")
        text = body_metrics.decode("utf-8")
        assert "agent_memory_jobs_success_rate" in text
        assert 'namespace="default"' in text

        status_otel, headers_otel, body_otel = http_get(f"{base_url}/metrics/otel")
        assert status_otel == 200
        assert "application/json" in headers_otel.get("Content-Type", "")
        payload = json.loads(body_otel.decode("utf-8"))
        assert "resource" in payload
        assert "scope_metrics" in payload

        status_health, headers_health, body_health = http_get(f"{base_url}/health")
        assert status_health == 200
        assert "application/json" in headers_health.get("Content-Type", "")
        health = json.loads(body_health.decode("utf-8"))
        assert "queue" in health
        assert "running_total" in health["queue"]

        status_missing, _, body_missing = http_get(f"{base_url}/missing")
        assert status_missing == 404
        missing = json.loads(body_missing.decode("utf-8"))
        assert missing["error"] == "not_found"


def test_metrics_http_token_auth(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    seed_jobs(svc)

    bridge = MetricsHTTPBridge(
        host="127.0.0.1",
        port=0,
        default_namespace="default",
        service_factory=lambda: make_service(tmp_path),
        default_window_minutes=60,
        token="top-secret",
    )
    with running_bridge(bridge) as base_url:
        status_no_token, _, _ = http_get(f"{base_url}/metrics")
        assert status_no_token == 401

        status_with_header, _, _ = http_get(
            f"{base_url}/metrics",
            headers={"Authorization": "Bearer top-secret"},
        )
        assert status_with_header == 200

        status_with_query, _, _ = http_get(f"{base_url}/metrics?token=top-secret")
        assert status_with_query == 200

        status_stream_no_token, _, _ = http_get(f"{base_url}/stream/jobs?max_events=1")
        assert status_stream_no_token == 401

        status_stream_with_header, _, _ = http_get(
            f"{base_url}/stream/jobs?max_events=1",
            headers={"Authorization": "Bearer top-secret"},
        )
        assert status_stream_with_header == 200

        status_stream_with_query, _, _ = http_get(f"{base_url}/stream/jobs?max_events=1&token=top-secret")
        assert status_stream_with_query == 200


def test_metrics_http_query_overrides(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    seed_jobs(svc)

    bridge = MetricsHTTPBridge(
        host="127.0.0.1",
        port=0,
        default_namespace="default",
        service_factory=lambda: make_service(tmp_path),
        default_window_minutes=60,
        token=None,
    )
    with running_bridge(bridge) as base_url:
        status, _, body = http_get(f"{base_url}/metrics?namespace=tenant-z&window_minutes=15")
        assert status == 200
        text = body.decode("utf-8")
        assert 'namespace="tenant-z"' in text


def test_metrics_http_job_stream_sse(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    seed_jobs(svc)

    bridge = MetricsHTTPBridge(
        host="127.0.0.1",
        port=0,
        default_namespace="default",
        service_factory=lambda: make_service(tmp_path),
        default_window_minutes=60,
        token=None,
    )
    with running_bridge(bridge) as base_url:
        status, headers, body = http_get(
            (
                f"{base_url}/stream/jobs"
                "?namespace=tenant-z&window_minutes=5&interval_seconds=0.01&include_metrics=true&max_events=2"
            )
        )
        assert status == 200
        assert "text/event-stream" in headers.get("Content-Type", "")

        events = parse_sse_events(body)
        assert len(events) == 2
        assert events[0]["event"] == "jobs.snapshot"
        assert events[0]["id"] == "1"
        assert events[1]["id"] == "2"

        first_payload = events[0]["data"]
        assert isinstance(first_payload, dict)
        assert first_payload["namespace"] == "tenant-z"
        assert first_payload["window_minutes"] == 5
        assert first_payload["event_index"] == 1
        assert "health" in first_payload
        assert "queue" in first_payload["health"]
        assert "metrics" in first_payload
        assert first_payload["metrics"]["window_minutes"] == 5
