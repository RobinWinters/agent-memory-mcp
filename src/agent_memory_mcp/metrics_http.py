from __future__ import annotations

import json
import signal
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from agent_memory_mcp.runtime_bootstrap import build_service_from_settings, load_settings_from_env
from agent_memory_mcp.service import MemoryPolicyService


def _parse_positive_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _parse_positive_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass
class MetricsHTTPBridge:
    host: str
    port: int
    default_namespace: str
    service: MemoryPolicyService | None = None
    service_factory: Callable[[], MemoryPolicyService] | None = None
    default_window_minutes: int = 60
    default_stream_interval_seconds: float = 2.0
    default_stream_include_metrics: bool = False
    token: str | None = None
    _thread_local: threading.local = field(default_factory=threading.local, init=False, repr=False)

    def _get_service(self) -> MemoryPolicyService:
        cached = getattr(self._thread_local, "service", None)
        if cached is not None:
            return cached

        if self.service_factory is not None:
            built = self.service_factory()
            self._thread_local.service = built
            return built
        if self.service is not None:
            self._thread_local.service = self.service
            return self.service
        raise ValueError("metrics bridge requires service or service_factory")

    def _resolve_namespace(self, query: dict[str, list[str]]) -> str:
        values = [item.strip() for item in query.get("namespace", []) if item.strip()]
        if values:
            return values[0]
        return self.default_namespace

    def _resolve_window_minutes(self, query: dict[str, list[str]]) -> int:
        values = [item.strip() for item in query.get("window_minutes", []) if item.strip()]
        if not values:
            return self.default_window_minutes
        return _parse_positive_int(values[0], self.default_window_minutes)

    def _resolve_stream_interval_seconds(self, query: dict[str, list[str]]) -> float:
        values = [item.strip() for item in query.get("interval_seconds", []) if item.strip()]
        if not values:
            return self.default_stream_interval_seconds
        return _parse_positive_float(values[0], self.default_stream_interval_seconds)

    def _resolve_stream_include_metrics(self, query: dict[str, list[str]]) -> bool:
        values = [item.strip() for item in query.get("include_metrics", []) if item.strip()]
        if not values:
            return self.default_stream_include_metrics
        return _parse_bool(values[0], self.default_stream_include_metrics)

    @staticmethod
    def _resolve_max_events(query: dict[str, list[str]]) -> int | None:
        values = [item.strip() for item in query.get("max_events", []) if item.strip()]
        if not values:
            return None
        parsed = _parse_positive_int(values[0], 0)
        if parsed <= 0:
            return None
        return parsed

    def _is_authorized(self, *, header_value: str | None, query: dict[str, list[str]]) -> bool:
        if not self.token:
            return True

        candidate = ""
        if header_value:
            normalized = header_value.strip()
            prefix = "bearer "
            if normalized.lower().startswith(prefix):
                candidate = normalized[len(prefix) :].strip()
            else:
                candidate = normalized
        if not candidate:
            query_values = [item.strip() for item in query.get("token", []) if item.strip()]
            if query_values:
                candidate = query_values[0]
        return candidate == self.token

    @staticmethod
    def _write_json(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        handler.send_response(int(status))
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    @staticmethod
    def _write_text(handler: BaseHTTPRequestHandler, status: HTTPStatus, text: str) -> None:
        body = text.encode("utf-8")
        handler.send_response(int(status))
        handler.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    @staticmethod
    def _write_sse_event(
        handler: BaseHTTPRequestHandler,
        *,
        event: str,
        payload: dict[str, Any],
        event_id: str,
        retry_ms: int,
    ) -> None:
        body = (
            f"id: {event_id}\n"
            f"event: {event}\n"
            f"retry: {retry_ms}\n"
            f"data: {json.dumps(payload, ensure_ascii=True, sort_keys=True)}\n\n"
        ).encode("utf-8")
        handler.wfile.write(body)
        handler.wfile.flush()

    def _serve_job_stream(
        self,
        *,
        handler: BaseHTTPRequestHandler,
        service: MemoryPolicyService,
        namespace: str,
        window_minutes: int,
        interval_seconds: float,
        include_metrics: bool,
        max_events: int | None,
    ) -> None:
        handler.send_response(int(HTTPStatus.OK))
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "close")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()

        retry_ms = max(1, int(interval_seconds * 1000))
        event_count = 0
        try:
            while True:
                health = service.ops_health(namespace=namespace)
                event_count += 1
                payload: dict[str, Any] = {
                    "namespace": namespace,
                    "window_minutes": window_minutes,
                    "event_index": event_count,
                    "health": health,
                }
                if include_metrics:
                    payload["metrics"] = service.ops_metrics(
                        window_minutes=window_minutes,
                        namespace=namespace,
                    )

                try:
                    MetricsHTTPBridge._write_sse_event(
                        handler,
                        event="jobs.snapshot",
                        payload=payload,
                        event_id=str(event_count),
                        retry_ms=retry_ms,
                    )
                except (BrokenPipeError, ConnectionResetError):
                    return

                if max_events is not None and event_count >= max_events:
                    return

                time.sleep(interval_seconds)
        finally:
            handler.close_connection = True

    def build_server(self) -> ThreadingHTTPServer:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query, keep_blank_values=False)
                auth_header = self.headers.get("Authorization")
                if not bridge._is_authorized(header_value=auth_header, query=query):
                    body = json.dumps(
                        {"error": "unauthorized", "message": "valid bearer token is required"},
                        ensure_ascii=True,
                    ).encode("utf-8")
                    self.send_response(int(HTTPStatus.UNAUTHORIZED))
                    self.send_header("WWW-Authenticate", "Bearer")
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                namespace = bridge._resolve_namespace(query)
                window_minutes = bridge._resolve_window_minutes(query)
                service = bridge._get_service()

                if parsed.path == "/health":
                    snapshot = service.ops_health(namespace=namespace)
                    MetricsHTTPBridge._write_json(self, HTTPStatus.OK, snapshot)
                    return

                if parsed.path == "/metrics":
                    export = service.ops_metrics_prometheus(
                        window_minutes=window_minutes,
                        namespace=namespace,
                    )
                    text = str(export.get("text", ""))
                    MetricsHTTPBridge._write_text(self, HTTPStatus.OK, text)
                    return

                if parsed.path == "/metrics/otel":
                    export = service.ops_metrics_otel(
                        window_minutes=window_minutes,
                        namespace=namespace,
                    )
                    payload = dict(export.get("payload", {}))
                    MetricsHTTPBridge._write_json(self, HTTPStatus.OK, payload)
                    return

                if parsed.path == "/stream/jobs":
                    interval_seconds = bridge._resolve_stream_interval_seconds(query)
                    include_metrics = bridge._resolve_stream_include_metrics(query)
                    max_events = bridge._resolve_max_events(query)
                    bridge._serve_job_stream(
                        handler=self,
                        service=service,
                        namespace=namespace,
                        window_minutes=window_minutes,
                        interval_seconds=interval_seconds,
                        include_metrics=include_metrics,
                        max_events=max_events,
                    )
                    return

                MetricsHTTPBridge._write_json(
                    self,
                    HTTPStatus.NOT_FOUND,
                    {"error": "not_found", "path": parsed.path},
                )

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
                _ = (fmt, args)
                return

        return ThreadingHTTPServer((self.host, self.port), Handler)


