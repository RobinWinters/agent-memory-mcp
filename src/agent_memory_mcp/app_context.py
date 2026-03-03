from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_memory_mcp.auth import Authorizer
from agent_memory_mcp.factory import build_service
from agent_memory_mcp.keyring import FileKeyring
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.settings import Settings


@dataclass
class AppContext:
    settings: Settings | None = None
    service: MemoryPolicyService | None = None
    authorizer: Authorizer | None = None
    keyring: FileKeyring | None = None
    keyring_mtime_ns: int | None = None

    def get_settings(self) -> Settings:
        if self.settings is None:
            self.settings = Settings.from_env()
        return self.settings

    def get_keyring(self) -> FileKeyring | None:
        settings = self.get_settings()
        if not settings.keyring_file:
            return None
        if self.keyring is None or str(self.keyring.path) != settings.keyring_file:
            self.keyring = FileKeyring(settings.keyring_file)
        return self.keyring

    def require_keyring(self) -> FileKeyring:
        keyring = self.get_keyring()
        if keyring is None:
            raise ValueError("keyring is not configured; set AGENT_MEMORY_KEYRING_FILE")
        keyring.ensure_exists()
        return keyring

    def _build_env_authorizer(self, settings: Settings) -> Authorizer:
        return Authorizer.from_sources(
            mode=settings.auth_mode,
            default_namespace=settings.default_namespace,
            keys_json=settings.auth_api_keys_json,
            keys_file=settings.auth_api_keys_file,
        )

    def apply_runtime_security(self, force: bool = False) -> dict[str, Any]:
        settings = self.get_settings()
        keyring = self.get_keyring()

        if keyring is None:
            should_reload = force or self.authorizer is None or self.keyring_mtime_ns != -1
            if should_reload:
                self.authorizer = self._build_env_authorizer(settings)
            self.keyring_mtime_ns = -1
            if should_reload and self.service is not None:
                policy_secret = settings.policy_signing_secret
                audit_secret = settings.audit_signing_secret
                self.service.update_signing_keys(
                    policy_active_secret=policy_secret,
                    audit_active_secret=audit_secret,
                    policy_verification_secrets=((policy_secret,) if policy_secret else ()),
                    audit_verification_secrets=((audit_secret,) if audit_secret else ()),
                )
            return {"reloaded": should_reload, "source": "env"}

        keyring.ensure_exists()
        current_mtime = keyring.mtime_ns()
        should_reload = force or self.authorizer is None or current_mtime != self.keyring_mtime_ns
        if not should_reload:
            return {"reloaded": False, "source": "keyring"}

        raw_policies = keyring.get_auth_raw_policies()
        if raw_policies:
            self.authorizer = Authorizer.from_raw_policies(
                mode=settings.auth_mode,
                default_namespace=settings.default_namespace,
                raw_policies=raw_policies,
            )
            auth_source = "keyring"
        else:
            self.authorizer = self._build_env_authorizer(settings)
            auth_source = "env"

        policy_active_secret, policy_verification_secrets = keyring.get_signing_material(
            purpose="policy",
            fallback_secret=settings.policy_signing_secret,
        )
        audit_active_secret, audit_verification_secrets = keyring.get_signing_material(
            purpose="audit",
            fallback_secret=settings.audit_signing_secret or settings.policy_signing_secret,
        )
        if self.service is not None:
            self.service.update_signing_keys(
                policy_active_secret=policy_active_secret,
                audit_active_secret=audit_active_secret,
                policy_verification_secrets=policy_verification_secrets,
                audit_verification_secrets=audit_verification_secrets,
            )

        self.keyring_mtime_ns = keyring.mtime_ns()
        return {
            "reloaded": True,
            "source": "keyring",
            "auth_source": auth_source,
            "keyring_mtime_ns": self.keyring_mtime_ns,
        }

    def get_authorizer(self) -> Authorizer:
        self.apply_runtime_security(force=False)
        if self.authorizer is None:
            self.authorizer = self._build_env_authorizer(self.get_settings())
        return self.authorizer

    def get_service(self) -> MemoryPolicyService:
        if self.service is None:
            settings = self.get_settings()
            self.service = build_service(settings=settings)
        self.apply_runtime_security(force=False)
        return self.service

    def authorize(self, namespace: str | None, scope: str, api_key: str | None) -> str:
        self.apply_runtime_security(force=False)
        return self.get_authorizer().authorize(api_key=api_key, namespace=namespace, scope=scope)
