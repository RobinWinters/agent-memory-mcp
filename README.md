# agent-memory-mcp

<p align="center">
  <img src="assets/banner.svg" alt="agent-memory-mcp banner" />
</p>

`agent-memory-mcp` is a local-first memory and policy service for AI agents.

It gives an agent a structured way to:
- save session events,
- distill useful memory notes,
- retrieve memory later,
- export/import portable handoff bundles across models or IDEs,
- propose and evaluate policy changes,
- run async jobs with retries,
- expose health/metrics for ops dashboards.

## What this is for

Use this when you want agent behavior to improve over time without losing control.

Instead of letting prompts drift, you keep:
- durable memory (events + distilled notes),
- explicit policy versions (`propose -> evaluate -> promote`),
- auditability and optional signing.

## Release notes

### v0.14.0 (March 3, 2026)

- Added `/stream/jobs` SSE endpoint for live queue snapshots with token auth support.
- Added end-to-end golden path integration test across MCP tools, worker, metrics, and SSE.
- Added runnable examples:
  - `examples/golden_path.py`
  - `examples/sse_watch.sh`
- Simplified README wording and removed user-specific local path examples.

## High-level architecture

The system runs as three processes:
1. `agent-memory-mcp` -> MCP server (tools over stdio)
2. `agent-memory-worker` -> async job worker
3. `agent-memory-metrics-http` -> HTTP health/metrics/SSE bridge

## Quickstart

```bash
git clone <repo-url>
cd agent-memory-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install ".[dev]"
pytest -q
```

Start the services in separate terminals:

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

## Typical usage flow

1. Call `memory.append` to record raw events.
2. Call `memory.distill` to create memory notes.
3. Call `memory.search` to retrieve relevant context.
4. Call `policy.propose`.
5. Call `policy.evaluate`.
6. Call `policy.promote` (or `policy.rollback`).

## Core MCP tools

Memory:
- `memory.append`
- `memory.distill`
- `memory.search`
- `memory.handoff_export`
- `memory.handoff_import`

Policy:
- `policy.get`
- `policy.propose`
- `policy.evaluate`
- `policy.promote`
- `policy.rollback`

Jobs:
- `jobs.submit`
- `jobs.run_pending`
- `jobs.status`
- `jobs.result`

Ops:
- `ops.health`
- `ops.metrics`
- `ops.metrics_prometheus`
- `ops.metrics_otel`
- `ops.audit_recent`
- `ops.audit_verify`
- `ops.keyring_status`
- `ops.keyring_reload`
- `ops.keyring_rotate`
- `ops.keyring_upsert_api_key`
- `ops.keyring_disable_api_key`
- `ops.keyring_list_presets`
- `ops.keyring_apply_preset`

## HTTP ops endpoints

Default endpoints:
- `GET /health` -> queue health JSON
- `GET /metrics` -> Prometheus text
- `GET /metrics/otel` -> OTel-style JSON
- `GET /stream/jobs` -> Server-Sent Events stream for live queue snapshots

Common query params:
- `namespace`
- `window_minutes`
- `token` (if token auth is enabled)

SSE-specific query params (`/stream/jobs`):
- `interval_seconds`
- `include_metrics`
- `max_events`

Example:

```bash
curl -N "http://127.0.0.1:9475/stream/jobs?interval_seconds=1&include_metrics=true"
```

## Minimal environment variables

Start with these:
- `AGENT_MEMORY_DB` (default: `./data/agent_memory.db`)
- `AGENT_MEMORY_NAMESPACE` (default: `default`)
- `AGENT_MEMORY_EMBEDDING_BACKEND` (`hash` or `openai`, default: `hash`)
- `AGENT_MEMORY_VECTOR_BACKEND` (`sqlite` or `qdrant`, default: `sqlite`)

If using OpenAI embeddings:
- `OPENAI_API_KEY`
- `OPENAI_EMBEDDING_MODEL` (default: `text-embedding-3-small`)

