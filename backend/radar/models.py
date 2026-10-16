"""Database schema. SQLite locally, Postgres (Supabase) in the cloud."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel

from radar.timeutil import utcnow

# Enumerations are kept as plain strings for portability; allowed values are listed here.
POSTING_STATUS = ("open", "closed", "unknown")
USER_STATUS = ("todo", "sent", "skipped")
FILTER_STATE = ("pass", "flagged", "rejected", "overridden")
PRIORITIES = ("Alta", "Media", "Bassa")
DRAFT_KINDS = ("linkedin_connection_note", "linkedin_message", "followup", "email")
DRAFT_STATUS = ("draft", "approved", "sent", "skipped", "failed")
APPLICATION_STAGES = (
    "Inviata",
    "Screening HR",
    "Test/case",
    "Colloquio 1",
    "Colloquio 2+",
    "Offerta",
    "Rifiutata",
    "Nessuna risposta",
    "Ritirata",
)
CHANGE_KINDS = ("new", "changed", "closed", "reopened")


def _json_col() -> Any:
    return Field(default=None, sa_column=Column(JSON))


class Company(SQLModel, table=True):
    __tablename__ = "companies"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    sector: str | None = None
    city: str | None = None
    careers_url: str | None = None
    program: str | None = None
    why: str | None = None
    notes: str | None = Field(default=None, sa_column=Column(Text))
    priority: str | None = None
    ats: str | None = None  # an ATS_SOURCES name (radar.connectors.registry)
    ats_slug: str | None = None
    ats_region: str | None = None  # "eu" for EU hosts (Lever, Greenhouse)
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class Posting(SQLModel, table=True):
    __tablename__ = "postings"

    id: int | None = Field(default=None, primary_key=True)
    uid: str = Field(index=True, unique=True)  # "<source>:<external_id>"
    source: str = Field(
        index=True
    )  # seed | greenhouse | lever | ashby | smartrecruiters | linkedin | manual
    external_id: str
    url: str
    canonical_url: str = Field(index=True)
    title: str
    company_name: str = Field(index=True)
    company_id: int | None = Field(default=None, foreign_key="companies.id")
    location: str | None = None
    country: str | None = None  # display name as found
    country_code: str | None = Field(default=None, index=True)  # ISO 3166-1 alpha-2, "EU", "REMOTE"
    zone: str | None = None  # Italia | Europa | Altro
    type: str | None = None  # Stage | Graduate | Lavoro
    area: str | None = None
    start_info: str | None = None
    deadline: date | None = None
    via: str | None = None  # how to apply (channel)
    raw_text: str | None = Field(default=None, sa_column=Column(Text))
    content_hash: str = ""
    dedupe_key: str = Field(default="", index=True)
    duplicate_of: int | None = Field(default=None, foreign_key="postings.id")
    # Primary rows only: every source that lists this job, [{uid, source, url, via, external_id,
    # first_seen, last_seen, listed}] (see pipeline/dedupe.py); NULL on duplicates.
    sources: list[dict[str, Any]] | None = _json_col()
    # uid of the member row whose (richer) description raw_text shows; NULL = its own.
    description_from: str | None = None
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    status: str = Field(default="open", index=True)
    closed_at: datetime | None = None
    filter_state: str = Field(default="pass", index=True)
    user_status: str = Field(default="todo", index=True)
    note: str | None = Field(default=None, sa_column=Column(Text))
    # Local embedding similarity to the profile (pipeline/rank.py), 0..1; None if not ranked.
    similarity: float | None = None
    similarity_version: str | None = None  # model + profile chunks hash it was computed with


class ChangeEvent(SQLModel, table=True):
    """Change log: one row per new/changed/closed/reopened posting per run (daily diff)."""

    __tablename__ = "change_events"

    id: int | None = Field(default=None, primary_key=True)
    posting_id: int = Field(foreign_key="postings.id", index=True)
    run_id: int | None = Field(default=None, foreign_key="runs.id", index=True)
    kind: str = Field(index=True)
    changes: dict[str, Any] | None = _json_col()  # {"field": [old, new]}
    at: datetime = Field(default_factory=utcnow, index=True)


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str = "running"  # running | ok | partial | failed
    stats: dict[str, Any] | None = _json_col()
    report_md: str | None = Field(default=None, sa_column=Column(Text))


class FilterResult(SQLModel, table=True):
    __tablename__ = "filter_results"

    id: int | None = Field(default=None, primary_key=True)
    posting_id: int = Field(foreign_key="postings.id", index=True)
    rule_id: str
    action: str  # reject | flag
    reason: str
    evidence: str | None = None
    overridden: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Score(SQLModel, table=True):
    __tablename__ = "scores"

    id: int | None = Field(default=None, primary_key=True)
    posting_id: int = Field(foreign_key="postings.id", index=True)
    content_hash: str = Field(index=True)
    priority: str
    why_fits: str
    what_doesnt_fit: str
    start_window: str | None = None
    language_requirements: list[str] | None = _json_col()
    warnings: list[str] | None = _json_col()
    confidence: float | None = None
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    # Version of the post-LLM guards (radar.llm.guards.GUARD_VERSION); None = scored before them.
    guard_version: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ApplicationPack(SQLModel, table=True):
    __tablename__ = "application_packs"

    id: int | None = Field(default=None, primary_key=True)
    posting_id: int = Field(foreign_key="postings.id", index=True, unique=True)
    content_hash: str = ""
    cover_letter: str = Field(default="", sa_column=Column(Text))
    form_answers: list[dict[str, str]] | None = _json_col()  # [{"question":..., "answer":...}]
    cv_version: str = "CV v1"
    apply_link: str = ""
    language: str = "en"
    style_issues: list[str] | None = _json_col()
    # Fact check of the texts against the fact bank (shape in radar.llm.verify).
    verification: dict[str, Any] | None = _json_col()
    provider: str = ""
    model: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Contact(SQLModel, table=True):
    __tablename__ = "contacts"

    id: int | None = Field(default=None, primary_key=True)
    posting_id: int | None = Field(default=None, foreign_key="postings.id", index=True)
    company_id: int | None = Field(default=None, foreign_key="companies.id")
    name: str
    role: str | None = None
    profile_url: str | None = None
    why: str | None = None
    # Provenance, not personal data: "manual" (typed by the user) or "search" (found by
    # radar.llm.contacts from public search results; may be wrong, so needs_review starts True).
    source: str = "manual"
    source_query: str | None = None
    needs_review: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Draft(SQLModel, table=True):
    __tablename__ = "drafts"

    id: int | None = Field(default=None, primary_key=True)
    posting_id: int | None = Field(default=None, foreign_key="postings.id", index=True)
    pack_id: int | None = Field(default=None, foreign_key="application_packs.id")
    contact_id: int | None = Field(default=None, foreign_key="contacts.id")
    kind: str
    channel: str = "linkedin"  # linkedin | email | other
    recipient: str | None = None  # LinkedIn public id / profile URL / email
    text: str = Field(default="", sa_column=Column(Text))
    status: str = Field(default="draft", index=True)
    style_issues: list[str] | None = _json_col()
    verification: dict[str, Any] | None = _json_col()  # see radar.llm.verify
    send_result: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    approved_at: datetime | None = None
    sent_at: datetime | None = None


class Application(SQLModel, table=True):
    __tablename__ = "applications"

    id: int | None = Field(default=None, primary_key=True)
    posting_id: int | None = Field(default=None, foreign_key="postings.id", index=True)
    company: str
    role: str
    city: str | None = None
    date_sent: date
    channel: str
    cv_version: str = "CV v1"
    stage: str = "Inviata"
    next_step: str | None = None
    next_step_date: date | None = None
    notes: str | None = Field(default=None, sa_column=Column(Text))
    responded: bool = False
    updated_at: datetime = Field(default_factory=utcnow)


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: Any = Field(default=None, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)


class LLMUsage(SQLModel, table=True):
    __tablename__ = "llm_usage"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int | None = Field(default=None, foreign_key="runs.id", index=True)
    provider: str
    model: str
    purpose: str  # scoring | pack | draft | contacts | verify
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0  # server-side tool calls (web searches, page opens)
    est_cost_eur: float = 0.0  # estimated from published prices (radar.llm.contacts)
    at: datetime = Field(default_factory=utcnow)
