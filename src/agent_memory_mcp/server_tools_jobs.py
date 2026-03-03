from __future__ import annotations

from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from agent_memory_mcp.service import MemoryPolicyService

AuthorizeFn = Callable[[str | None, str, str | None], str]
GetServiceFn = Callable[[], MemoryPolicyService]


def register_jobs_tools(
    mcp: FastMCP,
    *,
    authorize: AuthorizeFn,
    get_service: GetServiceFn,
) -> None:
    @mcp.tool(name="jobs.submit")
    def jobs_submit(
        job_type: str,
        payload: dict[str, Any],
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Submit an async job for supported operations."""
        resolved_ns = authorize(namespace, "jobs:submit", api_key)
        return get_service().jobs_submit(job_type=job_type, payload=payload, namespace=resolved_ns)

    @mcp.tool(name="jobs.run_pending")
    def jobs_run_pending(
        limit: int = 1,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Run pending jobs for a namespace and persist results."""
        resolved_ns = authorize(namespace, "jobs:run", api_key)
        return get_service().jobs_run_pending(limit=limit, namespace=resolved_ns)

    @mcp.tool(name="jobs.status")
    def jobs_status(
        job_id: int,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Get current job status without returning full result payload."""
        resolved_ns = authorize(namespace, "jobs:read", api_key)
        return get_service().jobs_status(job_id=job_id, namespace=resolved_ns)

    @mcp.tool(name="jobs.result")
    def jobs_result(
        job_id: int,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Get final or in-progress job result payload."""
        resolved_ns = authorize(namespace, "jobs:read", api_key)
        return get_service().jobs_result(job_id=job_id, namespace=resolved_ns)
