from __future__ import annotations

from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from agent_memory_mcp.service import MemoryPolicyService

AuthorizeFn = Callable[[str | None, str, str | None], str]
GetServiceFn = Callable[[], MemoryPolicyService]


def register_policy_tools(
    mcp: FastMCP,
    *,
    authorize: AuthorizeFn,
    get_service: GetServiceFn,
) -> None:
    @mcp.tool(name="policy.get")
    def policy_get(
        active_version: bool = True,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Get active policy content and metadata."""
        _ = active_version
        resolved_ns = authorize(namespace, "policy:read", api_key)
        return get_service().policy_get(namespace=resolved_ns)

    @mcp.tool(name="policy.propose")
    def policy_propose(
        delta_md: str,
        evidence_refs: list[str] | None = None,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a policy proposal from markdown delta plus evidence references."""
        resolved_ns = authorize(namespace, "policy:propose", api_key)
        return get_service().policy_propose(delta_md=delta_md, evidence_refs=evidence_refs, namespace=resolved_ns)

    @mcp.tool(name="policy.evaluate")
    def policy_evaluate(
        proposal_id: str,
        async_mode: bool = False,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Run weighted gate checks and regression suite for a policy proposal."""
        resolved_ns = authorize(namespace, "policy:evaluate", api_key)
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
        resolved_ns = authorize(namespace, "policy:promote", api_key)
        return get_service().policy_promote(proposal_id=proposal_id, namespace=resolved_ns)

    @mcp.tool(name="policy.rollback")
    def policy_rollback(
        version_id: str,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Rollback active policy to a previously promoted version."""
        resolved_ns = authorize(namespace, "policy:rollback", api_key)
        return get_service().policy_rollback(version_id=version_id, namespace=resolved_ns)
