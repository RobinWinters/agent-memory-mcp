from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_memory_mcp.auth import Authorizer
from agent_memory_mcp.factory import build_service
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.settings import Settings

mcp = FastMCP("agent-memory-mcp")

_settings_singleton: Settings | None = None
_service_singleton: MemoryPolicyService | None = None
_authorizer_singleton: Authorizer | None = None


def get_settings() -> Settings:
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = Settings.from_env()
    return _settings_singleton


def get_authorizer() -> Authorizer:
    global _authorizer_singleton
    if _authorizer_singleton is None:
        settings = get_settings()
        _authorizer_singleton = Authorizer.from_sources(
            mode=settings.auth_mode,
            default_namespace=settings.default_namespace,
            keys_json=settings.auth_api_keys_json,
            keys_file=settings.auth_api_keys_file,
        )
    return _authorizer_singleton


def get_service() -> MemoryPolicyService:
    global _service_singleton
    if _service_singleton is None:
        settings = get_settings()
        _service_singleton = build_service(settings=settings)
    return _service_singleton


def authorize(namespace: str | None, scope: str, api_key: str | None) -> str:
    return get_authorizer().authorize(api_key=api_key, namespace=namespace, scope=scope)


@mcp.tool(name="memory.append")
def memory_append(
    session_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Append a raw event to a session."""
    resolved_ns = authorize(namespace=namespace, scope="memory:write", api_key=api_key)
    return get_service().append_event(
        session_id=session_id,
        role=role,
        content=content,
        metadata=metadata,
        namespace=resolved_ns,
    )


@mcp.tool(name="memory.distill")
def memory_distill(
    session_id: str,
    max_lines: int = 6,
    async_mode: bool = False,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Create a distilled memory note from session events."""
    resolved_ns = authorize(namespace=namespace, scope="memory:write", api_key=api_key)
    return get_service().distill_session(
        session_id=session_id,
        max_lines=max_lines,
        namespace=resolved_ns,
        async_mode=async_mode,
    )


@mcp.tool(name="memory.search")
def memory_search(
    query: str,
    k: int = 5,
    namespace: str | None = None,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Search memory notes using configured embeddings backend."""
    resolved_ns = authorize(namespace=namespace, scope="memory:read", api_key=api_key)
    return get_service().memory_search(query=query, k=k, namespace=resolved_ns)


@mcp.tool(name="policy.get")
def policy_get(
    active_version: bool = True,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Get active policy content and metadata."""
    _ = active_version
    resolved_ns = authorize(namespace=namespace, scope="policy:read", api_key=api_key)
    return get_service().policy_get(namespace=resolved_ns)


@mcp.tool(name="policy.propose")
def policy_propose(
    delta_md: str,
    evidence_refs: list[str] | None = None,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Create a policy proposal from markdown delta plus evidence references."""
    resolved_ns = authorize(namespace=namespace, scope="policy:propose", api_key=api_key)
    return get_service().policy_propose(delta_md=delta_md, evidence_refs=evidence_refs, namespace=resolved_ns)


@mcp.tool(name="policy.evaluate")
def policy_evaluate(
    proposal_id: str,
    async_mode: bool = False,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Run weighted gate checks and regression suite for a policy proposal."""
    resolved_ns = authorize(namespace=namespace, scope="policy:evaluate", api_key=api_key)
    return get_service().policy_evaluate(
        proposal_id=proposal_id,
        namespace=resolved_ns,
        async_mode=async_mode,
    )


@mcp.tool(name="policy.promote")
def policy_promote(
    proposal_id: str,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Promote a passing policy proposal to active policy."""
    resolved_ns = authorize(namespace=namespace, scope="policy:promote", api_key=api_key)
    return get_service().policy_promote(proposal_id=proposal_id, namespace=resolved_ns)


@mcp.tool(name="policy.rollback")
def policy_rollback(
    version_id: str,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Rollback active policy to a previously promoted version."""
    resolved_ns = authorize(namespace=namespace, scope="policy:rollback", api_key=api_key)
    return get_service().policy_rollback(version_id=version_id, namespace=resolved_ns)


@mcp.tool(name="jobs.submit")
def jobs_submit(
    job_type: str,
    payload: dict[str, Any],
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Submit an async job for supported operations."""
    resolved_ns = authorize(namespace=namespace, scope="jobs:submit", api_key=api_key)
    return get_service().jobs_submit(job_type=job_type, payload=payload, namespace=resolved_ns)


@mcp.tool(name="jobs.run_pending")
def jobs_run_pending(
    limit: int = 1,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Run pending jobs for a namespace and persist results."""
    resolved_ns = authorize(namespace=namespace, scope="jobs:run", api_key=api_key)
    return get_service().jobs_run_pending(limit=limit, namespace=resolved_ns)


@mcp.tool(name="jobs.status")
def jobs_status(
    job_id: int,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Get current job status without returning full result payload."""
    resolved_ns = authorize(namespace=namespace, scope="jobs:read", api_key=api_key)
    return get_service().jobs_status(job_id=job_id, namespace=resolved_ns)


@mcp.tool(name="jobs.result")
def jobs_result(
    job_id: int,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Get final or in-progress job result payload."""
    resolved_ns = authorize(namespace=namespace, scope="jobs:read", api_key=api_key)
    return get_service().jobs_result(job_id=job_id, namespace=resolved_ns)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
