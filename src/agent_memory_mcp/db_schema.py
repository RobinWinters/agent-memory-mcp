from __future__ import annotations

NAMESPACED_TABLES = [
    "sessions",
    "events",
    "memories",
    "policy_proposals",
    "policy_evaluations",
    "policy_versions",
    "jobs",
]


class DatabaseSchemaMixin:
    def _ensure_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL DEFAULT 'default',
                started_at TEXT NOT NULL,
                ended_at TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL DEFAULT 'default',
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL DEFAULT 'default',
                session_id TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_proposals (
                proposal_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL DEFAULT 'default',
                delta_md TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL DEFAULT 'default',
                proposal_id TEXT NOT NULL,
                score REAL NOT NULL,
                passed INTEGER NOT NULL,
                report TEXT NOT NULL,
                checks_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_versions (
                version_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL DEFAULT 'default',
                content_md TEXT NOT NULL,
                content_sha256 TEXT NOT NULL DEFAULT '',
                signature TEXT,
                signing_method TEXT NOT NULL DEFAULT 'none',
                source_proposal_id TEXT,
                is_active INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL DEFAULT 'default',
                event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL DEFAULT 'default',
                job_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error_text TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                next_run_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
            """
        )

        self._migrate_existing_tables()

        self.conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_sessions_namespace ON sessions(namespace, session_id);
            CREATE INDEX IF NOT EXISTS idx_events_ns_session ON events(namespace, session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_memories_ns_session ON memories(namespace, session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_policy_prop_ns ON policy_proposals(namespace, proposal_id);
            CREATE INDEX IF NOT EXISTS idx_policy_eval_ns_proposal ON policy_evaluations(namespace, proposal_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_policy_ver_ns_active ON policy_versions(namespace, is_active, created_at);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_ns_id ON audit_logs(namespace, id);
            CREATE INDEX IF NOT EXISTS idx_jobs_ns_status_created ON jobs(namespace, status, created_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_ns_status_next_run ON jobs(namespace, status, next_run_at, id);
            """
        )
        self.conn.commit()

    def _migrate_existing_tables(self) -> None:
        for table in NAMESPACED_TABLES:
            if not self._table_has_column(table, "namespace"):
                self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN namespace TEXT NOT NULL DEFAULT 'default'"
                )

        if not self._table_has_column("policy_evaluations", "checks_json"):
            self.conn.execute(
                "ALTER TABLE policy_evaluations ADD COLUMN checks_json TEXT NOT NULL DEFAULT '[]'"
            )

        if not self._table_has_column("jobs", "attempt_count"):
            self.conn.execute("ALTER TABLE jobs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")

        if not self._table_has_column("jobs", "max_attempts"):
            self.conn.execute("ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3")

        if not self._table_has_column("jobs", "next_run_at"):
            self.conn.execute("ALTER TABLE jobs ADD COLUMN next_run_at TEXT")
            self.conn.execute(
                """
                UPDATE jobs
                SET next_run_at = COALESCE(created_at, updated_at)
                WHERE next_run_at IS NULL OR next_run_at = ''
                """
            )

        if not self._table_has_column("policy_versions", "content_sha256"):
            self.conn.execute("ALTER TABLE policy_versions ADD COLUMN content_sha256 TEXT NOT NULL DEFAULT ''")
            self.conn.execute(
                """
                UPDATE policy_versions
                SET content_sha256 = ''
                WHERE content_sha256 IS NULL
                """
            )

        if not self._table_has_column("policy_versions", "signature"):
            self.conn.execute("ALTER TABLE policy_versions ADD COLUMN signature TEXT")

        if not self._table_has_column("policy_versions", "signing_method"):
            self.conn.execute("ALTER TABLE policy_versions ADD COLUMN signing_method TEXT NOT NULL DEFAULT 'none'")

        self.conn.commit()

    def _table_has_column(self, table_name: str, column_name: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(row["name"] == column_name for row in rows)
