from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class KeyPolicy:
    namespaces: set[str]
    scopes: set[str]


class Authorizer:
    def __init__(self, mode: str, default_namespace: str, policies: dict[str, KeyPolicy]) -> None:
        normalized_mode = (mode or "off").strip().lower()
        if normalized_mode not in {"off", "api_key"}:
            raise ValueError(f"unsupported auth mode '{mode}'")

        self.mode = normalized_mode
        self.default_namespace = default_namespace
        self.policies = policies

        if self.mode == "api_key" and not self.policies:
            raise ValueError("auth mode 'api_key' requires at least one key policy")

    @classmethod
    def from_sources(
        cls,
        *,
        mode: str,
        default_namespace: str,
        keys_json: str | None,
        keys_file: str | None,
    ) -> "Authorizer":
        raw = cls._load_raw_policies(keys_json=keys_json, keys_file=keys_file)
        return cls.from_raw_policies(
            mode=mode,
            default_namespace=default_namespace,
            raw_policies=raw,
        )

    @staticmethod
    def _load_raw_policies(keys_json: str | None, keys_file: str | None) -> dict[str, Any]:
        raw: dict[str, dict] = {}

        if keys_file:
            payload = Path(keys_file).read_text(encoding="utf-8")
            raw = json.loads(payload)
        elif keys_json:
            raw = json.loads(keys_json)

        if not isinstance(raw, dict):
            return {}
        return raw

    @classmethod
    def from_raw_policies(
        cls,
        *,
        mode: str,
        default_namespace: str,
        raw_policies: dict[str, Any],
    ) -> "Authorizer":
        policies = cls._parse_policies(raw_policies=raw_policies)
        return cls(mode=mode, default_namespace=default_namespace, policies=policies)

    @staticmethod
    def _parse_policies(raw_policies: dict[str, Any]) -> dict[str, KeyPolicy]:
        if not isinstance(raw_policies, dict):
            return {}

        policy_map: dict[str, Any]
        auth_block = raw_policies.get("auth")
        if isinstance(auth_block, dict) and isinstance(auth_block.get("api_keys"), dict):
            policy_map = auth_block["api_keys"]
        elif isinstance(raw_policies.get("api_keys"), dict):
            policy_map = raw_policies["api_keys"]
        else:
            policy_map = raw_policies

        policies: dict[str, KeyPolicy] = {}
        for api_key, config in policy_map.items():
            if not isinstance(api_key, str) or not isinstance(config, dict):
                continue
            enabled = bool(config.get("enabled", True))
            if not enabled:
                continue
            namespaces = {str(item) for item in config.get("namespaces", ["default"])}
            scopes = {str(item) for item in config.get("scopes", [])}
            policies[api_key] = KeyPolicy(namespaces=namespaces, scopes=scopes)

        return policies

    def authorize(self, *, api_key: str | None, namespace: str | None, scope: str) -> str:
        resolved_namespace = (namespace or self.default_namespace).strip() or self.default_namespace

        if self.mode == "off":
            return resolved_namespace

        if not api_key:
            raise PermissionError("api_key is required")

        policy = self.policies.get(api_key)
        if policy is None:
            raise PermissionError("api_key is invalid")

        if not self._matches_namespace(policy.namespaces, resolved_namespace):
            raise PermissionError(f"api_key not allowed for namespace '{resolved_namespace}'")

        if not self._matches_scope(policy.scopes, scope):
            raise PermissionError(f"api_key lacks scope '{scope}'")

        return resolved_namespace

    @staticmethod
    def _matches_namespace(granted: set[str], required_namespace: str) -> bool:
        return "*" in granted or required_namespace in granted

    @staticmethod
    def _matches_scope(granted: set[str], required_scope: str) -> bool:
        if "*" in granted or required_scope in granted:
            return True

        required_family = required_scope.split(":", maxsplit=1)[0]
        return f"{required_family}:*" in granted
