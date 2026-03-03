# agent-memory-mcp

MCP server for agent memory + policy self-improvement workflows with namespace partitioning,
pluggable embeddings/vector backends, structured policy evaluation, API-key ACL auth, durable async jobs,
background workers, observability, and integrity verification.

## Current build status (v0.9.0)

Implemented:

- Immutable event ingest and session distillation into memory notes.
- Namespace-aware partitioning for memory, policy, jobs, and queue operations.
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
- Durable async jobs + background worker daemon.
- Reliability hardening:
  - retries, backoff, dead-lettering, stuck-job recovery
- Observability:
  - queue health snapshots
  - throughput/success/failure metrics
  - queue/run/end-to-end latency aggregates
- Integrity hardening:
  - policy content digest + optional HMAC signatures
  - append-only audit hash chain
  - audit chain + policy signature verification tools

## Binaries

- `agent-memory-mcp`: MCP server (stdio)
- `agent-memory-worker`: background queue worker

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
- `ops.health(namespace?, api_key?)`
- `ops.metrics(window_minutes=60, namespace?, api_key?)`
- `ops.audit_recent(limit=50, namespace?, api_key?)`
- `ops.audit_verify(limit=1000, namespace?, api_key?)`

Supported `job_type` values:
- `memory.distill`
- `policy.evaluate`

## Job lifecycle semantics

Persisted statuses:
- `queued`
- `running`
- `succeeded`
- `dead`

`jobs.run_pending` also reports:
- `retried`
- `dead`
- `recovered_stuck` (`requeued`, `dead_lettered`)

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

Worker:
- `AGENT_MEMORY_WORKER_POLL_SECONDS` (default: `1.0`)
- `AGENT_MEMORY_WORKER_BATCH_SIZE` (default: `20`)
- `AGENT_MEMORY_WORKER_NAMESPACES` (comma-separated list, default: `AGENT_MEMORY_NAMESPACE`)

Job reliability:
- `AGENT_MEMORY_JOB_MAX_ATTEMPTS` (default: `3`)
- `AGENT_MEMORY_JOB_BACKOFF_BASE_SECONDS` (default: `2.0`)
- `AGENT_MEMORY_JOB_BACKOFF_MAX_SECONDS` (default: `300.0`)
- `AGENT_MEMORY_JOB_RUNNING_TIMEOUT_SECONDS` (default: `300.0`)

Integrity:
- `AGENT_MEMORY_POLICY_SIGNING_SECRET` (optional HMAC secret for policy version signatures)
- `AGENT_MEMORY_AUDIT_SIGNING_SECRET` (optional HMAC secret for audit chain; defaults to policy secret)

Auth:
- `AGENT_MEMORY_AUTH_MODE` (`off` or `api_key`, default: `off`)
- `AGENT_MEMORY_API_KEYS_JSON` (inline key-policy JSON)
- `AGENT_MEMORY_API_KEYS_FILE` (path to JSON file, preferred in production)

Auth scope families:
- `memory:*`
- `policy:*`
- `jobs:*`

`ops.*` tools use `jobs:read` scope.

## Quickstart

```bash
cd <repo-root>
python3 -m venv .venv
source .venv/bin/activate
pip install ".[dev]"
pytest -q
```

Run MCP server:

```bash
source .venv/bin/activate
agent-memory-mcp
```

Run worker daemon:

```bash
source .venv/bin/activate
agent-memory-worker
```

Run server + worker together (two terminals) for automatic async processing.

## Qdrant setup

Start Qdrant locally:

```bash
docker run -p 6333:6333 qdrant/qdrant
```

Run with Qdrant backend:

```bash
export AGENT_MEMORY_VECTOR_BACKEND=qdrant
export QDRANT_URL=http://localhost:6333
export QDRANT_COLLECTION=agent_memory
agent-memory-mcp
```

Qdrant handles vector lookup while SQLite remains canonical source for memory/policy/job records.

## Test cadence

```bash
source .venv/bin/activate
pytest tests/test_service.py -q
pytest tests/test_jobs.py -q
pytest tests/test_worker.py -q
pytest tests/test_observability.py -q
pytest tests/test_integrity.py -q
pytest -q
```

## Next phase

1. Exportable metrics endpoint/adapters (Prometheus/OpenTelemetry).
2. Key rotation/management tooling for signing and auth secrets.

## Publish / update GitHub

```bash
cd <repo-root>
git add .
git commit -m "Build out v0.9.0: policy signing and tamper-evident audit logs"
git push
```
