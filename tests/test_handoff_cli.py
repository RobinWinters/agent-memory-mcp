from __future__ import annotations

import json
from pathlib import Path

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import HashEmbedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.handoff_cli import main as handoff_main
from agent_memory_mcp.handoff_schema import HANDOFF_SCHEMA_ID
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.vector_store import LocalMemoryVectorStore


def make_service(db_path: Path) -> MemoryPolicyService:
    db = Database(str(db_path))
    return MemoryPolicyService(
        db=db,
        embedder=HashEmbedder(),
        evaluator=PolicyEvaluator(pass_threshold=0.7),
        vector_store=LocalMemoryVectorStore(db=db),
        default_namespace="default",
        job_default_max_attempts=2,
        job_backoff_base_seconds=0.001,
        job_backoff_max_seconds=0.001,
        job_running_timeout_seconds=1.0,
    )


def seed_source(db_path: Path, namespace: str) -> None:
    svc = make_service(db_path)
    try:
        svc.append_event("s1", "user", "Need safe promotion gates.", namespace=namespace)
        svc.append_event("s1", "assistant", "Keep rollback paths and eval checks.", namespace=namespace)
        svc.distill_session("s1", namespace=namespace)

        proposal = svc.policy_propose(
            delta_md="""
            ## Gate Rules
            - Require eval before promotion.
            - Keep rollback support active.
            - Preserve regression checks.
            """,
            evidence_refs=["session:s1", "memory:1"],
            namespace=namespace,
        )
        eval_result = svc.policy_evaluate(proposal_id=str(proposal["proposal_id"]), namespace=namespace)
        assert eval_result["passed"] is True
        svc.policy_promote(proposal_id=str(proposal["proposal_id"]), namespace=namespace)
    finally:
        svc.db.close()


def test_handoff_cli_export_import_roundtrip(tmp_path: Path, monkeypatch) -> None:
    source_ns = "source-cli"
    target_ns = "target-cli"
    source_db = tmp_path / "source.db"
    target_db = tmp_path / "target.db"
    handoff_file = tmp_path / "handoff.json"
    prompt_file = tmp_path / "handoff.md"

    monkeypatch.setenv("AGENT_MEMORY_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("AGENT_MEMORY_VECTOR_BACKEND", "sqlite")
    monkeypatch.setenv("AGENT_MEMORY_POLICY_SIGNING_SECRET", "handoff-cli-secret")

    seed_source(source_db, namespace=source_ns)

    export_code = handoff_main(
        [
            "export",
            "--db",
            str(source_db),
            "--namespace",
            source_ns,
            "--include-events",
            "--sign",
            "--k",
            "5",
            "--output",
            str(handoff_file),
            "--prompt-output",
            str(prompt_file),
            "--pretty",
        ]
    )
    assert export_code == 0
    assert handoff_file.exists()
    assert prompt_file.exists()

    exported = json.loads(handoff_file.read_text(encoding="utf-8"))
    assert exported["schema"] == "agent-memory-handoff.v1"
    assert exported["namespace"] == source_ns
    assert exported["stats"]["memory_count"] >= 1
    assert "Agent Handoff Context" in prompt_file.read_text(encoding="utf-8")

    import_code = handoff_main(
        [
            "import",
            "--db",
            str(target_db),
            "--namespace",
            target_ns,
            "--input",
            str(handoff_file),
            "--import-policy",
            "--import-events",
            "--verify",
            "--pretty",
        ]
    )
    assert import_code == 0

    target = make_service(target_db)
    try:
        found = target.memory_search(query="rollback eval checks", k=3, namespace=target_ns)
        assert found
        assert all(item["namespace"] == target_ns for item in found)

        policy = target.policy_get(namespace=target_ns)
        assert "Require eval before promotion" in policy["content_md"]

        events = target.db.list_events(namespace=target_ns, session_id="s1")
        assert len(events) >= 2
    finally:
        target.db.close()


def test_handoff_cli_schema_command(tmp_path: Path) -> None:
    schema_path = tmp_path / "handoff.schema.json"
    code = handoff_main(["schema", "--output", str(schema_path), "--pretty"])
    assert code == 0
    assert schema_path.exists()

    payload = json.loads(schema_path.read_text(encoding="utf-8"))
    assert payload["$id"] == HANDOFF_SCHEMA_ID
    assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_handoff_cli_verify_rejects_tampered_payload(tmp_path: Path, monkeypatch) -> None:
    source_ns = "source-cli"
    target_ns = "target-cli"
    source_db = tmp_path / "source.db"
    target_db = tmp_path / "target.db"
    handoff_file = tmp_path / "handoff.json"

    monkeypatch.setenv("AGENT_MEMORY_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("AGENT_MEMORY_VECTOR_BACKEND", "sqlite")
    monkeypatch.setenv("AGENT_MEMORY_POLICY_SIGNING_SECRET", "handoff-cli-secret")

    seed_source(source_db, namespace=source_ns)

    export_code = handoff_main(
        [
            "export",
            "--db",
            str(source_db),
            "--namespace",
            source_ns,
            "--sign",
            "--output",
            str(handoff_file),
        ]
    )
    assert export_code == 0

    payload = json.loads(handoff_file.read_text(encoding="utf-8"))
    payload["memories"][0]["content"] = "tampered"
    handoff_file.write_text(json.dumps(payload), encoding="utf-8")

    import_code = handoff_main(
        [
            "import",
            "--db",
            str(target_db),
            "--namespace",
            target_ns,
            "--input",
            str(handoff_file),
            "--verify",
        ]
    )
    assert import_code == 1
