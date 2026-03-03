from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

HANDOFF_SCHEMA_ID = "agent-memory-handoff.v1"
HANDOFF_SCHEMA_RESOURCE = "schemas/agent-memory-handoff.v1.schema.json"


@lru_cache(maxsize=1)
def get_handoff_json_schema() -> dict[str, Any]:
    raw = files("agent_memory_mcp").joinpath(HANDOFF_SCHEMA_RESOURCE).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("handoff schema resource is invalid")
    return payload


@lru_cache(maxsize=1)
def _handoff_validator() -> Draft202012Validator:
    schema = get_handoff_json_schema()
    return Draft202012Validator(schema=schema)


def validate_handoff_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("handoff must be a JSON object")

    schema = str(payload.get("schema", "")).strip()
    if schema != HANDOFF_SCHEMA_ID:
        raise ValueError(f"unsupported handoff schema '{schema or '<missing>'}'")

    validator = _handoff_validator()
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
    if not errors:
        return

    first = errors[0]
    path = ".".join(str(part) for part in first.path)
    location = path or "<root>"
    raise ValueError(f"handoff schema validation failed at {location}: {first.message}")
