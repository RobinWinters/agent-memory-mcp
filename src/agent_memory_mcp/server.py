from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_memory_mcp.auth import Authorizer
from agent_memory_mcp.factory import build_service
from agent_memory_mcp.keyring import FileKeyring
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.settings import Settings

mcp = FastMCP("agent-memory-mcp")

_settings_singleton: Settings | None = None
_service_singleton: MemoryPolicyService | None = None
_authorizer_singleton: Authorizer | None = None
_keyring_singleton: FileKeyring | None = None
_keyring_mtime_ns: int | None = None


def get_settings() -> Settings:
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = Settings.from_env()
    return _settings_singleton


def get_keyring() -> FileKeyring | None:
    global _keyring_singleton
    settings = get_settings()
    if not settings.keyring_file:
        return None
    if _keyring_singleton is None or str(_keyring_singleton.path) != settings.keyring_file:
        _keyring_singleton = FileKeyring(settings.keyring_file)
    return _keyring_singleton


def require_keyring() -> FileKeyring:
    keyring = get_keyring()
    if keyring is None:
        raise ValueError("keyring is not configured; set AGENT_MEMORY_KEYRING_FILE")
    keyring.ensure_exists()
    return keyring


def _build_env_authorizer(settings: Settings) -> Authorizer:
    return Authorizer.from_sources(
        mode=settings.auth_mode,
        default_namespace=settings.default_namespace,
        keys_json=settings.auth_api_keys_json,
        keys_file=settings.auth_api_keys_file,
    )


def _apply_runtime_security(force: bool = False) -> dict[str, Any]:
    global _authorizer_singleton
    global _keyring_mtime_ns

    settings = get_settings()
    keyring = get_keyring()

    if keyring is None:
        should_reload = force or _authorizer_singleton is None or _keyring_mtime_ns != -1
        if should_reload:
            _authorizer_singleton = _build_env_authorizer(settings)
        _keyring_mtime_ns = -1
        if should_reload and _service_singleton is not None:
            policy_secret = settings.policy_signing_secret
            audit_secret = settings.audit_signing_secret
            _service_singleton.update_signing_keys(
                policy_active_secret=policy_secret,
                audit_active_secret=audit_secret,
                policy_verification_secrets=((policy_secret,) if policy_secret else ()),
                audit_verification_secrets=((audit_secret,) if audit_secret else ()),
            )
        return {"reloaded": should_reload, "source": "env"}

    keyring.ensure_exists()
    current_mtime = keyring.mtime_ns()
    should_reload = force or _authorizer_singleton is None or current_mtime != _keyring_mtime_ns
    if not should_reload:
        return {"reloaded": False, "source": "keyring"}

    raw_policies = keyring.get_auth_raw_policies()
    if raw_policies:
        _authorizer_singleton = Authorizer.from_raw_policies(
            mode=settings.auth_mode,
            default_namespace=settings.default_namespace,
            raw_policies=raw_policies,
        )
        auth_source = "keyring"
    else:
        _authorizer_singleton = _build_env_authorizer(settings)
        auth_source = "env"

    policy_active_secret, policy_verification_secrets = keyring.get_signing_material(
        purpose="policy",
        fallback_secret=settings.policy_signing_secret,
    )
    audit_active_secret, audit_verification_secrets = keyring.get_signing_material(
        purpose="audit",
        fallback_secret=settings.audit_signing_secret or settings.policy_signing_secret,
    )
    if _service_singleton is not None:
        _service_singleton.update_signing_keys(
            policy_active_secret=policy_active_secret,
            audit_active_secret=audit_active_secret,
            policy_verification_secrets=policy_verification_secrets,
            audit_verification_secrets=audit_verification_secrets,
        )

    _keyring_mtime_ns = keyring.mtime_ns()
    return {
        "reloaded": True,
        "source": "keyring",
        "auth_source": auth_source,
        "keyring_mtime_ns": _keyring_mtime_ns,
    }


def get_authorizer() -> Authorizer:
    _apply_runtime_security(force=False)
    global _authorizer_singleton
    if _authorizer_singleton is None:
        _authorizer_singleton = _build_env_authorizer(get_settings())
    return _authorizer_singleton


