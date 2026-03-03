from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory_mcp.app_context import AppContext
from agent_memory_mcp.keyring import FileKeyring


def test_app_context_authorize_off_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_MEMORY_DB", str(tmp_path / "ctx-off.db"))
    monkeypatch.setenv("AGENT_MEMORY_AUTH_MODE", "off")

    ctx = AppContext()
    ns = ctx.authorize(namespace=None, scope="memory:read", api_key=None)
    assert ns == "default"

    service = ctx.get_service()
    assert service.default_namespace == "default"


def test_app_context_uses_keyring_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    keyring_path = tmp_path / "keyring.json"
    keyring = FileKeyring(str(keyring_path))
    keyring.ensure_exists()
    keyring.upsert_api_key(
        api_key="ctx-key",
        namespaces=["tenant-a"],
        scopes=["memory:read"],
        enabled=True,
    )

    monkeypatch.setenv("AGENT_MEMORY_DB", str(tmp_path / "ctx-keyring.db"))
    monkeypatch.setenv("AGENT_MEMORY_AUTH_MODE", "api_key")
    monkeypatch.setenv("AGENT_MEMORY_KEYRING_FILE", str(keyring_path))

    ctx = AppContext()
    allowed_ns = ctx.authorize(namespace="tenant-a", scope="memory:read", api_key="ctx-key")
    assert allowed_ns == "tenant-a"

    with pytest.raises(PermissionError):
        ctx.authorize(namespace="tenant-a", scope="memory:write", api_key="ctx-key")
