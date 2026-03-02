from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class SessionEvent(BaseModel):
    session_id: str
    role: str
    content: str
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryNote(BaseModel):
    session_id: str
    content: str
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyProposal(BaseModel):
    proposal_id: str
    delta_md: str
    evidence_refs: list[str] = Field(default_factory=list)
    status: str = "proposed"
    created_at: str = Field(default_factory=utc_now_iso)


class PolicyEvaluation(BaseModel):
    proposal_id: str
    score: float
    passed: bool
    report: str
    created_at: str = Field(default_factory=utc_now_iso)


class PolicyVersion(BaseModel):
    version_id: str
    content_md: str
    source_proposal_id: str | None = None
    is_active: bool = False
    created_at: str = Field(default_factory=utc_now_iso)


class MemorySearchResult(BaseModel):
    memory_id: int
    session_id: str
    score: float
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
