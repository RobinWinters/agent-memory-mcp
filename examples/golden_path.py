#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Support running directly from a source checkout without package install.
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agent_memory_mcp.runtime_bootstrap import build_service_from_env


def _run_pending_until_done(
    service: Any,
    *,
    namespace: str,
    job_id: int,
    max_cycles: int = 10,
) -> dict[str, Any]:
    for _ in range(max_cycles):
        service.jobs_run_pending(limit=10, namespace=namespace)
        result = service.jobs_result(job_id=job_id, namespace=namespace)
        if result["status"] in {"succeeded", "dead"}:
            return result
    return service.jobs_result(job_id=job_id, namespace=namespace)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local golden-path memory/policy workflow.")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--session-id", default="golden-demo")
    parser.add_argument("--query", default="eval gates rollback safety")
    parser.add_argument("--max-lines", type=int, default=6)
    args = parser.parse_args()

    _, service = build_service_from_env()

    service.append_event(
        session_id=args.session_id,
        role="user",
        content="Need stricter evaluation gates before policy promotion.",
        namespace=args.namespace,
    )
    service.append_event(
        session_id=args.session_id,
        role="assistant",
        content="Add rollback checks, retries, and observability before promote.",
        namespace=args.namespace,
    )

    queued_distill = service.distill_session(
        session_id=args.session_id,
        max_lines=args.max_lines,
        namespace=args.namespace,
        async_mode=True,
    )
    distill_result = _run_pending_until_done(
        service,
        namespace=args.namespace,
        job_id=int(queued_distill["job_id"]),
    )

    search_results = service.memory_search(
        query=args.query,
        k=3,
        namespace=args.namespace,
    )

    proposal = service.policy_propose(
        delta_md=(
            "## Guardrails\n"
            "- Require eval before promotion.\n"
            "- Require rollback path.\n"
            "- Keep regression threshold checks."
        ),
        evidence_refs=[f"session:{args.session_id}", "memory:1"],
        namespace=args.namespace,
    )

    queued_eval = service.policy_evaluate(
        proposal_id=str(proposal["proposal_id"]),
        namespace=args.namespace,
        async_mode=True,
    )
    eval_result = _run_pending_until_done(
        service,
        namespace=args.namespace,
        job_id=int(queued_eval["job_id"]),
    )

    promoted: dict[str, Any] | None = None
    eval_payload = eval_result.get("result") or {}
    if eval_result["status"] == "succeeded" and eval_payload.get("passed") is True:
        promoted = service.policy_promote(
            proposal_id=str(proposal["proposal_id"]),
            namespace=args.namespace,
        )

    summary = {
        "namespace": args.namespace,
        "session_id": args.session_id,
        "distill_job": distill_result,
        "search_top_k": len(search_results),
        "proposal_id": proposal["proposal_id"],
        "eval_job": eval_result,
        "promoted": promoted,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
