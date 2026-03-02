# agent-memory-mcp

MCP server for agent memory + policy self-improvement workflows with namespace partitioning,
pluggable embeddings, pluggable vector backends, structured policy evaluation, and API-key ACL authorization.

## Current build status (v0.4.0)

Implemented now:

- Immutable event ingest and session distillation into memory notes.
- Namespace-aware data partitioning across all memory and policy entities.
- Pluggable embedding backend:
  - `hash` (default, offline deterministic)
  - `openai` (remote embeddings API)
- Pluggable vector search backend:
  - `sqlite` (default, local full-scan)
  - `qdrant` (ANN-capable external vector store)
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

Vector backend:
- `AGENT_MEMORY_VECTOR_BACKEND` (`sqlite` or `qdrant`, default: `sqlite`)
- `QDRANT_URL` (default: `http://localhost:6333`)
- `QDRANT_API_KEY` (optional)
- `QDRANT_COLLECTION` (default: `agent_memory`)
- `QDRANT_TIMEOUT_SECONDS` (default: `10`)
- `QDRANT_AUTO_CREATE_COLLECTION` (`true`/`false`, default: `true`)

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

## Qdrant setup

Start Qdrant locally:

```bash
docker run -p 6333:6333 qdrant/qdrant
```

Run server with Qdrant backend:

```bash
export AGENT_MEMORY_VECTOR_BACKEND=qdrant
export QDRANT_URL=http://localhost:6333
export QDRANT_COLLECTION=agent_memory
agent-memory-mcp
```

Qdrant is used for vector lookup while SQLite remains canonical storage for full memory records.

## Local evaluator regression suite

Regression cases live in `evals/policy_regression_cases.json` and are replayed during
`policy.evaluate`.

## Concrete build plan from here

1. Add asynchronous ingestion/distillation workers and background eval jobs.
2. Add replay benchmark harness with dataset snapshots and trend reporting.
3. Add policy artifact signing and tamper-evident audit logs.

## Publish / update GitHub

```bash
cd <repo-root>
git add .
git commit -m "Build out v0.4.0: Qdrant vector backend"
git push
```
