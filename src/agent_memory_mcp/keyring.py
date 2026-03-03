from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_memory_mcp.auth_presets import list_auth_presets, resolve_auth_preset

KEYRING_SCHEMA_VERSION = 1
SIGNING_PURPOSES = {"policy", "audit"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedupe_non_empty(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        clean = value.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        ordered.append(clean)
    return tuple(ordered)


def _default_document() -> dict[str, Any]:
    now = _utc_now_iso()
    return {
        "schema_version": KEYRING_SCHEMA_VERSION,
        "updated_at": now,
        "signing": {
            "policy": {"active_key_id": None, "keys": {}},
            "audit": {"active_key_id": None, "keys": {}},
        },
        "auth": {"api_keys": {}},
    }


def _normalize_document(raw: Any) -> dict[str, Any]:
    base = _default_document()
    if not isinstance(raw, dict):
        return base

    schema_version = raw.get("schema_version")
    base["schema_version"] = int(schema_version) if isinstance(schema_version, int) else KEYRING_SCHEMA_VERSION
    updated_at = raw.get("updated_at")
    if isinstance(updated_at, str) and updated_at.strip():
        base["updated_at"] = updated_at

    raw_signing = raw.get("signing")
    if isinstance(raw_signing, dict):
        for purpose in SIGNING_PURPOSES:
            section = raw_signing.get(purpose)
            if not isinstance(section, dict):
                continue
            active_key_id = section.get("active_key_id")
            base["signing"][purpose]["active_key_id"] = (
                str(active_key_id).strip() if active_key_id is not None else None
            )
            keys = section.get("keys")
            if isinstance(keys, dict):
                normalized_keys: dict[str, dict[str, Any]] = {}
                for key_id, config in keys.items():
                    if not isinstance(key_id, str) or not isinstance(config, dict):
                        continue
                    secret = str(config.get("secret", "")).strip()
                    if not secret:
                        continue
                    normalized_keys[key_id] = {
                        "secret": secret,
                        "enabled": bool(config.get("enabled", True)),
                        "created_at": str(config.get("created_at", _utc_now_iso())),
                    }
                    if "description" in config and isinstance(config["description"], str):
                        normalized_keys[key_id]["description"] = config["description"]
                base["signing"][purpose]["keys"] = normalized_keys

    raw_auth = raw.get("auth")
    if isinstance(raw_auth, dict):
        raw_api_keys = raw_auth.get("api_keys")
        if isinstance(raw_api_keys, dict):
            normalized_api_keys: dict[str, dict[str, Any]] = {}
            for api_key, config in raw_api_keys.items():
                if not isinstance(api_key, str) or not isinstance(config, dict):
                    continue
                namespaces = [str(item).strip() for item in config.get("namespaces", ["default"])]
                scopes = [str(item).strip() for item in config.get("scopes", [])]
                cleaned_namespaces = [item for item in namespaces if item]
                cleaned_scopes = [item for item in scopes if item]
                entry: dict[str, Any] = {
                    "namespaces": cleaned_namespaces or ["default"],
                    "scopes": cleaned_scopes,
                    "enabled": bool(config.get("enabled", True)),
                    "created_at": str(config.get("created_at", _utc_now_iso())),
                }
                if "label" in config and isinstance(config["label"], str):
                    entry["label"] = config["label"]
                normalized_api_keys[api_key] = entry
            base["auth"]["api_keys"] = normalized_api_keys

    return base


class FileKeyring:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def ensure_exists(self) -> None:
        if self.path.exists():
            return
        with self._lock:
            if self.path.exists():
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write(_default_document())

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _default_document()
        payload = self.path.read_text(encoding="utf-8")
        if not payload.strip():
            return _default_document()
        parsed = json.loads(payload)
        return _normalize_document(parsed)

    def _write(self, document: dict[str, Any]) -> None:
        serialized = json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        tmp_path.write_text(serialized, encoding="utf-8")
        tmp_path.replace(self.path)

    def save(self, document: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize_document(document)
        normalized["updated_at"] = _utc_now_iso()
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write(normalized)
        return normalized

    def mtime_ns(self) -> int | None:
        if not self.path.exists():
            return None
        return self.path.stat().st_mtime_ns

    def status(self) -> dict[str, Any]:
        document = self.load()
        signing_status: dict[str, Any] = {}
        for purpose in sorted(SIGNING_PURPOSES):
            section = dict(document["signing"][purpose])
            keys = dict(section.get("keys", {}))
            enabled_key_ids = [
                key_id for key_id, config in keys.items() if bool(dict(config).get("enabled", True))
            ]
            signing_status[purpose] = {
                "active_key_id": section.get("active_key_id"),
                "total_keys": len(keys),
                "enabled_keys": len(enabled_key_ids),
                "enabled_key_ids": enabled_key_ids,
            }

        api_keys = dict(document["auth"].get("api_keys", {}))
        enabled_api_keys = [
            key for key, cfg in api_keys.items() if bool(dict(cfg).get("enabled", True))
        ]
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "schema_version": document.get("schema_version"),
            "updated_at": document.get("updated_at"),
            "signing": signing_status,
            "auth": {
                "total_api_keys": len(api_keys),
                "enabled_api_keys": len(enabled_api_keys),
            },
        }

    def get_signing_material(
        self,
        *,
        purpose: str,
        fallback_secret: str | None = None,
    ) -> tuple[str | None, tuple[str, ...]]:
        normalized_purpose = purpose.strip().lower()
        if normalized_purpose not in SIGNING_PURPOSES:
            raise ValueError(f"unsupported signing purpose '{purpose}'")

        document = self.load()
        section = dict(document["signing"][normalized_purpose])
        keys = dict(section.get("keys", {}))

        enabled_secrets: list[str] = []
        active_secret: str | None = None
        for key_id, config in keys.items():
            cfg = dict(config)
            if not bool(cfg.get("enabled", True)):
                continue
            secret = str(cfg.get("secret", "")).strip()
            if not secret:
                continue
            enabled_secrets.append(secret)
            if key_id == section.get("active_key_id"):
                active_secret = secret

        if active_secret is None and enabled_secrets:
            active_secret = enabled_secrets[0]
        if active_secret is None:
            active_secret = fallback_secret.strip() if fallback_secret else None
        if fallback_secret:
            enabled_secrets.append(fallback_secret)
        if active_secret:
            enabled_secrets.append(active_secret)

        return active_secret, _dedupe_non_empty(enabled_secrets)

    def get_auth_raw_policies(self) -> dict[str, Any]:
        document = self.load()
        return dict(document["auth"].get("api_keys", {}))

    def rotate_signing_key(
        self,
        *,
        purpose: str,
        secret: str | None = None,
        key_id: str | None = None,
        disable_previous: bool = False,
    ) -> dict[str, Any]:
        normalized_purpose = purpose.strip().lower()
        if normalized_purpose not in SIGNING_PURPOSES:
            raise ValueError(f"unsupported signing purpose '{purpose}'")

        generated_secret = (secret or "").strip() or secrets.token_urlsafe(32)
        generated_key_id = (key_id or "").strip()
        if not generated_key_id:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            generated_key_id = f"{normalized_purpose}-{timestamp}-{secrets.token_hex(4)}"

        document = self.load()
        section = dict(document["signing"][normalized_purpose])
        keys = dict(section.get("keys", {}))
        now = _utc_now_iso()

        previous_key_ids = list(keys.keys())
        for previous_id in previous_key_ids:
            if disable_previous:
                previous = dict(keys[previous_id])
                previous["enabled"] = False
                keys[previous_id] = previous

        existing = dict(keys.get(generated_key_id, {}))
        created_at = str(existing.get("created_at", now))
        next_entry: dict[str, Any] = {
            "secret": generated_secret,
            "enabled": True,
            "created_at": created_at,
        }
        if existing.get("description"):
            next_entry["description"] = str(existing["description"])
        keys[generated_key_id] = next_entry
        section["active_key_id"] = generated_key_id
        section["keys"] = keys
        document["signing"][normalized_purpose] = section
        saved = self.save(document)
        enabled_key_ids = [
            candidate_id
            for candidate_id, cfg in dict(saved["signing"][normalized_purpose]["keys"]).items()
            if bool(dict(cfg).get("enabled", True))
        ]
        return {
            "purpose": normalized_purpose,
            "active_key_id": generated_key_id,
            "secret": generated_secret,
            "disable_previous": disable_previous,
            "enabled_key_ids": enabled_key_ids,
            "updated_at": saved["updated_at"],
        }

    def upsert_api_key(
        self,
        *,
        api_key: str,
        namespaces: list[str],
        scopes: list[str],
        enabled: bool = True,
        label: str | None = None,
    ) -> dict[str, Any]:
        resolved_api_key = api_key.strip()
        if not resolved_api_key:
            raise ValueError("api_key is required")

        resolved_namespaces = [item.strip() for item in namespaces if item.strip()]
        if not resolved_namespaces:
            resolved_namespaces = ["default"]
        resolved_scopes = [item.strip() for item in scopes if item.strip()]

        document = self.load()
        api_keys = dict(document["auth"].get("api_keys", {}))
        existing = dict(api_keys.get(resolved_api_key, {}))
        created_at = str(existing.get("created_at", _utc_now_iso()))
        entry: dict[str, Any] = {
            "namespaces": resolved_namespaces,
            "scopes": resolved_scopes,
            "enabled": bool(enabled),
            "created_at": created_at,
        }
        if label and label.strip():
            entry["label"] = label.strip()
        api_keys[resolved_api_key] = entry
        document["auth"]["api_keys"] = api_keys
        saved = self.save(document)
        return {
            "api_key": resolved_api_key,
            "enabled": entry["enabled"],
            "namespaces": entry["namespaces"],
            "scopes": entry["scopes"],
            "label": entry.get("label"),
            "updated_at": saved["updated_at"],
        }

    def disable_api_key(self, *, api_key: str) -> dict[str, Any]:
        resolved_api_key = api_key.strip()
        if not resolved_api_key:
            raise ValueError("api_key is required")

        document = self.load()
        api_keys = dict(document["auth"].get("api_keys", {}))
        if resolved_api_key not in api_keys:
            raise ValueError(f"api_key '{resolved_api_key}' not found")

        entry = dict(api_keys[resolved_api_key])
        entry["enabled"] = False
        api_keys[resolved_api_key] = entry
        document["auth"]["api_keys"] = api_keys
        saved = self.save(document)
        return {
            "api_key": resolved_api_key,
            "enabled": False,
            "updated_at": saved["updated_at"],
        }

    def list_auth_presets(self) -> list[dict[str, Any]]:
        return list_auth_presets()

    def apply_auth_preset(
        self,
        *,
        preset: str,
        api_key: str,
        namespaces: list[str] | None = None,
        enabled: bool = True,
        label: str | None = None,
    ) -> dict[str, Any]:
        resolved_namespaces, resolved_scopes = resolve_auth_preset(
            preset=preset,
            namespaces=namespaces,
        )
        updated = self.upsert_api_key(
            api_key=api_key,
            namespaces=resolved_namespaces,
            scopes=resolved_scopes,
            enabled=enabled,
            label=label,
        )
        updated["preset"] = preset.strip().lower()
        return updated
