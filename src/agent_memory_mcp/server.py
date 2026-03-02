from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_memory_mcp.db import Database
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.vector_index import SimpleVectorIndex

mcp = FastMCP("agent-memory-mcp")

_service_singleton: MemoryPolicyService | None = None


def get_service() -> MemoryPolicyService:
    global _service_singleton
    if _service_singleton is None:
        db_path = os.getenv("AGENT_MEMORY_DB")
        if not db_path:
            root = Path(__file__).resolve().parents[2]
            db_path = str(root / "data" / "agent_memory.db")
        db = Database(db_path=db_path)
        _service_singleton = MemoryPolicyService(db=db, index=SimpleVectorIndex())
    return _service_singleton


@mcp.tool(name="memory.append")
def memory_append(
    session_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a raw event to a session."""
    return get_service().append_event(session_id=session_id, role=role, content=content, metadata=metadata)


@mcp.tool(name="memory.distill")
def memory_distill(session_id: str, max_lines: int = 6) -> dict[str, Any]:
    """Create a distilled memory note from session events."""
    return get_service().distill_session(session_id=session_id, max_lines=max_lines)


@mcp.tool(name="memory.search")
def memory_search(query: str, k: int = 5) -> list[dict[str, Any]]:
    """Search memory notes with deterministic vector similarity."""
    return get_service().memory_search(query=query, k=k)


@mcp.tool(name="policy.get")
def policy_get(active_version: bool = True) -> dict[str, Any]:
    """Get active policy content and metadata."""
    _ = active_version
    return get_service().policy_get()


@mcp.tool(name="policy.propose")
def policy_propose(delta_md: str, evidence_refs: list[str] | None = None) -> dict[str, Any]:
    """Create a policy proposal from markdown delta plus evidence references."""
    return get_service().policy_propose(delta_md=delta_md, evidence_refs=evidence_refs)


@mcp.tool(name="policy.evaluate")
def policy_evaluate(proposal_id: str) -> dict[str, Any]:
    """Run gate checks and score a policy proposal."""
    return get_service().policy_evaluate(proposal_id=proposal_id)


@mcp.tool(name="policy.promote")
def policy_promote(proposal_id: str) -> dict[str, Any]:
    """Promote a passing policy proposal to active policy."""
    return get_service().policy_promote(proposal_id=proposal_id)


@mcp.tool(name="policy.rollback")
def policy_rollback(version_id: str) -> dict[str, Any]:
    """Rollback active policy to a previously promoted version."""
    return get_service().policy_rollback(version_id=version_id)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
