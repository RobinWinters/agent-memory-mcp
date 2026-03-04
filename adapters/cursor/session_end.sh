#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${AGENT_MEMORY_DB:-./data/agent_memory.db}"
NAMESPACE="${AGENT_MEMORY_NAMESPACE:-default}"
HANDOFF_FILE="${AGENT_MEMORY_HANDOFF_FILE:-.agent-memory/handoff.json}"
PROMPT_FILE="${AGENT_MEMORY_PROMPT_FILE:-.agent-memory/context.md}"
CURSOR_FILE="${AGENT_MEMORY_CURSOR_FILE:-.agent-memory/cursor.json}"

agent-memory-adapter cursor-end \
  --db "${DB_PATH}" \
  --namespace "${NAMESPACE}" \
  --handoff-file "${HANDOFF_FILE}" \
  --prompt-file "${PROMPT_FILE}" \
  --cursor-file "${CURSOR_FILE}" \
  --include-events \
  --sign \
  --pretty
