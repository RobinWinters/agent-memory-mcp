from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_memory_mcp.db_audit import DatabaseAuditMixin
from agent_memory_mcp.db_jobs import DatabaseJobsMixin
from agent_memory_mcp.db_memory import DatabaseMemoryMixin
from agent_memory_mcp.db_policy import DatabasePolicyMixin
from agent_memory_mcp.db_schema import DatabaseSchemaMixin


class Database(
    DatabaseSchemaMixin,
    DatabaseMemoryMixin,
    DatabasePolicyMixin,
    DatabaseJobsMixin,
    DatabaseAuditMixin,
):
    def __init__(self, db_path: str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()
