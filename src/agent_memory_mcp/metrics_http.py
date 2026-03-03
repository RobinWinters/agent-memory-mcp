from __future__ import annotations

import json
import signal
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from agent_memory_mcp.factory import build_service
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.settings import Settings


def _parse_positive_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass
class MetricsHTTPBridge:
    host: str
    port: int
    default_namespace: str
    service: MemoryPolicyService | None = None
    service_factory: Callable[[], MemoryPolicyService] | None = None
    default_window_minutes: int = 60
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
    settings = Settings.from_env()
    bridge = MetricsHTTPBridge(
        service_factory=lambda: build_service(settings=settings),
        host=settings.metrics_http_host,
        port=settings.metrics_http_port,
        default_namespace=settings.metrics_http_namespace,
        default_window_minutes=settings.metrics_http_window_minutes,
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
