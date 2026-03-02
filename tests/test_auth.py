from __future__ import annotations

import pytest

from agent_memory_mcp.auth import Authorizer


def test_auth_off_allows_without_key() -> None:
    auth = Authorizer.from_sources(
        mode="off",
        default_namespace="default",
        keys_json=None,
        keys_file=None,
    )

    ns = auth.authorize(api_key=None, namespace=None, scope="memory:read")
    assert ns == "default"


def test_api_key_mode_requires_key() -> None:
    auth = Authorizer.from_sources(
        mode="api_key",
        default_namespace="default",
        keys_json='{"k1":{"namespaces":["default"],"scopes":["memory:read"]}}',
        keys_file=None,
    )

    with pytest.raises(PermissionError):
        auth.authorize(api_key=None, namespace="default", scope="memory:read")


def test_scope_and_namespace_enforced() -> None:
    auth = Authorizer.from_sources(
        mode="api_key",
        default_namespace="default",
        keys_json='{"k1":{"namespaces":["tenant-a"],"scopes":["memory:read"]}}',
        keys_file=None,
    )

    with pytest.raises(PermissionError):
        auth.authorize(api_key="k1", namespace="tenant-b", scope="memory:read")

    with pytest.raises(PermissionError):
        auth.authorize(api_key="k1", namespace="tenant-a", scope="memory:write")


def test_wildcard_scope_family_and_namespace() -> None:
    auth = Authorizer.from_sources(
        mode="api_key",
        default_namespace="default",
        keys_json='{"admin":{"namespaces":["*"],"scopes":["policy:*","memory:read"]}}',
        keys_file=None,
    )

    ns = auth.authorize(api_key="admin", namespace="tenant-z", scope="policy:promote")
    assert ns == "tenant-z"

    ns2 = auth.authorize(api_key="admin", namespace="tenant-z", scope="memory:read")
    assert ns2 == "tenant-z"

    with pytest.raises(PermissionError):
        auth.authorize(api_key="admin", namespace="tenant-z", scope="memory:write")
