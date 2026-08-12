from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


SourceType = Literal[
    "linkedin_existing",
    "official_staff_page",
    "official_department_page",
    "official_news",
    "official_minutes",
    "official_agenda",
    "official_budget",
    "official_plan",
    "government_directory",
    "organization_website",
    "web_search",
    "facebook_search",
    "youtube_search",
    "linkedin_search",
    "news_search",
    "other_public_source",
]


class EvidenceItem(BaseModel):
    evidence_type: str
    source_url: str
    source_title: str | None = None
    extracted_text: str | None = None
    source_type: SourceType = "other_public_source"
    confidence: float = Field(ge=0.0, le=1.0)
    published_date: date | None = None

    @field_validator("source_url", "source_title", "extracted_text", mode="before")
    @classmethod
    def clean_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split()).strip()
        return text or None


class ContactInput(BaseModel):
    row_id: int | None = None
    source_row: int | None = None
    name: str
    email: str | None = None
    organization: str | None = None
    linkedin_url: str | None = None
    linkedin_title: str | None = None
    linkedin_snippet: str | None = None
    existing_city: str | None = None
    existing_state: str | None = None
    existing_job_title: str | None = None
    organization_website: str | None = None
    original_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "name",
        "email",
        "organization",
        "linkedin_url",
        "linkedin_title",
        "linkedin_snippet",
        "existing_city",
        "existing_state",
        "existing_job_title",
        "organization_website",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split()).strip()
        return text or None


class ContactEnrichmentResult(BaseModel):
    name: str
    organization: str | None = None
    linkedin_url: str | None = None
    role: str | None = None
    background_summary: str | None = None
    city: str | None = None
    state: str | None = None
    department_raw: str | None = None
    department_bucket: str | None = None
    sub_function: str | None = None
    local_context: str | None = None
    organization_website: str | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    identity_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    role_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    organization_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    location_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    department_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sub_function_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    local_context_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    profile_complete: bool = False
    manual_review: bool = True
    review_reason: str | None = None
    status: Literal["completed", "manual_review", "failed"] = "manual_review"
    verification_method: str | None = None


class BatchEnrichmentSummary(BaseModel):
    batch_id: str
    status: Literal["idle", "running", "complete", "stopped", "failed"]
    total: int = 0
    processed: int = 0
    completed: int = 0
    manual_review: int = 0
    failed: int = 0
    current_row: int | None = None
    current_name: str | None = None
    stage: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    last_error: str | None = None
