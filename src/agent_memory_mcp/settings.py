from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str
    default_namespace: str
    embedding_backend: str
    openai_api_key: str | None
    openai_embedding_model: str
    policy_pass_threshold: float
    auth_mode: str
    auth_api_keys_json: str | None
    auth_api_keys_file: str | None
    vector_backend: str
    qdrant_url: str
    qdrant_api_key: str | None
    qdrant_collection: str
    qdrant_timeout_seconds: float
    qdrant_auto_create_collection: bool

    @classmethod
    def from_env(cls) -> "Settings":
        def parse_bool(value: str | None, *, default: bool) -> bool:
            if value is None:
                return default
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            return default

        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        db_path = os.getenv("AGENT_MEMORY_DB", os.path.join(root, "data", "agent_memory.db"))
        default_namespace = os.getenv("AGENT_MEMORY_NAMESPACE", "default").strip() or "default"
        embedding_backend = os.getenv("AGENT_MEMORY_EMBEDDING_BACKEND", "hash").strip().lower()
        vector_backend = os.getenv("AGENT_MEMORY_VECTOR_BACKEND", "sqlite").strip().lower()
        openai_api_key = os.getenv("OPENAI_API_KEY")
        openai_embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        threshold_raw = os.getenv("AGENT_MEMORY_POLICY_PASS_THRESHOLD", "0.75")
        auth_mode = os.getenv("AGENT_MEMORY_AUTH_MODE", "off").strip().lower()
        auth_api_keys_json = os.getenv("AGENT_MEMORY_API_KEYS_JSON")
        auth_api_keys_file = os.getenv("AGENT_MEMORY_API_KEYS_FILE")
        qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        qdrant_api_key = os.getenv("QDRANT_API_KEY")
        qdrant_collection = os.getenv("QDRANT_COLLECTION", "agent_memory")
        qdrant_timeout_raw = os.getenv("QDRANT_TIMEOUT_SECONDS", "10")
        qdrant_auto_create = parse_bool(os.getenv("QDRANT_AUTO_CREATE_COLLECTION"), default=True)

        try:
            threshold = float(threshold_raw)
        except ValueError:
            threshold = 0.75
        try:
            qdrant_timeout_seconds = float(qdrant_timeout_raw)
        except ValueError:
            qdrant_timeout_seconds = 10.0

        threshold = max(0.0, min(1.0, threshold))
        qdrant_timeout_seconds = max(0.5, qdrant_timeout_seconds)

        return cls(
            db_path=db_path,
            default_namespace=default_namespace,
            embedding_backend=embedding_backend,
            openai_api_key=openai_api_key,
            openai_embedding_model=openai_embedding_model,
            policy_pass_threshold=threshold,
            auth_mode=auth_mode,
            auth_api_keys_json=auth_api_keys_json,
            auth_api_keys_file=auth_api_keys_file,
            vector_backend=vector_backend,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            qdrant_collection=qdrant_collection,
            qdrant_timeout_seconds=qdrant_timeout_seconds,
            qdrant_auto_create_collection=qdrant_auto_create,
        )
