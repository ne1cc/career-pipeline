from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class FunnelState(StrEnum):
    QUEUED = "QUEUED"
    APPLIED = "APPLIED"
    SKIPPED = "SKIPPED"
    REFERRAL_HOLD = "REFERRAL_HOLD"
    REFERRAL_STALE = "REFERRAL_STALE"
    OA_RECEIVED = "OA_RECEIVED"
    PHONE_SCREEN = "PHONE_SCREEN"
    REJECTED = "REJECTED"


class ResumeArchetype(StrEnum):
    # Order is the tie-break order when keyword scores are equal.
    SWE_BACKEND = "swe_backend"
    DATA_ENG = "data_eng"
    ANALYTICS_MSBA = "analytics_msba"


class JobRow(BaseModel):
    dedup_key: str
    jobspy_id: str | None
    site: str | None
    company: str
    company_norm: str
    title: str
    location: str | None
    is_remote: bool = False
    apply_url: str
    description: str | None
    date_posted: str | None
    priority_score: float
    resume_archetype: ResumeArchetype
    resume_path: str
