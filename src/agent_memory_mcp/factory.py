from __future__ import annotations

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import build_embedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.settings import Settings
from agent_memory_mcp.vector_store import build_vector_store


def build_service(settings: Settings) -> MemoryPolicyService:
    db = Database(db_path=settings.db_path)
    embedder = build_embedder(
        backend=settings.embedding_backend,
        openai_api_key=settings.openai_api_key,
        openai_model=settings.openai_embedding_model,
    )
    vector_store = build_vector_store(
        backend=settings.vector_backend,
        db=db,
        qdrant_url=settings.qdrant_url,
        qdrant_collection=settings.qdrant_collection,
        qdrant_api_key=settings.qdrant_api_key,
        qdrant_timeout_seconds=settings.qdrant_timeout_seconds,
        qdrant_auto_create_collection=settings.qdrant_auto_create_collection,
    )
    evaluator = PolicyEvaluator(pass_threshold=settings.policy_pass_threshold)
    return MemoryPolicyService(
        db=db,
        embedder=embedder,
        evaluator=evaluator,
        vector_store=vector_store,
        default_namespace=settings.default_namespace,
        job_default_max_attempts=settings.job_default_max_attempts,
        job_backoff_base_seconds=settings.job_backoff_base_seconds,
        job_backoff_max_seconds=settings.job_backoff_max_seconds,
        job_running_timeout_seconds=settings.job_running_timeout_seconds,
    )
