from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from agent_memory_mcp.app_context import AppContext
from agent_memory_mcp.server_tools_jobs import register_jobs_tools
from agent_memory_mcp.server_tools_memory import register_memory_tools
from agent_memory_mcp.server_tools_ops import register_ops_tools
from agent_memory_mcp.server_tools_policy import register_policy_tools

mcp = FastMCP("agent-memory-mcp")
_context = AppContext()

register_memory_tools(mcp, authorize=_context.authorize, get_service=_context.get_service)
register_policy_tools(mcp, authorize=_context.authorize, get_service=_context.get_service)
register_jobs_tools(mcp, authorize=_context.authorize, get_service=_context.get_service)
register_ops_tools(
    mcp,
    authorize=_context.authorize,
    get_service=_context.get_service,
    get_keyring=_context.get_keyring,
    require_keyring=_context.require_keyring,
    apply_runtime_security=_context.apply_runtime_security,
)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
