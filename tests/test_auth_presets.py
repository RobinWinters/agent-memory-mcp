from __future__ import annotations

import pytest

from agent_memory_mcp.auth_presets import list_auth_presets, resolve_auth_preset


def test_list_auth_presets_contains_expected_names() -> None:
    presets = list_auth_presets()
    names = {item["name"] for item in presets}
    assert names == {"admin", "writer", "reader"}


def test_resolve_admin_defaults_to_global_namespace() -> None:
    namespaces, scopes = resolve_auth_preset(preset="admin")
    assert namespaces == ["*"]
    assert scopes == ["*"]


def test_resolve_writer_defaults_to_default_namespace() -> None:
    namespaces, scopes = resolve_auth_preset(preset="writer")
    assert namespaces == ["default"]
    assert "memory:write" in scopes
    assert "jobs:run" in scopes


def test_resolve_invalid_preset_raises() -> None:
    with pytest.raises(ValueError):
        resolve_auth_preset(preset="unknown-role")
