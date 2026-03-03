from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{key}="{_escape_label(val)}"' for key, val in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _line(metric_name: str, value: float, labels: dict[str, str]) -> str:
    return f"{metric_name}{_format_labels(labels)} {value}"


def render_prometheus_text(snapshot: dict[str, Any]) -> str:
    namespace = str(snapshot.get("namespace", "default"))
    jobs = dict(snapshot.get("jobs", {}))
    queue = dict(snapshot.get("queue", {}))

    lines: list[str] = []
    lines.append("# HELP agent_memory_queue_jobs Number of jobs by queue state")
    lines.append("# TYPE agent_memory_queue_jobs gauge")

    queue_metrics = {
        "queued_total": "queued_total",
        "queued_ready": "queued_ready",
        "queued_delayed": "queued_delayed",
        "queued_retries": "queued_retries",
        "running_total": "running_total",
        "running_stuck": "running_stuck",
        "succeeded_total": "succeeded_total",
        "dead_total": "dead_total",
    }
    for key, state in queue_metrics.items():
        lines.append(
            _line(
                "agent_memory_queue_jobs",
                _to_float(queue.get(key, 0)),
                {"namespace": namespace, "state": state},
            )
        )

    lines.append("# HELP agent_memory_jobs_created_total Jobs created in metrics window")
    lines.append("# TYPE agent_memory_jobs_created_total counter")
    lines.append(
        _line(
            "agent_memory_jobs_created_total",
            _to_float(jobs.get("created_total", 0)),
            {"namespace": namespace},
        )
    )

    lines.append("# HELP agent_memory_jobs_completed_total Completed jobs in metrics window")
    lines.append("# TYPE agent_memory_jobs_completed_total counter")
    lines.append(
        _line(
            "agent_memory_jobs_completed_total",
            _to_float(jobs.get("completed_total", 0)),
            {"namespace": namespace},
        )
    )

    lines.append("# HELP agent_memory_jobs_terminal_total Terminal jobs by status in metrics window")
    lines.append("# TYPE agent_memory_jobs_terminal_total counter")
    lines.append(
        _line(
            "agent_memory_jobs_terminal_total",
            _to_float(jobs.get("succeeded", 0)),
            {"namespace": namespace, "status": "succeeded"},
        )
    )
    lines.append(
        _line(
            "agent_memory_jobs_terminal_total",
            _to_float(jobs.get("dead", 0)),
            {"namespace": namespace, "status": "dead"},
        )
    )

    lines.append("# HELP agent_memory_jobs_success_rate Success rate in metrics window")
    lines.append("# TYPE agent_memory_jobs_success_rate gauge")
    lines.append(
        _line(
            "agent_memory_jobs_success_rate",
            _to_float(jobs.get("success_rate", 0.0)),
            {"namespace": namespace},
        )
    )

    lines.append("# HELP agent_memory_jobs_retry_events_total Retry events in metrics window")
    lines.append("# TYPE agent_memory_jobs_retry_events_total counter")
    lines.append(
        _line(
            "agent_memory_jobs_retry_events_total",
            _to_float(jobs.get("retry_events", 0)),
            {"namespace": namespace},
        )
    )

    lines.append("# HELP agent_memory_jobs_avg_attempt_count Average attempt count for completed jobs")
    lines.append("# TYPE agent_memory_jobs_avg_attempt_count gauge")
    lines.append(
        _line(
            "agent_memory_jobs_avg_attempt_count",
            _to_float(jobs.get("avg_attempt_count", 0.0)),
            {"namespace": namespace},
        )
    )

    lines.append("# HELP agent_memory_jobs_latency_seconds Average latency in seconds")
    lines.append("# TYPE agent_memory_jobs_latency_seconds gauge")
    latency_keys = {
        "avg_queue_latency_seconds": "queue",
        "avg_run_latency_seconds": "run",
        "avg_end_to_end_latency_seconds": "end_to_end",
    }
    for key, stage in latency_keys.items():
        lines.append(
            _line(
                "agent_memory_jobs_latency_seconds",
                _to_float(jobs.get(key, 0.0)),
                {"namespace": namespace, "stage": stage},
            )
        )

    by_type = dict(jobs.get("completed_by_type", {}))
    lines.append("# HELP agent_memory_jobs_completed_by_type Completed jobs by type and terminal status")
    lines.append("# TYPE agent_memory_jobs_completed_by_type counter")
    for job_type, status_counts in sorted(by_type.items()):
        scoped = dict(status_counts)
        for status, count_value in sorted(scoped.items()):
            lines.append(
                _line(
                    "agent_memory_jobs_completed_by_type",
                    _to_float(count_value),
                    {
                        "namespace": namespace,
                        "job_type": str(job_type),
                        "status": str(status),
                    },
                )
            )

    return "\n".join(lines) + "\n"


def _iso_to_unix_nanos(value: str) -> int:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def build_otel_json(snapshot: dict[str, Any]) -> dict[str, Any]:
    namespace = str(snapshot.get("namespace", "default"))
    generated_at = str(snapshot.get("generated_at", ""))
    window_minutes = int(snapshot.get("window_minutes", 0) or 0)

    jobs = dict(snapshot.get("jobs", {}))
    queue = dict(snapshot.get("queue", {}))

    timestamp_unix_nano = _iso_to_unix_nanos(generated_at)

    def metric(name: str, value: float, attributes: dict[str, str]) -> dict[str, Any]:
        return {
            "name": name,
            "data": {
                "data_points": [
                    {
                        "as_double": float(value),
                        "attributes": attributes,
                        "time_unix_nano": timestamp_unix_nano,
                    }
                ]
            },
        }

    metrics: list[dict[str, Any]] = []

    metrics.append(metric("agent_memory.jobs.created_total", _to_float(jobs.get("created_total", 0)), {"namespace": namespace}))
    metrics.append(metric("agent_memory.jobs.completed_total", _to_float(jobs.get("completed_total", 0)), {"namespace": namespace}))
    metrics.append(metric("agent_memory.jobs.success_rate", _to_float(jobs.get("success_rate", 0)), {"namespace": namespace}))
    metrics.append(metric("agent_memory.jobs.retry_events_total", _to_float(jobs.get("retry_events", 0)), {"namespace": namespace}))

    metrics.append(metric("agent_memory.queue.queued_total", _to_float(queue.get("queued_total", 0)), {"namespace": namespace}))
    metrics.append(metric("agent_memory.queue.running_total", _to_float(queue.get("running_total", 0)), {"namespace": namespace}))
    metrics.append(metric("agent_memory.queue.running_stuck", _to_float(queue.get("running_stuck", 0)), {"namespace": namespace}))
    metrics.append(metric("agent_memory.queue.dead_total", _to_float(queue.get("dead_total", 0)), {"namespace": namespace}))

    return {
        "resource": {
            "attributes": {
                "service.name": "agent-memory-mcp",
                "agent_memory.namespace": namespace,
            }
        },
        "scope_metrics": [
            {
                "scope": {"name": "agent-memory-mcp", "version": "0.1"},
                "metrics": metrics,
            }
        ],
        "window_minutes": window_minutes,
        "generated_at": generated_at,
    }
