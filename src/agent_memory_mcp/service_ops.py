from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from agent_memory_mcp.integrity import compute_audit_event_hash, verify_policy_artifact
from agent_memory_mcp.metrics_export import build_otel_json, render_prometheus_text
from agent_memory_mcp.models import utc_now_iso


class ServiceOpsMixin:
    def ops_health(self, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        cutoff_dt = now_dt - timedelta(
            seconds=self._coerce_positive_float(self.job_running_timeout_seconds, default=300.0)
        )
        health = self.db.get_job_queue_health(
            namespace=ns,
            now=now_iso,
            running_cutoff_started_at=cutoff_dt.isoformat(),
        )
        return {
            "namespace": ns,
            "generated_at": now_iso,
            "job_running_timeout_seconds": self.job_running_timeout_seconds,
            "queue": health,
        }

    def ops_metrics(self, window_minutes: int = 60, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        resolved_window_minutes = self._coerce_positive_int(window_minutes, default=60)
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        since_dt = now_dt - timedelta(minutes=resolved_window_minutes)

        metrics = self.db.get_job_metrics_window(
            namespace=ns,
            since=since_dt.isoformat(),
            now=now_iso,
        )
        health = self.db.get_job_queue_health(
            namespace=ns,
            now=now_iso,
            running_cutoff_started_at=(
                now_dt
                - timedelta(
                    seconds=self._coerce_positive_float(self.job_running_timeout_seconds, default=300.0)
                )
            ).isoformat(),
        )
        return {
            "namespace": ns,
            "generated_at": now_iso,
            "window_minutes": resolved_window_minutes,
            "jobs": metrics,
            "queue": health,
        }

    def ops_metrics_prometheus(self, window_minutes: int = 60, namespace: str | None = None) -> dict:
        snapshot = self.ops_metrics(window_minutes=window_minutes, namespace=namespace)
        text = render_prometheus_text(snapshot=snapshot)
        return {
            "namespace": snapshot["namespace"],
            "generated_at": snapshot["generated_at"],
            "window_minutes": snapshot["window_minutes"],
            "format": "prometheus_text",
            "text": text,
        }

    def ops_metrics_otel(self, window_minutes: int = 60, namespace: str | None = None) -> dict:
        snapshot = self.ops_metrics(window_minutes=window_minutes, namespace=namespace)
        payload = build_otel_json(snapshot=snapshot)
        return {
            "namespace": snapshot["namespace"],
            "generated_at": snapshot["generated_at"],
            "window_minutes": snapshot["window_minutes"],
            "format": "otel_json",
            "payload": payload,
        }

    def ops_audit_recent(self, limit: int = 50, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        resolved_limit = self._coerce_positive_int(limit, default=50)
        entries = self.db.list_audit_logs(namespace=ns, limit=resolved_limit, ascending=False)
        return {
            "namespace": ns,
            "generated_at": utc_now_iso(),
            "count": len(entries),
            "entries": entries,
        }

    def ops_audit_verify(self, limit: int = 1000, namespace: str | None = None) -> dict:
        ns = self._ns(namespace)
        resolved_limit = self._coerce_positive_int(limit, default=1000)
        entries = self.db.list_audit_logs(namespace=ns, limit=resolved_limit, ascending=True)
        audit_secrets = self._resolve_audit_verification_secrets()

        failures: list[dict[str, Any]] = []
        prev_event_hash: str | None = None
        for entry in entries:
            expected_hashes = [
                compute_audit_event_hash(
                    namespace=ns,
                    event_type=str(entry["event_type"]),
                    entity_type=str(entry["entity_type"]),
                    entity_id=str(entry["entity_id"]),
                    payload=dict(entry["payload"]),
                    created_at=str(entry["created_at"]),
                    prev_hash=str(entry["prev_hash"]),
                    audit_secret=secret,
                )
                for secret in audit_secrets
            ]
            if not audit_secrets:
                expected_hashes = [
                    compute_audit_event_hash(
                        namespace=ns,
                        event_type=str(entry["event_type"]),
                        entity_type=str(entry["entity_type"]),
                        entity_id=str(entry["entity_id"]),
                        payload=dict(entry["payload"]),
                        created_at=str(entry["created_at"]),
                        prev_hash=str(entry["prev_hash"]),
                        audit_secret=None,
                    )
                ]
            if str(entry["event_hash"]) not in expected_hashes:
                failures.append(
                    {
                        "id": entry["id"],
                        "reason": "event_hash_mismatch",
                    }
                )

            if prev_event_hash is not None and str(entry["prev_hash"]) != prev_event_hash:
                failures.append(
                    {
                        "id": entry["id"],
                        "reason": "prev_hash_chain_break",
                    }
                )
            prev_event_hash = str(entry["event_hash"])

        versions = self.db.list_policy_versions(namespace=ns, limit=resolved_limit)
        bad_versions: list[str] = []
        policy_secrets = self._resolve_policy_verification_secrets()
        for version in versions:
            ok = verify_policy_artifact(
                namespace=ns,
                version_id=str(version["version_id"]),
                content_md=str(version["content_md"]),
                created_at=str(version["created_at"]),
                content_sha256=str(version["content_sha256"]),
                signature=version["signature"],
                signing_method=str(version["signing_method"]),
                signing_secret=self.policy_signing_secret,
                signing_secrets=policy_secrets,
            )
            if not ok:
                bad_versions.append(str(version["version_id"]))

        verified = len(failures) == 0 and len(bad_versions) == 0
        return {
            "namespace": ns,
            "generated_at": utc_now_iso(),
            "verified": verified,
            "audit_events_checked": len(entries),
            "policy_versions_checked": len(versions),
            "audit_failures": failures,
            "invalid_policy_versions": bad_versions,
        }
