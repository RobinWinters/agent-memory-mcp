# agent-memory-mcp

MVP for agent self-improvement memory and policy management over MCP.

## What this repo does

- Stores immutable session events in SQLite.
- Distills sessions into memory notes.
- Indexes distilled notes with deterministic local vectors.
- Supports policy propose/evaluate/promote/rollback lifecycle.
- Exposes everything as MCP tools.

## MCP tools

- `memory.append(session_id, role, content, metadata?)`
- `memory.distill(session_id, max_lines=6)`
- `memory.search(query, k=5)`
- `policy.get(active_version=true)`
- `policy.propose(delta_md, evidence_refs=[])`
- `policy.evaluate(proposal_id)`
- `policy.promote(proposal_id)`
- `policy.rollback(version_id)`

## Quickstart

```bash
cd <repo-root>
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Run server (stdio transport):

```bash
source .venv/bin/activate
agent-memory-mcp
```

Optional DB location:

```bash
export AGENT_MEMORY_DB=/absolute/path/to/agent_memory.db
agent-memory-mcp
```

## Example MCP workflow

1. Append raw events for a session.
2. Distill that session into a memory note.
3. Search memory notes for evidence.
4. Propose a policy delta with evidence refs.
5. Evaluate proposal.
6. Promote only if evaluation passes.
7. Roll back to a prior version if needed.

## Suggested next engineering upgrades

- Replace deterministic vectors with embedding provider + proper vector DB.
- Add signed policy bundles and a stricter evaluator harness.
- Add namespaces/ACLs for multi-tenant agent environments.
- Add offline replay evals with regression datasets.

## Publish as a public GitHub repo

```bash
cd <repo-root>
git add .
git commit -m "Initial MVP: memory+policy MCP server"
```

If GitHub CLI is authenticated:

```bash
gh repo create agent-memory-mcp --public --source=. --remote=origin --push
```

Or manual remote setup:

```bash
git remote add origin git@github.com:<your-username>/agent-memory-mcp.git
git branch -M main
git push -u origin main
```
