from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContactRow(BaseModel):
    row_id: int = Field(ge=1)
    source_row: int = Field(ge=1)
    name: str | None = None
    organization: str | None = None
    title: str | None = None
    email: str | None = None
    location: str | None = None
    city: str | None = None
    state: str | None = None
    linkedin_title: str | None = None
    linkedin_snippet: str | None = None
    organization_website: str | None = None
    initial_linkedin_url: str | None = None
    initial_lookup_status: str = "pending"
    initial_validation_note: str | None = None
    initial_match_confidence: int = 0
    initial_flagged: bool = False
    initial_flag_reason: str | None = None
    original_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "name",
        "organization",
        "title",
        "email",
        "location",
        "city",
        "state",
        "linkedin_title",
        "linkedin_snippet",
        "organization_website",
        "initial_linkedin_url",
        "initial_validation_note",
        "initial_flag_reason",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split()).strip()
        return text or None
