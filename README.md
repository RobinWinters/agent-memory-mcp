# agent-memory-mcp

MCP server for agent memory + policy self-improvement workflows with namespace partitioning,
pluggable embeddings, structured policy evaluation, and API-key ACL authorization.

## Current build status (v0.3.0)

Implemented now:

- Immutable event ingest and session distillation into memory notes.
- Namespace-aware data partitioning across all memory and policy entities.
- Pluggable embedding backend:
  - `hash` (default, offline deterministic)
  - `openai` (remote embeddings API)
- Structured policy evaluator with weighted checks and regression suite replay.
- Policy lifecycle with gating:
  - `propose` -> `evaluate` -> `promote` -> `rollback`
- API-key auth and ACL scope enforcement at the MCP tool layer.

## MCP tools

- `memory.append(session_id, role, content, metadata?, namespace?, api_key?)`
- `memory.distill(session_id, max_lines=6, namespace?, api_key?)`
- `memory.search(query, k=5, namespace?, api_key?)`
- `policy.get(active_version=true, namespace?, api_key?)`
- `policy.propose(delta_md, evidence_refs=[], namespace?, api_key?)`
- `policy.evaluate(proposal_id, namespace?, api_key?)`
- `policy.promote(proposal_id, namespace?, api_key?)`
- `policy.rollback(version_id, namespace?, api_key?)`

## Environment

Core:
- `AGENT_MEMORY_DB` (default: `./data/agent_memory.db`)
- `AGENT_MEMORY_NAMESPACE` (default: `default`)
- `AGENT_MEMORY_EMBEDDING_BACKEND` (`hash` or `openai`, default: `hash`)
- `OPENAI_API_KEY` (required when backend is `openai`)
- `OPENAI_EMBEDDING_MODEL` (default: `text-embedding-3-small`)
- `AGENT_MEMORY_POLICY_PASS_THRESHOLD` (default: `0.75`)

Auth:
- `AGENT_MEMORY_AUTH_MODE` (`off` or `api_key`, default: `off`)
- `AGENT_MEMORY_API_KEYS_JSON` (inline key-policy JSON)
- `AGENT_MEMORY_API_KEYS_FILE` (path to JSON file, preferred in production)

`AGENT_MEMORY_API_KEYS_JSON` / file format:

```json
{
  "key_admin": {
    "namespaces": ["*"],
    "scopes": ["*"]
  },
  "key_memory_reader": {
    "namespaces": ["default", "tenant-a"],
    "scopes": ["memory:read"]
  },
  "key_policy_ops": {
    "namespaces": ["tenant-a"],
    "scopes": ["policy:*", "memory:read", "memory:write"]
  }
}
```

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

Run with auth enabled (example):

```bash
export AGENT_MEMORY_AUTH_MODE=api_key
export AGENT_MEMORY_API_KEYS_JSON='{"key_admin":{"namespaces":["*"],"scopes":["*"]}}'
agent-memory-mcp
```

## Local evaluator regression suite

Regression cases live in `evals/policy_regression_cases.json` and are replayed during
`policy.evaluate`.

## Concrete build plan from here

1. Add provider adapters for Qdrant/pgvector and switch memory search from full-scan to ANN.
2. Add asynchronous ingestion/distillation workers and background eval jobs.
3. Add replay benchmark harness with dataset snapshots and trend reporting.
4. Add policy artifact signing and tamper-evident audit logs.

## Publish / update GitHub

```bash
cd <repo-root>
git add .
git commit -m "Build out v0.3.0: MCP auth and ACL enforcement"
git push
```
