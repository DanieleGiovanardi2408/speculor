"""Shared value objects passed between connectors and the pipeline."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class RawPosting(BaseModel):
    """One posting as returned by a connector, before normalization."""

    source: str  # connector name, e.g. "greenhouse"
    external_id: str  # stable id within the source
    url: str  # public posting / apply URL
    title: str
    company: str
    location: str | None = None
    country: str | None = None  # country name or ISO code if the source gives one
    raw_text: str = ""  # plain text description (HTML stripped)
    department: str | None = None
    employment_type: str | None = None
    posted_at: datetime | None = None
    deadline: date | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
