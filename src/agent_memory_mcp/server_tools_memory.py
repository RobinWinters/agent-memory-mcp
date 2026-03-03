from __future__ import annotations

from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from agent_memory_mcp.service import MemoryPolicyService

AuthorizeFn = Callable[[str | None, str, str | None], str]
GetServiceFn = Callable[[], MemoryPolicyService]


def register_memory_tools(
    mcp: FastMCP,
    *,
    authorize: AuthorizeFn,
    get_service: GetServiceFn,
) -> None:
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
        resolved_ns = authorize(namespace, "memory:write", api_key)
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
        resolved_ns = authorize(namespace, "memory:write", api_key)
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
        resolved_ns = authorize(namespace, "memory:read", api_key)
        return get_service().memory_search(query=query, k=k, namespace=resolved_ns)

    @mcp.tool(name="memory.handoff_export")
    def memory_handoff_export(
        query: str | None = None,
        k: int = 20,
        include_policy: bool = True,
        include_events: bool = False,
        max_events_per_session: int = 20,
        sign: bool = False,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Export portable memory/policy handoff payload for model-agnostic session transfer."""
        resolved_ns = authorize(namespace, "memory:read", api_key)
        if sign:
            _ = authorize(resolved_ns, "security:read", api_key)
        if include_policy:
            _ = authorize(resolved_ns, "policy:read", api_key)
        return get_service().memory_handoff_export(
            query=query,
            k=k,
            include_policy=include_policy,
            include_events=include_events,
            max_events_per_session=max_events_per_session,
            sign=sign,
            namespace=resolved_ns,
        )

    @mcp.tool(name="memory.handoff_import")
    def memory_handoff_import(
        handoff: dict[str, Any],
        session_id_prefix: str = "imported",
        import_policy: bool = False,
        import_events: bool = False,
        max_events_per_session: int = 200,
        verify: bool = False,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Import portable handoff payload into local memory/policy stores."""
        resolved_ns = authorize(namespace, "memory:write", api_key)
        if verify:
            _ = authorize(resolved_ns, "security:read", api_key)
        if import_policy:
            _ = authorize(resolved_ns, "policy:promote", api_key)
        return get_service().memory_handoff_import(
            handoff=handoff,
            session_id_prefix=session_id_prefix,
            import_policy=import_policy,
            import_events=import_events,
            max_events_per_session=max_events_per_session,
            verify=verify,
            namespace=resolved_ns,
        )