If enabling API-key auth:
- `AGENT_MEMORY_AUTH_MODE=api_key`
- `AGENT_MEMORY_API_KEYS_FILE=/path/to/api-keys.json` (or `AGENT_MEMORY_API_KEYS_JSON`)

If enabling keyring-backed signing/auth management:
- `AGENT_MEMORY_KEYRING_FILE=/path/to/keyring.json`

## Example scripts

Run an end-to-end local workflow:

```bash
source .venv/bin/activate
python examples/golden_path.py --namespace default --session-id demo-session
```

Watch live queue snapshots over SSE:

```bash
./examples/sse_watch.sh default 1 true
```

If metrics token auth is enabled:

```bash
export AGENT_MEMORY_METRICS_TOKEN=your-token
./examples/sse_watch.sh default 1 true
```

## Model-agnostic handoff

Export context bundle from one environment:

1. Call `memory.handoff_export(include_policy=true, include_events=true)`.
2. Save returned JSON and optional `prompt_md`.
3. Transfer to another model, IDE, or deployment.

Import into another environment:

1. Call `memory.handoff_import(handoff=<json>, import_policy=true, import_events=true)`.
2. Continue with `memory.search` and normal `policy.*` workflow.

## Full environment reference

Core:
- `AGENT_MEMORY_DB`
- `AGENT_MEMORY_NAMESPACE`
- `AGENT_MEMORY_EMBEDDING_BACKEND`
- `OPENAI_API_KEY`
- `OPENAI_EMBEDDING_MODEL`
- `AGENT_MEMORY_POLICY_PASS_THRESHOLD`

Vector backend:
- `AGENT_MEMORY_VECTOR_BACKEND`
- `QDRANT_URL`
- `QDRANT_API_KEY`
- `QDRANT_COLLECTION`
- `QDRANT_TIMEOUT_SECONDS`
- `QDRANT_AUTO_CREATE_COLLECTION`

Worker:
- `AGENT_MEMORY_WORKER_POLL_SECONDS`
- `AGENT_MEMORY_WORKER_BATCH_SIZE`
- `AGENT_MEMORY_WORKER_NAMESPACES`

Job reliability:
- `AGENT_MEMORY_JOB_MAX_ATTEMPTS`
- `AGENT_MEMORY_JOB_BACKOFF_BASE_SECONDS`
- `AGENT_MEMORY_JOB_BACKOFF_MAX_SECONDS`
- `AGENT_MEMORY_JOB_RUNNING_TIMEOUT_SECONDS`

Integrity:
- `AGENT_MEMORY_POLICY_SIGNING_SECRET`
- `AGENT_MEMORY_AUDIT_SIGNING_SECRET`
- `AGENT_MEMORY_KEYRING_FILE`

Metrics HTTP bridge:
- `AGENT_MEMORY_METRICS_HTTP_HOST`
- `AGENT_MEMORY_METRICS_HTTP_PORT`
- `AGENT_MEMORY_METRICS_WINDOW_MINUTES`
- `AGENT_MEMORY_METRICS_NAMESPACE`
- `AGENT_MEMORY_METRICS_STREAM_INTERVAL_SECONDS`
- `AGENT_MEMORY_METRICS_STREAM_INCLUDE_METRICS`
- `AGENT_MEMORY_METRICS_TOKEN`

Auth:
- `AGENT_MEMORY_AUTH_MODE`
- `AGENT_MEMORY_API_KEYS_JSON`
- `AGENT_MEMORY_API_KEYS_FILE`

## Auth scopes

Scope families:
- `memory:*`
- `policy:*`
- `jobs:*`
- `security:*`

Notes:
- `ops.*` requires `jobs:read`
- `ops.keyring_*` requires `security:read` or `security:manage`

## Testing

Run all tests:

```bash
source .venv/bin/activate
pytest -q
```

Run a targeted suite:

```bash
source .venv/bin/activate
pytest tests/test_metrics_http.py -q
pytest tests/test_golden_path_integration.py -q
pytest tests/test_handoff.py -q
```