def _install_shutdown_handlers(server: ThreadingHTTPServer) -> None:
    def _handler(_sig: int, _frame: object) -> None:
        server.shutdown()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def main() -> None:
    settings = load_settings_from_env()
    bridge = MetricsHTTPBridge(
        service_factory=lambda: build_service_from_settings(settings=settings),
        host=settings.metrics_http_host,
        port=settings.metrics_http_port,
        default_namespace=settings.metrics_http_namespace,
        default_window_minutes=settings.metrics_http_window_minutes,
        default_stream_interval_seconds=settings.metrics_http_stream_interval_seconds,
        default_stream_include_metrics=settings.metrics_http_stream_include_metrics,
        token=settings.metrics_http_token,
    )
    server = bridge.build_server()
    _install_shutdown_handlers(server=server)

    address, port = server.server_address[:2]
    print(
        "metrics_http.start",
        {
            "host": address,
            "port": port,
            "default_namespace": settings.metrics_http_namespace,
            "default_window_minutes": settings.metrics_http_window_minutes,
            "default_stream_interval_seconds": settings.metrics_http_stream_interval_seconds,
            "default_stream_include_metrics": settings.metrics_http_stream_include_metrics,
            "token_enabled": bool(settings.metrics_http_token),
        },
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        print("metrics_http.stop", {"host": address, "port": port}, flush=True)


if __name__ == "__main__":
    main()
