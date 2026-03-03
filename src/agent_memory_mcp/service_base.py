from __future__ import annotations

from typing import Any


class ServiceBaseMixin:
    def _ns(self, namespace: str | None) -> str:
        if namespace and namespace.strip():
            return namespace.strip()
        return self.default_namespace

    @staticmethod
    def _coerce_positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return parsed if parsed > 0 else default

    @staticmethod
    def _coerce_positive_float(value: Any, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return parsed if parsed > 0 else default

    @staticmethod
    def _dedupe_secrets(values: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            clean = value.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            ordered.append(clean)
        return tuple(ordered)

    def _resolve_policy_verification_secrets(self) -> tuple[str, ...]:
        candidates = list(self.policy_verification_secrets)
        if self.policy_signing_secret:
            candidates.append(self.policy_signing_secret)
        return self._dedupe_secrets(candidates)

    def _resolve_audit_verification_secrets(self) -> tuple[str, ...]:
        candidates = list(self.audit_verification_secrets)
        if self.audit_signing_secret:
            candidates.append(self.audit_signing_secret)
        return self._dedupe_secrets(candidates)

    def update_signing_keys(
        self,
        *,
        policy_active_secret: str | None,
        audit_active_secret: str | None,
        policy_verification_secrets: tuple[str, ...] = (),
        audit_verification_secrets: tuple[str, ...] = (),
    ) -> None:
        self.policy_signing_secret = policy_active_secret
        self.audit_signing_secret = audit_active_secret
        self.policy_verification_secrets = self._dedupe_secrets(list(policy_verification_secrets))
        self.audit_verification_secrets = self._dedupe_secrets(list(audit_verification_secrets))
