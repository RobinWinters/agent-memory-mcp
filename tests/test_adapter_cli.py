from __future__ import annotations

import json
from pathlib import Path

from agent_memory_mcp.adapter_cli import main as adapter_main
from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import HashEmbedder
from agent_memory_mcp.evaluator import PolicyEvaluator
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
        svc.append_event("s1", "user", "Need continuity across tools.", namespace=namespace)
        svc.append_event("s1", "assistant", "Use signed handoff bundles.", namespace=namespace)
        svc.distill_session("s1", namespace=namespace)

        proposal = svc.policy_propose(
            delta_md="""
            ## Continuity Rules
            - Require eval before promotion.
            - Keep rollback support active.
            - Keep regression checks.
            """,
            evidence_refs=["session:s1", "memory:1"],
            namespace=namespace,
        )
        eval_result = svc.policy_evaluate(proposal_id=str(proposal["proposal_id"]), namespace=namespace)
        assert eval_result["passed"] is True
        svc.policy_promote(proposal_id=str(proposal["proposal_id"]), namespace=namespace)
    finally:
        svc.db.close()


def test_adapter_cli_cursor_roundtrip(tmp_path: Path, monkeypatch, capsys) -> None:
    source_ns = "source-adapter"
    target_ns = "target-adapter"
    source_db = tmp_path / "source.db"
    target_db = tmp_path / "target.db"
    handoff_file = tmp_path / "handoff.json"
    prompt_file = tmp_path / "context.md"

    monkeypatch.setenv("AGENT_MEMORY_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("AGENT_MEMORY_VECTOR_BACKEND", "sqlite")
    monkeypatch.setenv("AGENT_MEMORY_POLICY_SIGNING_SECRET", "adapter-secret")

    seed_source(source_db, namespace=source_ns)

    end_code = adapter_main(
        [
            "cursor-end",
            "--db",
            str(source_db),
            "--namespace",
            source_ns,
            "--handoff-file",
            str(handoff_file),
            "--prompt-file",
            str(prompt_file),
            "--include-events",
            "--sign",
            "--pretty",
        ]
    )
    assert end_code == 0
    assert handoff_file.exists()
    assert prompt_file.exists()
    exported = json.loads(handoff_file.read_text(encoding="utf-8"))
    assert isinstance(exported.get("signature"), dict)
    end_output = capsys.readouterr().out
    assert '"action": "end"' in end_output

    start_code = adapter_main(
        [
            "cursor-start",
            "--db",
            str(target_db),
            "--namespace",
            target_ns,
            "--handoff-file",
            str(handoff_file),
            "--prompt-file",
            str(prompt_file),
            "--verify",
            "--import-policy",
            "--import-events",
            "--pretty",
        ]
    )
    assert start_code == 0
    start_output = capsys.readouterr().out
    assert '"action": "start"' in start_output
    assert '"handoff_loaded": true' in start_output
    assert "Agent Handoff Context" in prompt_file.read_text(encoding="utf-8")

    target = make_service(target_db)
    try:
        found = target.memory_search(query="signed continuity tools", k=3, namespace=target_ns)
        assert found
        policy = target.policy_get(namespace=target_ns)
        assert "Require eval before promotion" in policy["content_md"]
        events = target.db.list_events(namespace=target_ns, session_id="s1")
        assert len(events) >= 2
    finally:
        target.db.close()


def test_adapter_cli_cursor_start_missing_handoff(tmp_path: Path, monkeypatch, capsys) -> None:
    db_path = tmp_path / "target.db"
    handoff_file = tmp_path / "missing.json"
    prompt_file = tmp_path / "context.md"

    monkeypatch.setenv("AGENT_MEMORY_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("AGENT_MEMORY_VECTOR_BACKEND", "sqlite")

    code = adapter_main(
        [
            "cursor-start",
            "--db",
            str(db_path),
            "--namespace",
            "default",
            "--handoff-file",
            str(handoff_file),
            "--prompt-file",
            str(prompt_file),
            "--pretty",
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert '"handoff_loaded": false' in output
    assert prompt_file.exists()

    strict_code = adapter_main(
        [
            "cursor-start",
            "--db",
            str(db_path),
            "--namespace",
            "default",
            "--handoff-file",
            str(handoff_file),
            "--prompt-file",
            str(prompt_file),
            "--require-handoff",
        ]
    )
    assert strict_code == 1
    err = capsys.readouterr().err
    assert "handoff file not found" in err
