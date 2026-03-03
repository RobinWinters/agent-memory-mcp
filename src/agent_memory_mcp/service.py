from __future__ import annotations

from dataclasses import dataclass

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import Embedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.service_base import ServiceBaseMixin
from agent_memory_mcp.service_constants import BASELINE_POLICY, SUPPORTED_JOB_TYPES
from agent_memory_mcp.service_handoff import ServiceHandoffMixin
from agent_memory_mcp.service_jobs import ServiceJobsMixin
from agent_memory_mcp.service_memory import ServiceMemoryMixin
from agent_memory_mcp.service_ops import ServiceOpsMixin
from agent_memory_mcp.service_policy import ServicePolicyMixin
from agent_memory_mcp.vector_store import MemoryVectorStore

__all__ = ["MemoryPolicyService", "BASELINE_POLICY", "SUPPORTED_JOB_TYPES"]


@dataclass
class MemoryPolicyService(
    ServiceBaseMixin,
    ServiceMemoryMixin,
    ServicePolicyMixin,
    ServiceHandoffMixin,
    ServiceOpsMixin,
    ServiceJobsMixin,
):
    db: Database
    embedder: Embedder
    evaluator: PolicyEvaluator
    vector_store: MemoryVectorStore
    default_namespace: str = "default"
    job_default_max_attempts: int = 3
    job_backoff_base_seconds: float = 2.0
    job_backoff_max_seconds: float = 300.0
    job_running_timeout_seconds: float = 300.0
    policy_signing_secret: str | None = None
    audit_signing_secret: str | None = None
    policy_verification_secrets: tuple[str, ...] = ()
    audit_verification_secrets: tuple[str, ...] = ()
