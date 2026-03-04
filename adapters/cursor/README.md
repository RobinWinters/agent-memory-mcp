# Cursor Adapter (v1)

These helper scripts wire `agent-memory-mcp` handoff into a Cursor-style start/end workflow.

## Files

- `session_start.sh`
- `session_end.sh`

## Behavior

- `session_start.sh`
  - imports `.agent-memory/handoff.json` into the current namespace
  - verifies signature by default
  - writes refreshed prompt context to `.agent-memory/context.md`

- `session_end.sh`
  - exports a signed handoff bundle to `.agent-memory/handoff.json`
  - writes prompt context to `.agent-memory/context.md`
  - updates `.agent-memory/cursor.json` for incremental delta exports

## Environment variables

- `AGENT_MEMORY_DB` (default `./data/agent_memory.db`)
- `AGENT_MEMORY_NAMESPACE` (default `default`)
- `AGENT_MEMORY_HANDOFF_FILE` (default `.agent-memory/handoff.json`)
- `AGENT_MEMORY_PROMPT_FILE` (default `.agent-memory/context.md`)
- `AGENT_MEMORY_CURSOR_FILE` (default `.agent-memory/cursor.json`, incremental export cursor)
- `AGENT_MEMORY_POLICY_SIGNING_SECRET` (required for signed export/verified import)

## Usage

```bash
./adapters/cursor/session_start.sh
# ...work...
./adapters/cursor/session_end.sh
```
