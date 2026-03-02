# agent-memory-mcp

MCP server for agent memory + policy self-improvement workflows with namespace partitioning,
pluggable embeddings/vector backends, structured policy evaluation, API-key ACL auth, and durable async jobs.

## Current build status (v0.5.0)

Implemented:

- Immutable event ingest and session distillation into memory notes.
- Namespace-aware partitioning for memory, policy, and jobs.
- Pluggable embedding backend:
  - `hash` (default, offline deterministic)
  - `openai` (remote embeddings API)
- Pluggable vector search backend:
  - `sqlite` (default, local full-scan)
  - `qdrant` (external ANN)
- Structured policy evaluator with weighted checks + regression replay.
- Policy lifecycle with gating:
  - `propose` -> `evaluate` -> `promote` -> `rollback`
- API-key auth + ACL scopes.
- Durable async jobs for `memory.distill` and `policy.evaluate`.

## MCP tools

- `memory.append(session_id, role, content, metadata?, namespace?, api_key?)`
- `memory.distill(session_id, max_lines=6, async_mode=false, namespace?, api_key?)`
- `memory.search(query, k=5, namespace?, api_key?)`
- `policy.get(active_version=true, namespace?, api_key?)`
- `policy.propose(delta_md, evidence_refs=[], namespace?, api_key?)`
- `policy.evaluate(proposal_id, async_mode=false, namespace?, api_key?)`
- `policy.promote(proposal_id, namespace?, api_key?)`
- `policy.rollback(version_id, namespace?, api_key?)`
- `jobs.submit(job_type, payload, namespace?, api_key?)`
- `jobs.run_pending(limit=1, namespace?, api_key?)`
- `jobs.status(job_id, namespace?, api_key?)`
- `jobs.result(job_id, namespace?, api_key?)`

Supported `job_type` values:
- `memory.distill`
- `policy.evaluate`

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

Auth scope families:
- `memory:*`
- `policy:*`
- `jobs:*`

`AGENT_MEMORY_API_KEYS_JSON` / file format:

```json
{
  "key_admin": {
    "namespaces": ["*"],
    "scopes": ["*"]
  },
  "key_runner": {
    "namespaces": ["tenant-a"],
    "scopes": ["memory:read", "memory:write", "policy:*", "jobs:*"]
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

Run server:

```bash
source .venv/bin/activate
agent-memory-mcp
```

## Async job flow

Example (`memory.distill` async):

1. Call `memory.distill(..., async_mode=true)`.
2. Call `jobs.run_pending(limit=1)`.
3. Poll `jobs.status(job_id)` and read `jobs.result(job_id)`.

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

## Regression testing cadence

Recommended while iterating:

```bash
source .venv/bin/activate
pytest tests/test_service.py -q
pytest tests/test_jobs.py -q
pytest -q
```

## Publish / update GitHub

```bash
cd <repo-root>
git add .
git commit -m "Build out v0.5.0: async job queue and MCP job tools"
git push
```
