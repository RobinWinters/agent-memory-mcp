from __future__ import annotations

from agent_memory_mcp.factory import build_service
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.settings import Settings


def load_settings_from_env() -> Settings:
    return Settings.from_env()


def build_service_from_settings(settings: Settings) -> MemoryPolicyService:
    return build_service(settings=settings)


def build_service_from_env() -> tuple[Settings, MemoryPolicyService]:
    settings = load_settings_from_env()
    service = build_service_from_settings(settings=settings)
    return settings, service
