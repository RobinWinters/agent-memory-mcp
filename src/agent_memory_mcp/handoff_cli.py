from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_memory_mcp.handoff_schema import get_handoff_json_schema
from agent_memory_mcp.runtime_bootstrap import build_service_from_settings, load_settings_from_env


def _build_service(*, db_path: str | None, namespace: str | None):
    settings = load_settings_from_env()
    updated = settings
    if db_path:
        updated = replace(updated, db_path=str(db_path))
    if namespace and namespace.strip():
        updated = replace(updated, default_namespace=namespace.strip())
    service = build_service_from_settings(settings=updated)
    return updated, service


def _read_json(path: str) -> dict[str, Any]:
    if path == "-":
        payload = sys.stdin.read()
    else:
        payload = Path(path).read_text(encoding="utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("handoff input must be a JSON object")
    return data


def _json_dump(payload: Any, *, pretty: bool) -> str:
    if pretty:
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2)
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _cmd_export(args: argparse.Namespace) -> int:
    _, service = _build_service(db_path=args.db, namespace=args.namespace)
    try:
        payload = service.memory_handoff_export(
            query=args.query,
            k=args.k,
            include_policy=args.include_policy,
            include_events=args.include_events,
            max_events_per_session=args.max_events_per_session,
            namespace=args.namespace,
        )
    finally:
        service.db.close()

    serialized = _json_dump(payload, pretty=args.pretty) + "\n"
    if args.output == "-":
        sys.stdout.write(serialized)
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized, encoding="utf-8")

    if args.prompt_output:
        prompt_path = Path(args.prompt_output)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_text = str(payload.get("prompt_md", "")).rstrip() + "\n"
        prompt_path.write_text(prompt_text, encoding="utf-8")

    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    handoff = _read_json(args.input)
    _, service = _build_service(db_path=args.db, namespace=args.namespace)
    try:
        result = service.memory_handoff_import(
            handoff=handoff,
            session_id_prefix=args.session_id_prefix,
            import_policy=args.import_policy,
            import_events=args.import_events,
            max_events_per_session=args.max_events_per_session,
            namespace=args.namespace,
        )
    finally:
        service.db.close()

    sys.stdout.write(_json_dump(result, pretty=args.pretty) + "\n")
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    payload = get_handoff_json_schema()
    serialized = _json_dump(payload, pretty=args.pretty) + "\n"
    if args.output == "-":
        sys.stdout.write(serialized)
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized, encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-memory-handoff",
        description="Export/import portable handoff bundles for cross-agent continuity.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Export portable handoff JSON.")
    export_parser.add_argument("--db", default=None, help="SQLite DB path override.")
    export_parser.add_argument("--namespace", default=None, help="Namespace override.")
    export_parser.add_argument("--query", default=None, help="Optional semantic query for memory selection.")
    export_parser.add_argument("--k", type=int, default=20, help="Number of memories to export.")
    export_parser.add_argument(
        "--include-policy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include active policy content.",
    )
    export_parser.add_argument(
        "--include-events",
        action="store_true",
        help="Include raw session events linked to exported memories.",
    )
    export_parser.add_argument(
        "--max-events-per-session",
        type=int,
        default=20,
        help="Max events to include for each exported session.",
    )
    export_parser.add_argument("--output", default="-", help="Output JSON file path, or '-' for stdout.")
    export_parser.add_argument("--prompt-output", default=None, help="Optional file path for prompt markdown.")
    export_parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    export_parser.set_defaults(handler=_cmd_export)

    import_parser = subparsers.add_parser("import", help="Import portable handoff JSON.")
    import_parser.add_argument("--db", default=None, help="SQLite DB path override.")
    import_parser.add_argument("--namespace", default=None, help="Target namespace override.")
    import_parser.add_argument("--input", required=True, help="Input handoff JSON path, or '-' for stdin.")
    import_parser.add_argument(
        "--session-id-prefix",
        default="imported",
        help="Fallback session ID prefix for imported records.",
    )
    import_parser.add_argument("--import-policy", action="store_true", help="Import and activate policy snapshot.")
    import_parser.add_argument("--import-events", action="store_true", help="Import raw session events.")
    import_parser.add_argument(
        "--max-events-per-session",
        type=int,
        default=200,
        help="Max events to import for each session.",
    )
    import_parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    import_parser.set_defaults(handler=_cmd_import)

    schema_parser = subparsers.add_parser("schema", help="Print bundled handoff JSON Schema.")
    schema_parser.add_argument("--output", default="-", help="Output schema file path, or '-' for stdout.")
    schema_parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    schema_parser.set_defaults(handler=_cmd_schema)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        handler = args.handler
        return int(handler(args))
    except Exception as exc:  # noqa: BLE001
        print(f"agent-memory-handoff.error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
