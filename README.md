# agent-memory-mcp

MCP server for agent memory + policy self-improvement workflows with namespace partitioning,
pluggable embeddings, and a structured policy evaluator.

## Current build status (v0.2.0)

Implemented now:

- Immutable event ingest and session distillation into memory notes.
- Namespace-aware data partitioning across all memory and policy entities.
- Pluggable embedding backend:
  - `hash` (default, offline deterministic)
  - `openai` (remote embeddings API)
- Structured policy evaluator with weighted checks and regression suite replay.
- Policy lifecycle with gating:
  - `propose` -> `evaluate` -> `promote` -> `rollback`

## MCP tools

- `memory.append(session_id, role, content, metadata?, namespace?)`
- `memory.distill(session_id, max_lines=6, namespace?)`
- `memory.search(query, k=5, namespace?)`
- `policy.get(active_version=true, namespace?)`
- `policy.propose(delta_md, evidence_refs=[], namespace?)`
- `policy.evaluate(proposal_id, namespace?)`
- `policy.promote(proposal_id, namespace?)`
- `policy.rollback(version_id, namespace?)`

## Environment

- `AGENT_MEMORY_DB` (default: `./data/agent_memory.db`)
- `AGENT_MEMORY_NAMESPACE` (default: `default`)
- `AGENT_MEMORY_EMBEDDING_BACKEND` (`hash` or `openai`, default: `hash`)
- `OPENAI_API_KEY` (required when backend is `openai`)
- `OPENAI_EMBEDDING_MODEL` (default: `text-embedding-3-small`)
- `AGENT_MEMORY_POLICY_PASS_THRESHOLD` (default: `0.75`)

## Quickstart

```bash
cd <repo-root>
python3 -m venv .venv
source .venv/bin/activate
pip install ".[dev]"
pytest -q
```

Run MCP server over stdio:

```bash
source .venv/bin/activate
agent-memory-mcp
```

## Local evaluator regression suite

Regression cases live in `evals/policy_regression_cases.json` and are replayed during
`policy.evaluate`.

## Concrete build plan from here

1. Add provider adapters for Qdrant/pgvector and switch memory search from full-scan to ANN.
2. Add authN/authZ for MCP tool calls (namespace ACLs + signed client identity).
3. Add asynchronous ingestion/distillation workers and background eval jobs.
4. Add replay benchmark harness with dataset snapshots and trend reporting.
5. Add policy artifact signing and tamper-evident audit logs.

## Publish / update GitHub

```bash
cd <repo-root>
git add .
git commit -m "Build out v0.2.0: namespaces, embeddings, evaluator"
git push
```
