from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import HashEmbedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.handoff_schema import HANDOFF_SCHEMA_ID, get_handoff_json_schema, validate_handoff_payload
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.vector_store import LocalMemoryVectorStore


def make_service(db_path: Path) -> MemoryPolicyService:
    db = Database(str(db_path))
    return MemoryPolicyService(
        db=db,
        embedder=HashEmbedder(dimensions=64),
        evaluator=PolicyEvaluator(pass_threshold=0.7),
        vector_store=LocalMemoryVectorStore(db=db),
        default_namespace="default",
    )


def test_handoff_schema_resource_loads() -> None:
    schema = get_handoff_json_schema()
    assert schema["$id"] == HANDOFF_SCHEMA_ID
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"


def test_export_payload_matches_schema(tmp_path: Path) -> None:
    svc = make_service(tmp_path / "source.db")
    svc.append_event("s1", "user", "Export me.", namespace="alpha")
    svc.append_event("s1", "assistant", "With policy and events.", namespace="alpha")
    svc.distill_session("s1", namespace="alpha")

    payload = svc.memory_handoff_export(
        include_policy=True,
        include_events=True,
        namespace="alpha",
    )
    validate_handoff_payload(payload)


def test_validator_rejects_missing_required_fields() -> None:
    bad_payload = {"schema": HANDOFF_SCHEMA_ID, "namespace": "alpha"}
    with pytest.raises(ValueError, match="schema validation failed"):
        validate_handoff_payload(bad_payload)
