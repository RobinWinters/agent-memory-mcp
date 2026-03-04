from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

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


def _json_dump(payload: Any, *, pretty: bool) -> str:
    if pretty:
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2)
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _read_handoff_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"handoff file '{path}' must contain a JSON object")
    return payload


def _cmd_cursor_start(args: argparse.Namespace) -> int:
    handoff_path = Path(args.handoff_file)
    prompt_path = Path(args.prompt_file)

    _, service = _build_service(db_path=args.db, namespace=args.namespace)
    try:
        import_result: dict[str, Any] | None = None
        handoff_loaded = False
        handoff_signed = False

        if handoff_path.exists():
            payload = _read_handoff_file(handoff_path)
            import_result = service.memory_handoff_import(
                handoff=payload,
                session_id_prefix=args.session_id_prefix,
                import_policy=args.import_policy,
                import_events=args.import_events,
                max_events_per_session=args.max_events_per_session,
                verify=args.verify,
                namespace=args.namespace,
            )
            handoff_loaded = True
            handoff_signed = isinstance(payload.get("signature"), dict)
        elif args.require_handoff:
            raise ValueError(f"handoff file not found: {handoff_path}")

        current = service.memory_handoff_export(
            query=args.query,
            k=args.k,
            include_policy=True,
            include_events=False,
            max_events_per_session=20,
            sign=False,
            namespace=args.namespace,
        )
    finally:
        service.db.close()

    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_text = str(current.get("prompt_md", "")).rstrip() + "\n"
    prompt_path.write_text(prompt_text, encoding="utf-8")

    summary = {
        "adapter": "cursor",
        "action": "start",
        "namespace": current["namespace"],
        "handoff_file": str(handoff_path),
        "handoff_loaded": handoff_loaded,
        "handoff_signed": handoff_signed,
        "import_result": import_result,
        "prompt_file": str(prompt_path),
        "prompt_chars": len(prompt_text),
        "context_stats": current.get("stats", {}),
        "generated_at": current.get("generated_at"),
    }
    sys.stdout.write(_json_dump(summary, pretty=args.pretty) + "\n")
    return 0


def _cmd_cursor_end(args: argparse.Namespace) -> int:
    handoff_path = Path(args.handoff_file)
    prompt_path = Path(args.prompt_file)

    _, service = _build_service(db_path=args.db, namespace=args.namespace)
    try:
        payload = service.memory_handoff_export(
            query=args.query,
            k=args.k,
            include_policy=True,
            include_events=args.include_events,
            max_events_per_session=args.max_events_per_session,
            sign=args.sign,
            namespace=args.namespace,
        )
    finally:
        service.db.close()

    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(_json_dump(payload, pretty=True) + "\n", encoding="utf-8")

    if args.write_prompt:
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_text = str(payload.get("prompt_md", "")).rstrip() + "\n"
        prompt_path.write_text(prompt_text, encoding="utf-8")
    else:
        prompt_text = ""

    summary = {
        "adapter": "cursor",
        "action": "end",
        "namespace": payload["namespace"],
        "handoff_file": str(handoff_path),
        "signed": isinstance(payload.get("signature"), dict),
        "prompt_file": str(prompt_path) if args.write_prompt else None,
        "prompt_chars": len(prompt_text),
        "stats": payload.get("stats", {}),
        "generated_at": payload.get("generated_at"),
    }
    sys.stdout.write(_json_dump(summary, pretty=args.pretty) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-memory-adapter",
        description="Adapter workflows for integrating agent-memory-mcp into AI IDE/session lifecycles.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("cursor-start", help="Import handoff and refresh prompt context.")
    start_parser.add_argument("--db", default=None, help="SQLite DB path override.")
    start_parser.add_argument("--namespace", default=None, help="Namespace override.")
    start_parser.add_argument("--handoff-file", default=".agent-memory/handoff.json", help="Handoff bundle path.")
    start_parser.add_argument("--prompt-file", default=".agent-memory/context.md", help="Prompt markdown output path.")
    start_parser.add_argument("--query", default=None, help="Optional context refresh query.")
    start_parser.add_argument("--k", type=int, default=20, help="Memories to include in refreshed prompt.")
    start_parser.add_argument(
        "--import-policy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Import policy from handoff if present.",
    )
    start_parser.add_argument(
        "--import-events",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Import events from handoff if present.",
    )
    start_parser.add_argument(
        "--verify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Verify handoff signature when present.",
    )
    start_parser.add_argument(
        "--require-handoff",
        action="store_true",
        help="Fail if handoff file is missing.",
    )
    start_parser.add_argument("--session-id-prefix", default="imported", help="Fallback session ID prefix.")
    start_parser.add_argument("--max-events-per-session", type=int, default=200, help="Event import cap.")
    start_parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    start_parser.set_defaults(handler=_cmd_cursor_start)

    end_parser = subparsers.add_parser("cursor-end", help="Export handoff bundle and prompt context.")
    end_parser.add_argument("--db", default=None, help="SQLite DB path override.")
    end_parser.add_argument("--namespace", default=None, help="Namespace override.")
    end_parser.add_argument("--handoff-file", default=".agent-memory/handoff.json", help="Handoff bundle path.")
    end_parser.add_argument("--prompt-file", default=".agent-memory/context.md", help="Prompt markdown output path.")
    end_parser.add_argument("--query", default=None, help="Optional semantic selection query.")
    end_parser.add_argument("--k", type=int, default=20, help="Number of memories to export.")
    end_parser.add_argument("--include-events", action="store_true", help="Include raw events in exported handoff.")
    end_parser.add_argument("--max-events-per-session", type=int, default=20, help="Event export cap.")
    end_parser.add_argument(
        "--sign",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Sign handoff payload.",
    )
    end_parser.add_argument(
        "--write-prompt",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write prompt markdown file.",
    )
    end_parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    end_parser.set_defaults(handler=_cmd_cursor_end)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:  # noqa: BLE001
        print(f"agent-memory-adapter.error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
