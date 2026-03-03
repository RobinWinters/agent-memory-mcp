# agent-memory-mcp

MCP server for agent memory + policy self-improvement workflows with namespace partitioning,
pluggable embeddings/vector backends, structured policy evaluation, API-key ACL auth, durable async jobs,
background workers, observability, integrity verification, and export adapters.

## Purpose

This system is for agents/models that want durable memory plus controlled self-improvement:

- Capture raw session events as immutable source data.
- Distill sessions into retrievable memory notes.
- Propose/evaluate/promote policy updates with explicit gates.
- Serve all operations through MCP tools with namespace isolation and auth.

## End-to-end flow

1. Append raw events with `memory.append`.
2. Distill sessions into vector-searchable notes with `memory.distill`.
3. Retrieve prior context with `memory.search`.
4. Propose policy deltas with `policy.propose`.
5. Evaluate with `policy.evaluate`.
6. Promote passing changes with `policy.promote` (or rollback with `policy.rollback`).

## Current build status (v0.12.1)

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
  - audit chain + policy signature verification
- Metrics export adapters:
  - Prometheus exposition text
  - OpenTelemetry-style JSON payload
- Keyring-based security management:
  - file-backed signing key rotation (`policy`/`audit`)
  - auth API-key upsert/disable management
  - runtime hot-reload into MCP server auth/signing state
- HTTP metrics endpoint bridge:
  - Prometheus scrape endpoint over HTTP
  - OTel-style metrics endpoint over HTTP JSON
  - optional bearer token guard
- Internal architecture refactor on `main`:
  - DB layer split into domain modules (`db_*`)
  - MCP tool registration split by domain (`server_tools_*`)
  - service layer split by domain (`service_*`)
  - runtime state centralized in `AppContext`

## Architecture snapshot

- Core domain:
  - `service.py` (facade) + `service_base.py`, `service_memory.py`, `service_policy.py`, `service_jobs.py`, `service_ops.py`
  - `db.py` (facade) + `db_schema.py`, `db_memory.py`, `db_policy.py`, `db_jobs.py`, `db_audit.py`
- Runtime wiring:
  - `app_context.py` (settings/service/auth/keyring runtime state for MCP server)
  - `runtime_bootstrap.py` (shared env/settings/service bootstrap helpers)
- Entrypoints:
  - `server.py` (MCP process bootstrap)
  - `worker.py` (queue worker loop)
  - `metrics_http.py` (HTTP metrics bridge)

## Binaries

- `agent-memory-mcp`: MCP server (stdio)
- `agent-memory-worker`: background queue worker
- `agent-memory-metrics-http`: HTTP bridge for metrics scraping/reads

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
- `ops.metrics_prometheus(window_minutes=60, namespace?, api_key?)`
- `ops.metrics_otel(window_minutes=60, namespace?, api_key?)`
- `ops.audit_recent(limit=50, namespace?, api_key?)`
- `ops.audit_verify(limit=1000, namespace?, api_key?)`
- `ops.keyring_status(namespace?, api_key?)`
- `ops.keyring_reload(namespace?, api_key?)`
- `ops.keyring_rotate(purpose, secret?, key_id?, disable_previous=false, namespace?, api_key?)`
- `ops.keyring_upsert_api_key(managed_api_key, namespaces, scopes, enabled=true, label?, namespace?, api_key?)`
- `ops.keyring_disable_api_key(managed_api_key, namespace?, api_key?)`

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
- `AGENT_MEMORY_POLICY_SIGNING_SECRET` (optional HMAC secret for policy signatures)
- `AGENT_MEMORY_AUDIT_SIGNING_SECRET` (optional HMAC secret for audit chain; defaults to policy secret)
- `AGENT_MEMORY_KEYRING_FILE` (optional JSON keyring path for runtime signing/auth key management)

Metrics HTTP bridge:
- `AGENT_MEMORY_METRICS_HTTP_HOST` (default: `127.0.0.1`)
- `AGENT_MEMORY_METRICS_HTTP_PORT` (default: `9475`)
- `AGENT_MEMORY_METRICS_WINDOW_MINUTES` (default: `60`)
- `AGENT_MEMORY_METRICS_NAMESPACE` (default: `AGENT_MEMORY_NAMESPACE`)
- `AGENT_MEMORY_METRICS_TOKEN` (optional bearer token for endpoint access)

Auth:
- `AGENT_MEMORY_AUTH_MODE` (`off` or `api_key`, default: `off`)
- `AGENT_MEMORY_API_KEYS_JSON` (inline key-policy JSON)
- `AGENT_MEMORY_API_KEYS_FILE` (path to JSON file, preferred in production)

Auth scope families:
- `memory:*`
- `policy:*`
- `jobs:*`
- `security:*`

`ops.*` tools use `jobs:read` scope.
`ops.keyring_*` tools use `security:read`/`security:manage` scopes.

## Quickstart

```bash
cd <repo-root>
python3 -m venv .venv
source .venv/bin/activate
pip install ".[dev]"
pytest -q
```

Basic local run (3 processes):

```bash
source .venv/bin/activate
agent-memory-mcp
```

```bash
source .venv/bin/activate
agent-memory-worker
```

```bash
source .venv/bin/activate
agent-memory-metrics-http
```

Then:

1. Send events to `memory.append`.
2. Distill with `memory.distill`.
3. Query with `memory.search`.
4. Iterate policy via `policy.propose` -> `policy.evaluate` -> `policy.promote`.

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

Run metrics HTTP bridge:

```bash
source .venv/bin/activate
agent-memory-metrics-http
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

## Metrics export usage

Prometheus format via MCP:

1. Call `ops.metrics_prometheus(window_minutes=60)`.
2. Read returned `text` field and expose through your scrape bridge.

OTel-style JSON via MCP:

1. Call `ops.metrics_otel(window_minutes=60)`.
2. Forward returned `payload` into your telemetry pipeline adapter.

## Keyring quickstart

Enable file-backed key management:

```bash
export AGENT_MEMORY_KEYRING_FILE=<repo-root>/data/keyring.json
agent-memory-mcp
```

Then manage keys through MCP:

1. `ops.keyring_status()` to inspect current state.
2. `ops.keyring_rotate(purpose="policy")` to rotate policy signing.
3. `ops.keyring_upsert_api_key(...)` / `ops.keyring_disable_api_key(...)` to manage auth keys.

## HTTP bridge usage

Default endpoints:

- `GET /metrics` (Prometheus text)
- `GET /metrics/otel` (OTel-style JSON)
- `GET /health` (queue health JSON)

Optional query overrides:

- `namespace=<name>`
- `window_minutes=<int>`
- `token=<value>` (only if token auth enabled)

## Test cadence

```bash
source .venv/bin/activate
pytest tests/test_service.py -q
pytest tests/test_jobs.py -q
pytest tests/test_worker.py -q
pytest tests/test_observability.py -q
pytest tests/test_integrity.py -q
pytest tests/test_metrics_export.py -q
pytest tests/test_metrics_http.py -q
pytest tests/test_keyring.py -q
pytest tests/test_app_context.py -q
pytest -q
```

## Next phase

1. Add role-separated auth presets (`admin`, `writer`, `reader`) bootstrap helper.
2. Optional SSE/streaming endpoint for real-time job queue updates.

## Publish / update GitHub

```bash
cd <repo-root>
git add .
git commit -m "your change summary"
git push
```
