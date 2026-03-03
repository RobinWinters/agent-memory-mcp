from __future__ import annotations

from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from agent_memory_mcp.keyring import FileKeyring
from agent_memory_mcp.service import MemoryPolicyService

AuthorizeFn = Callable[[str | None, str, str | None], str]
GetServiceFn = Callable[[], MemoryPolicyService]
GetKeyringFn = Callable[[], FileKeyring | None]
RequireKeyringFn = Callable[[], FileKeyring]
ApplyRuntimeSecurityFn = Callable[[bool], dict[str, Any]]


def register_ops_tools(
    mcp: FastMCP,
    *,
    authorize: AuthorizeFn,
    get_service: GetServiceFn,
    get_keyring: GetKeyringFn,
    require_keyring: RequireKeyringFn,
    apply_runtime_security: ApplyRuntimeSecurityFn,
) -> None:
    @mcp.tool(name="ops.keyring_list_presets")
    def ops_keyring_list_presets(
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """List built-in auth ACL presets for keyring bootstrap."""
        _ = authorize(namespace, "security:read", api_key)
        keyring = require_keyring()
        return {
            "enabled": True,
            "keyring_path": str(keyring.path),
            "presets": keyring.list_auth_presets(),
        }

    @mcp.tool(name="ops.keyring_apply_preset")
    def ops_keyring_apply_preset(
        preset: str,
        managed_api_key: str,
        namespaces: list[str] | None = None,
        enabled: bool = True,
        label: str | None = None,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply a built-in auth ACL preset to a managed API key and reload authorizer."""
        _ = authorize(namespace, "security:manage", api_key)
        keyring = require_keyring()
        updated = keyring.apply_auth_preset(
            preset=preset,
            api_key=managed_api_key,
            namespaces=namespaces,
            enabled=enabled,
            label=label,
        )
        runtime = apply_runtime_security(True)
        return {
            "enabled": True,
            "keyring_path": str(keyring.path),
            "api_key_policy": updated,
            "runtime": runtime,
        }

    @mcp.tool(name="ops.health")
    def ops_health(
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Return queue health snapshot including ready/deferred/stuck counts."""
        resolved_ns = authorize(namespace, "jobs:read", api_key)
        return get_service().ops_health(namespace=resolved_ns)

    @mcp.tool(name="ops.metrics")
    def ops_metrics(
        window_minutes: int = 60,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Return job throughput/latency metrics for a time window."""
        resolved_ns = authorize(namespace, "jobs:read", api_key)
        return get_service().ops_metrics(window_minutes=window_minutes, namespace=resolved_ns)

    @mcp.tool(name="ops.metrics_prometheus")
    def ops_metrics_prometheus(
        window_minutes: int = 60,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Return metrics as Prometheus text exposition format."""
        resolved_ns = authorize(namespace, "jobs:read", api_key)
        return get_service().ops_metrics_prometheus(window_minutes=window_minutes, namespace=resolved_ns)

    @mcp.tool(name="ops.metrics_otel")
    def ops_metrics_otel(
        window_minutes: int = 60,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Return metrics in OpenTelemetry-style JSON payload shape."""
        resolved_ns = authorize(namespace, "jobs:read", api_key)
        return get_service().ops_metrics_otel(window_minutes=window_minutes, namespace=resolved_ns)

    @mcp.tool(name="ops.audit_recent")
    def ops_audit_recent(
        limit: int = 50,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Return recent audit log entries for tamper-evident policy events."""
        resolved_ns = authorize(namespace, "jobs:read", api_key)
        return get_service().ops_audit_recent(limit=limit, namespace=resolved_ns)

    @mcp.tool(name="ops.audit_verify")
    def ops_audit_verify(
        limit: int = 1000,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Verify audit hash-chain continuity and policy artifact signatures."""
        resolved_ns = authorize(namespace, "jobs:read", api_key)
        return get_service().ops_audit_verify(limit=limit, namespace=resolved_ns)

    @mcp.tool(name="ops.keyring_status")
    def ops_keyring_status(
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Return keyring status (without exposing stored signing secrets)."""
        _ = authorize(namespace, "security:read", api_key)
        keyring = get_keyring()
        if keyring is None:
            return {
                "enabled": False,
                "message": "Set AGENT_MEMORY_KEYRING_FILE to enable keyring management.",
            }

        keyring.ensure_exists()
        runtime = apply_runtime_security(False)
        status = keyring.status()
        status["enabled"] = True
        status["runtime"] = runtime
        return status

    @mcp.tool(name="ops.keyring_reload")
    def ops_keyring_reload(
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Force reload security runtime state from keyring file."""
        _ = authorize(namespace, "security:read", api_key)
        keyring = require_keyring()
        runtime = apply_runtime_security(True)
        return {
            "enabled": True,
            "keyring_path": str(keyring.path),
            "runtime": runtime,
        }

    @mcp.tool(name="ops.keyring_rotate")
    def ops_keyring_rotate(
        purpose: str,
        secret: str | None = None,
        key_id: str | None = None,
        disable_previous: bool = False,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Rotate active signing key for policy or audit channel."""
        _ = authorize(namespace, "security:manage", api_key)
        keyring = require_keyring()
        rotated = keyring.rotate_signing_key(
            purpose=purpose,
            secret=secret,
            key_id=key_id,
            disable_previous=disable_previous,
        )
        runtime = apply_runtime_security(True)
        return {
            "enabled": True,
            "keyring_path": str(keyring.path),
            "rotation": rotated,
            "runtime": runtime,
        }

    @mcp.tool(name="ops.keyring_upsert_api_key")
    def ops_keyring_upsert_api_key(
        managed_api_key: str,
        namespaces: list[str],
        scopes: list[str],
        enabled: bool = True,
        label: str | None = None,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Upsert an API key policy in keyring auth map and reload authorizer."""
        _ = authorize(namespace, "security:manage", api_key)
        keyring = require_keyring()
        updated = keyring.upsert_api_key(
            api_key=managed_api_key,
            namespaces=namespaces,
            scopes=scopes,
            enabled=enabled,
            label=label,
        )
        runtime = apply_runtime_security(True)
        return {
            "enabled": True,
            "keyring_path": str(keyring.path),
            "api_key_policy": updated,
            "runtime": runtime,
        }

    @mcp.tool(name="ops.keyring_disable_api_key")
    def ops_keyring_disable_api_key(
        managed_api_key: str,
        namespace: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Disable an API key policy in keyring auth map and reload authorizer."""
        _ = authorize(namespace, "security:manage", api_key)
        keyring = require_keyring()
        updated = keyring.disable_api_key(api_key=managed_api_key)
        runtime = apply_runtime_security(True)
        return {
            "enabled": True,
            "keyring_path": str(keyring.path),
            "api_key_policy": updated,
            "runtime": runtime,
        }
