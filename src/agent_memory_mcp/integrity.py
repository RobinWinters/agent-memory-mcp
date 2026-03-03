from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_policy_artifact(
    *,
    namespace: str,
    version_id: str,
    content_md: str,
    created_at: str,
    signing_secret: str | None,
) -> dict[str, Any]:
    content_sha256 = sha256_hex(content_md)
    if signing_secret:
        message = "|".join([namespace, version_id, content_sha256, created_at])
        signature = hmac.new(
            signing_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signing_method = "hmac-sha256"
    else:
        signature = None
        signing_method = "none"

    return {
        "content_sha256": content_sha256,
        "signature": signature,
        "signing_method": signing_method,
    }


def verify_policy_artifact(
    *,
    namespace: str,
    version_id: str,
    content_md: str,
    created_at: str,
    content_sha256: str,
    signature: str | None,
    signing_method: str,
    signing_secret: str | None,
) -> bool:
    if sha256_hex(content_md) != content_sha256:
        return False

    if signing_method == "none":
        return True

    if signing_method != "hmac-sha256" or not signing_secret or not signature:
        return False

    expected = build_policy_artifact(
        namespace=namespace,
        version_id=version_id,
        content_md=content_md,
        created_at=created_at,
        signing_secret=signing_secret,
    )
    expected_sig = str(expected["signature"] or "")
    return hmac.compare_digest(expected_sig, signature)


def compute_audit_event_hash(
    *,
    namespace: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any],
    created_at: str,
    prev_hash: str,
    audit_secret: str | None,
) -> str:
    canonical_payload = _canonical_json(payload)
    message = "|".join(
        [
            namespace,
            event_type,
            entity_type,
            entity_id,
            canonical_payload,
            created_at,
            prev_hash,
        ]
    )

    if audit_secret:
        return hmac.new(
            audit_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    return sha256_hex(message)
