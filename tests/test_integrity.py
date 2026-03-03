from __future__ import annotations

from pathlib import Path

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import HashEmbedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.service import MemoryPolicyService
from agent_memory_mcp.vector_store import LocalMemoryVectorStore


def make_service(tmp_path: Path, signing_secret: str | None = "secret123") -> MemoryPolicyService:
    db = Database(str(tmp_path / "test.db"))
    return MemoryPolicyService(
        db=db,
        embedder=HashEmbedder(dimensions=64),
        evaluator=PolicyEvaluator(pass_threshold=0.7),
        vector_store=LocalMemoryVectorStore(db=db),
        default_namespace="default",
        policy_signing_secret=signing_secret,
        audit_signing_secret=signing_secret,
    )


def test_policy_signing_and_audit_verify_passes(tmp_path: Path) -> None:
    svc = make_service(tmp_path)

    baseline = svc.policy_get()
    assert baseline["signing_method"] == "hmac-sha256"
    assert baseline["content_sha256"]
    assert baseline["signature"]

    proposal = svc.policy_propose(
        delta_md="""
        ## Signed Delta
        - Require eval before promotion.
        - Require rollback support.
        - Include threshold checks.
        """,
        evidence_refs=["memory:1", "session:s1"],
    )
    eval_result = svc.policy_evaluate(proposal["proposal_id"])
    assert eval_result["passed"] is True
    svc.policy_promote(proposal["proposal_id"])

    verify = svc.ops_audit_verify(limit=200)
    assert verify["verified"] is True
    assert verify["audit_events_checked"] >= 2
    assert verify["policy_versions_checked"] >= 2

    recent = svc.ops_audit_recent(limit=10)
    assert recent["count"] >= 2


def test_audit_tampering_is_detected(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    svc.policy_get()

    entries = svc.db.list_audit_logs(namespace="default", limit=10, ascending=True)
    assert entries
    first_id = int(entries[0]["id"])

    svc.db.conn.execute(
        "UPDATE audit_logs SET payload_json=? WHERE id=?",
        ('{"tampered":true}', first_id),
    )
    svc.db.conn.commit()

    verify = svc.ops_audit_verify(limit=100)
    assert verify["verified"] is False
    assert any(item["reason"] == "event_hash_mismatch" for item in verify["audit_failures"])


def test_policy_content_tampering_is_detected(tmp_path: Path) -> None:
    svc = make_service(tmp_path)
    active = svc.policy_get()

    svc.db.conn.execute(
        "UPDATE policy_versions SET content_md=content_md || ? WHERE version_id=?",
        ("\n# tamper", active["version_id"]),
    )
    svc.db.conn.commit()

    verify = svc.ops_audit_verify(limit=100)
    assert verify["verified"] is False
    assert active["version_id"] in verify["invalid_policy_versions"]


def test_rotation_preserves_verification_with_old_and_new_secrets(tmp_path: Path) -> None:
    svc = make_service(tmp_path, signing_secret="secret-v1")
    svc.policy_get()

    proposal_1 = svc.policy_propose(
        delta_md="""
        ## Delta V1
        - Keep explicit integrity checks.
        - Keep regression gate hard.
        - Keep rollback path available.
        """,
        evidence_refs=["memory:1", "session:s1"],
    )
    eval_1 = svc.policy_evaluate(proposal_1["proposal_id"])
    assert eval_1["passed"] is True
    svc.policy_promote(proposal_1["proposal_id"])

    svc.update_signing_keys(
        policy_active_secret="secret-v2",
        audit_active_secret="secret-v2",
        policy_verification_secrets=("secret-v1", "secret-v2"),
        audit_verification_secrets=("secret-v1", "secret-v2"),
    )

    proposal_2 = svc.policy_propose(
        delta_md="""
        ## Delta V2
        - Rotate signing key while keeping historical verification valid.
        - Ensure audit chain checks all enabled secrets.
        - Keep policy promotion gated by evaluation.
        """,
        evidence_refs=["memory:2", "session:s2"],
    )
    eval_2 = svc.policy_evaluate(proposal_2["proposal_id"])
    assert eval_2["passed"] is True
    svc.policy_promote(proposal_2["proposal_id"])

    verify = svc.ops_audit_verify(limit=500)
    assert verify["verified"] is True