def get_service() -> MemoryPolicyService:
    global _service_singleton
    if _service_singleton is None:
        settings = get_settings()
        _service_singleton = build_service(settings=settings)
    _apply_runtime_security(force=False)
    return _service_singleton


def authorize(namespace: str | None, scope: str, api_key: str | None) -> str:
    _apply_runtime_security(force=False)
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


@mcp.tool(name="ops.health")
def ops_health(
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Return queue health snapshot including ready/deferred/stuck counts."""
    resolved_ns = authorize(namespace=namespace, scope="jobs:read", api_key=api_key)
    return get_service().ops_health(namespace=resolved_ns)


@mcp.tool(name="ops.metrics")
def ops_metrics(
    window_minutes: int = 60,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Return job throughput/latency metrics for a time window."""
    resolved_ns = authorize(namespace=namespace, scope="jobs:read", api_key=api_key)
    return get_service().ops_metrics(window_minutes=window_minutes, namespace=resolved_ns)


@mcp.tool(name="ops.metrics_prometheus")
def ops_metrics_prometheus(
    window_minutes: int = 60,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Return metrics as Prometheus text exposition format."""
    resolved_ns = authorize(namespace=namespace, scope="jobs:read", api_key=api_key)
    return get_service().ops_metrics_prometheus(window_minutes=window_minutes, namespace=resolved_ns)


@mcp.tool(name="ops.metrics_otel")
def ops_metrics_otel(
    window_minutes: int = 60,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Return metrics in OpenTelemetry-style JSON payload shape."""
    resolved_ns = authorize(namespace=namespace, scope="jobs:read", api_key=api_key)
    return get_service().ops_metrics_otel(window_minutes=window_minutes, namespace=resolved_ns)


@mcp.tool(name="ops.audit_recent")
def ops_audit_recent(
    limit: int = 50,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Return recent audit log entries for tamper-evident policy events."""
    resolved_ns = authorize(namespace=namespace, scope="jobs:read", api_key=api_key)
    return get_service().ops_audit_recent(limit=limit, namespace=resolved_ns)


@mcp.tool(name="ops.audit_verify")
def ops_audit_verify(
    limit: int = 1000,
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Verify audit hash-chain continuity and policy artifact signatures."""
    resolved_ns = authorize(namespace=namespace, scope="jobs:read", api_key=api_key)
    return get_service().ops_audit_verify(limit=limit, namespace=resolved_ns)


@mcp.tool(name="ops.keyring_status")
def ops_keyring_status(
    namespace: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Return keyring status (without exposing stored signing secrets)."""
    _ = authorize(namespace=namespace, scope="security:read", api_key=api_key)
    keyring = get_keyring()
    if keyring is None:
        return {
            "enabled": False,
            "message": "Set AGENT_MEMORY_KEYRING_FILE to enable keyring management.",
        }

    keyring.ensure_exists()
    runtime = _apply_runtime_security(force=False)
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
    _ = authorize(namespace=namespace, scope="security:read", api_key=api_key)
    keyring = require_keyring()
    runtime = _apply_runtime_security(force=True)
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
    _ = authorize(namespace=namespace, scope="security:manage", api_key=api_key)
    keyring = require_keyring()
    rotated = keyring.rotate_signing_key(
        purpose=purpose,
        secret=secret,
        key_id=key_id,
        disable_previous=disable_previous,
    )
    runtime = _apply_runtime_security(force=True)
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
    _ = authorize(namespace=namespace, scope="security:manage", api_key=api_key)
    keyring = require_keyring()
    updated = keyring.upsert_api_key(
        api_key=managed_api_key,
        namespaces=namespaces,
        scopes=scopes,
        enabled=enabled,
        label=label,
    )
    runtime = _apply_runtime_security(force=True)
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
    _ = authorize(namespace=namespace, scope="security:manage", api_key=api_key)
    keyring = require_keyring()
    updated = keyring.disable_api_key(api_key=managed_api_key)
    runtime = _apply_runtime_security(force=True)
    return {
        "enabled": True,
        "keyring_path": str(keyring.path),
        "api_key_policy": updated,
        "runtime": runtime,
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
