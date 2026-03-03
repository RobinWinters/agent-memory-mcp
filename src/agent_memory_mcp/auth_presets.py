from __future__ import annotations

from typing import Any

AUTH_PRESET_SCOPES: dict[str, tuple[str, ...]] = {
    "admin": ("*",),
    "writer": (
        "memory:read",
        "memory:write",
        "policy:read",
        "policy:propose",
        "policy:evaluate",
        "jobs:submit",
        "jobs:run",
        "jobs:read",
    ),
    "reader": (
        "memory:read",
        "policy:read",
        "jobs:read",
    ),
}


def list_auth_presets() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name in sorted(AUTH_PRESET_SCOPES.keys()):
        scopes = list(AUTH_PRESET_SCOPES[name])
        items.append(
            {
                "name": name,
                "scope_count": len(scopes),
                "scopes": scopes,
            }
        )
    return items


def resolve_auth_preset(
    *,
    preset: str,
    namespaces: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    normalized = preset.strip().lower()
    scopes = AUTH_PRESET_SCOPES.get(normalized)
    if scopes is None:
        supported = ", ".join(sorted(AUTH_PRESET_SCOPES.keys()))
        raise ValueError(f"unsupported preset '{preset}'; expected one of: {supported}")

    if namespaces is None:
        if normalized == "admin":
            resolved_namespaces = ["*"]
        else:
            resolved_namespaces = ["default"]
    else:
        cleaned = [item.strip() for item in namespaces if item and item.strip()]
        resolved_namespaces = cleaned or (["*"] if normalized == "admin" else ["default"])

    return resolved_namespaces, list(scopes)
