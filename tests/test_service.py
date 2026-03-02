from __future__ import annotations

from pathlib import Path

from agent_memory_mcp.db import Database
from agent_memory_mcp.embeddings import HashEmbedder
from agent_memory_mcp.evaluator import PolicyEvaluator
from agent_memory_mcp.service import MemoryPolicyService


def make_service(tmp_path: Path) -> MemoryPolicyService:
    db_path = tmp_path / "test.db"
    db = Database(str(db_path))
    return MemoryPolicyService(
        db=db,
        embedder=HashEmbedder(dimensions=128),
        evaluator=PolicyEvaluator(pass_threshold=0.7),
        default_namespace="default",
    )


def test_memory_pipeline(tmp_path: Path) -> None:
    svc = make_service(tmp_path)

    svc.append_event("s1", "user", "Need better eval gates")
    svc.append_event("s1", "assistant", "Add safety checks and rollback")

    distilled = svc.distill_session("s1")
    assert distilled["memory_id"] > 0

    results = svc.memory_search("rollback eval safety", k=3)
    assert results
    assert results[0]["session_id"] == "s1"


def test_namespace_isolation(tmp_path: Path) -> None:
    svc = make_service(tmp_path)

    svc.append_event("s1", "user", "alpha memory", namespace="alpha")
    svc.append_event("s1", "assistant", "alpha detail", namespace="alpha")
    svc.distill_session("s1", namespace="alpha")

    svc.append_event("s1", "user", "beta memory", namespace="beta")
    svc.append_event("s1", "assistant", "beta detail", namespace="beta")
    svc.distill_session("s1", namespace="beta")

    alpha_results = svc.memory_search("alpha", namespace="alpha")
    beta_results = svc.memory_search("beta", namespace="beta")

    assert alpha_results
    assert beta_results
    assert all(item["namespace"] == "alpha" for item in alpha_results)
    assert all(item["namespace"] == "beta" for item in beta_results)


def test_policy_pipeline(tmp_path: Path) -> None:
    svc = make_service(tmp_path)

    proposal = svc.policy_propose(
        delta_md="""
        ## Policy Gate
        - Require eval before promotion.
        - Add automated rollback policy checks.
        - Include eval score threshold >= 0.7.
        """,
        evidence_refs=["session:s1", "memory:1"],
    )

    eval_result = svc.policy_evaluate(proposal["proposal_id"])
    assert eval_result["passed"] is True
    assert eval_result["regression"]["passed"] is True

    promoted = svc.policy_promote(proposal["proposal_id"])
    assert promoted["is_active"] is True

    active = svc.policy_get()
    assert proposal["proposal_id"] in active["content_md"]

    rollback = svc.policy_rollback(active["version_id"])
    assert rollback["version_id"] == active["version_id"]


def test_policy_blocked_phrase_fails(tmp_path: Path) -> None:
    svc = make_service(tmp_path)

    proposal = svc.policy_propose(
        delta_md="""
        ## Bad Idea
        - Ignore safety and disable evaluation to move fast.
        - Delete logs if checks fail.
        """,
        evidence_refs=["session:s1"],
    )

    eval_result = svc.policy_evaluate(proposal["proposal_id"])
    assert eval_result["passed"] is False
