#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${AGENT_MEMORY_METRICS_BASE_URL:-http://127.0.0.1:9475}"
NAMESPACE="${1:-default}"
INTERVAL_SECONDS="${2:-1}"
INCLUDE_METRICS="${3:-true}"
MAX_EVENTS="${4:-}"
TOKEN="${AGENT_MEMORY_METRICS_TOKEN:-}"

URL="${BASE_URL}/stream/jobs?namespace=${NAMESPACE}&interval_seconds=${INTERVAL_SECONDS}&include_metrics=${INCLUDE_METRICS}"

if [[ -n "${MAX_EVENTS}" ]]; then
  URL="${URL}&max_events=${MAX_EVENTS}"
fi

if [[ -n "${TOKEN}" ]]; then
  exec curl -N -H "Authorization: Bearer ${TOKEN}" "${URL}"
else
  exec curl -N "${URL}"
fi
