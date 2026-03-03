from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory_mcp.auth import Authorizer
from agent_memory_mcp.keyring import FileKeyring


def test_signing_rotation_and_material_resolution(tmp_path: Path) -> None:
    keyring = FileKeyring(str(tmp_path / "keyring.json"))
    keyring.ensure_exists()

    first = keyring.rotate_signing_key(
        purpose="policy",
        secret="secret-v1",
        key_id="policy-v1",
    )
    assert first["active_key_id"] == "policy-v1"

    second = keyring.rotate_signing_key(
        purpose="policy",
        secret="secret-v2",
        key_id="policy-v2",
        disable_previous=False,
    )
    assert second["active_key_id"] == "policy-v2"

    active, all_secrets = keyring.get_signing_material(purpose="policy")
    assert active == "secret-v2"
    assert "secret-v1" in all_secrets
    assert "secret-v2" in all_secrets

    third = keyring.rotate_signing_key(
        purpose="policy",
        secret="secret-v3",
        key_id="policy-v3",
        disable_previous=True,
    )
    assert third["active_key_id"] == "policy-v3"

    active_after, all_after = keyring.get_signing_material(purpose="policy")
    assert active_after == "secret-v3"
    assert all_after == ("secret-v3",)


def test_auth_upsert_and_disable(tmp_path: Path) -> None:
    keyring = FileKeyring(str(tmp_path / "keyring.json"))
    keyring.ensure_exists()

    keyring.upsert_api_key(
        api_key="alpha-key",
        namespaces=["tenant-a"],
        scopes=["memory:read", "security:read"],
        enabled=True,
        label="alpha",
    )
    keyring.upsert_api_key(
        api_key="beta-key",
        namespaces=["tenant-a"],
        scopes=["memory:read"],
        enabled=True,
    )
    auth = Authorizer.from_raw_policies(
        mode="api_key",
        default_namespace="default",
        raw_policies={"auth": {"api_keys": keyring.get_auth_raw_policies()}},
    )

    ns = auth.authorize(api_key="alpha-key", namespace="tenant-a", scope="security:read")
    assert ns == "tenant-a"

    keyring.disable_api_key(api_key="alpha-key")
    auth_after = Authorizer.from_raw_policies(
        mode="api_key",
        default_namespace="default",
        raw_policies={"auth": {"api_keys": keyring.get_auth_raw_policies()}},
    )
    with pytest.raises(PermissionError):
        auth_after.authorize(api_key="alpha-key", namespace="tenant-a", scope="memory:read")


def test_keyring_status_counts(tmp_path: Path) -> None:
    keyring = FileKeyring(str(tmp_path / "keyring.json"))
    keyring.ensure_exists()
    keyring.rotate_signing_key(purpose="policy", secret="p1", key_id="policy-v1")
    keyring.rotate_signing_key(purpose="audit", secret="a1", key_id="audit-v1")
    keyring.upsert_api_key(
        api_key="active-key",
        namespaces=["default"],
        scopes=["memory:*"],
        enabled=True,
    )
    keyring.upsert_api_key(
        api_key="disabled-key",
        namespaces=["default"],
        scopes=["memory:*"],
        enabled=False,
    )

    status = keyring.status()
    assert status["exists"] is True
    assert status["signing"]["policy"]["enabled_keys"] == 1
    assert status["signing"]["audit"]["enabled_keys"] == 1
    assert status["auth"]["total_api_keys"] == 2
    assert status["auth"]["enabled_api_keys"] == 1


def test_apply_auth_preset_writer(tmp_path: Path) -> None:
    keyring = FileKeyring(str(tmp_path / "keyring.json"))
    keyring.ensure_exists()

    applied = keyring.apply_auth_preset(
        preset="writer",
        api_key="writer-key",
        namespaces=["tenant-a"],
        label="writer role",
    )
    assert applied["preset"] == "writer"
    assert applied["api_key"] == "writer-key"
    assert applied["namespaces"] == ["tenant-a"]
    assert "memory:write" in applied["scopes"]
    assert "policy:promote" not in applied["scopes"]
