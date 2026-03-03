from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_memory_mcp.auth import Authorizer
from agent_memory_mcp.factory import build_service
from agent_memory_mcp.keyring import FileKeyring
from agent_memory_mcp.server_tools_jobs import register_jobs_tools
from agent_memory_mcp.server_tools_memory import register_memory_tools
from agent_memory_mcp.server_tools_ops import register_ops_tools
from agent_memory_mcp.server_tools_policy import register_policy_tools
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


register_memory_tools(mcp, authorize=authorize, get_service=get_service)
register_policy_tools(mcp, authorize=authorize, get_service=get_service)
register_jobs_tools(mcp, authorize=authorize, get_service=get_service)
register_ops_tools(
    mcp,
    authorize=authorize,
    get_service=get_service,
    get_keyring=get_keyring,
    require_keyring=require_keyring,
    apply_runtime_security=_apply_runtime_security,
)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
