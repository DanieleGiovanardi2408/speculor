"""Pydantic request/response models. Mirrors docs/API.md exactly."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field, computed_field

from radar.pipeline.filters import POST_GRADUATION_ID


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; they are UTC. Serialize them with an offset."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


UTCDateTime = Annotated[datetime, AfterValidator(_as_utc)]

Priority = Literal["Alta", "Media", "Bassa"]
FilterState = Literal["pass", "flagged", "rejected", "overridden"]
PostingStatus = Literal["open", "closed", "unknown"]
UserStatus = Literal["todo", "sent", "skipped"]
DraftStatus = Literal["draft", "approved", "sent", "skipped", "failed"]
EditableDraftStatus = Literal["draft", "approved", "skipped"]
DraftKind = Literal["linkedin_connection_note", "linkedin_message", "followup", "email"]
DraftChannel = Literal["linkedin", "email", "other"]
Stage = Literal[
    "Inviata",
    "Screening HR",
    "Test/case",
    "Colloquio 1",
    "Colloquio 2+",
    "Offerta",
    "Rifiutata",
    "Nessuna risposta",
    "Ritirata",
]
ChangeKind = Literal["new", "changed", "closed", "reopened"]
RunStatus = Literal["running", "ok", "partial", "failed"]
PostingSort = Literal["priority", "deadline", "company", "first_seen"]
Track = Literal["internship", "post_graduation"]
TrackFilter = Literal["internship", "post_graduation", "all"]


# --- outputs -----------------------------------------------------------------------------
# Output enums are plain ``str`` so unexpected values already in the DB never cause a 500.


class Health(BaseModel):
    status: str = "ok"


class FilterHit(BaseModel):
    rule_id: str
    action: str
    reason: str
    evidence: str | None
    overridden: bool


class ScoreOut(BaseModel):
    priority: str
    why_fits: str
    what_doesnt_fit: str
    start_window: str | None
    language_requirements: list[str]
    warnings: list[str]
    confidence: float | None
    provider: str
    model: str
    created_at: UTCDateTime


class PostingSummary(BaseModel):
    id: int
    source: str
    url: str
    title: str
    company_name: str
    location: str | None
    country_code: str | None
    zone: str | None
    type: str | None
    area: str | None
    deadline: date | None
    start_info: str | None
    status: str
    user_status: str
    filter_state: str
    first_seen: UTCDateTime
    last_seen: UTCDateTime
    note: str | None
    priority: str | None
    score: ScoreOut | None
    filter_hits: list[FilterHit]
    has_pack: bool
    # Parsed from the "[Saltato: reason]" prefix of ``note`` (see routes/skip.py).
    skip_reason: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def track(self) -> Track:
        """``post_graduation`` when a non-overridden post_graduation hit exists."""
        if any(h.rule_id == POST_GRADUATION_ID and not h.overridden for h in self.filter_hits):
            return "post_graduation"
        return "internship"


class ChangeEventOut(BaseModel):
    kind: str
    changes: dict[str, list[Any]] | None
    at: UTCDateTime
    run_id: int | None


class PostingDetail(PostingSummary):
    raw_text: str | None
    via: str | None
    history: list[ChangeEventOut]
    pack_id: int | None


class ContactOut(BaseModel):
    id: int
    posting_id: int | None
    name: str
    role: str | None
    profile_url: str | None
    why: str | None
    source: str = "manual"  # manual | search
    source_query: str | None = None
    needs_review: bool = False


class ContactPatch(BaseModel):
    needs_review: bool | None = None


class ContactBudget(BaseModel):
    month_cost_eur: float
    monthly_cap_eur: float
    calls_today: int
    daily_call_cap: int


class ContactSearchOut(BaseModel):
    stored: int
    found: int
    dropped: dict[str, int] = Field(default_factory=dict)
    contacts: list[ContactOut]
    budget: ContactBudget


class DraftOut(BaseModel):
    id: int
    posting_id: int | None
    pack_id: int | None
    contact_id: int | None
    kind: str
    channel: str
    recipient: str | None
    text: str
    status: str
    style_issues: list[str]
    send_result: str | None
    created_at: UTCDateTime
    updated_at: UTCDateTime
    sent_at: UTCDateTime | None
    posting_title: str | None
    company_name: str | None
    contact_name: str | None


class FormAnswer(BaseModel):
    question: str
    answer: str


class PackOut(BaseModel):
    id: int
    posting_id: int
    cover_letter: str
    form_answers: list[FormAnswer]
    cv_version: str
    apply_link: str
    language: str
    style_issues: list[str]
    provider: str
    model: str
    updated_at: UTCDateTime
    posting: PostingSummary
    contacts: list[ContactOut]
    drafts: list[DraftOut]


class ApplicationOut(BaseModel):
    id: int
    posting_id: int | None
    company: str
    role: str
    city: str | None
    date_sent: date
    channel: str
    cv_version: str
    stage: str
    next_step: str | None
    next_step_date: date | None
    notes: str | None
    responded: bool
    updated_at: UTCDateTime
    followup_due: bool


class StatsRow(BaseModel):
    key: str
    sent: int
    responses: int
    interviews: int
    interview_rate: float


class AppStats(BaseModel):
    by_channel: list[StatsRow]
    by_cv: list[StatsRow]
    by_stage: dict[str, int]


class RunSummary(BaseModel):
    id: int
    started_at: UTCDateTime
    finished_at: UTCDateTime | None
    status: str
    stats: dict[str, Any] | None


class RunDetail(RunSummary):
    report_md: str | None


class UsageRow(BaseModel):
    day: date
    provider: str
    model: str
    purpose: str
    requests: int
    input_tokens: int
    output_tokens: int


class UsageTotal(BaseModel):
    provider: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int


class UsageOut(BaseModel):
    since: date
    until: date
    rows: list[UsageRow]
    today: list[UsageTotal]
    period: list[UsageTotal]


class Today(BaseModel):
    weekly_goal: int
    applications_this_week: int
    new_since_yesterday: list[PostingSummary]  # internship track only
    new_post_graduation: int = 0  # new postings on the post_graduation track (count only)
    closed_since_yesterday: list[PostingSummary]
    upcoming_deadlines: list[PostingSummary]
    followups_due: list[ApplicationOut]
    pending_approvals: int
    last_run: RunSummary | None


class CompanyOut(BaseModel):
    id: int
    name: str
    sector: str | None
    city: str | None
    careers_url: str | None
    program: str | None
    why: str | None
    notes: str | None
    priority: str | None
    ats: str | None
    ats_slug: str | None
    active: bool
    open_postings: int


class SettingsOut(BaseModel):
    weekly_goal: int
    sources_enabled: dict[str, bool]


# --- inputs ------------------------------------------------------------------------------


class PostingCreate(BaseModel):
    url: str = Field(min_length=8)


class PostingPatch(BaseModel):
    user_status: UserStatus | None = None
    status: PostingStatus | None = None
    note: str | None = None
    # Non-empty: user_status becomes "skipped" and the reason is stored as a note prefix.
    skip_reason: str | None = Field(default=None, max_length=200)


class MarkSent(BaseModel):
    channel: str = Field(min_length=1)
    cv_version: str = Field(min_length=1)
    date_sent: date | None = None


class PackPatch(BaseModel):
    cover_letter: str | None = None
    form_answers: list[FormAnswer] | None = None
    cv_version: str | None = None
    apply_link: str | None = None


class ContactCreate(BaseModel):
    posting_id: int
    name: str = Field(min_length=1)
    role: str | None = None
    profile_url: str | None = None
    why: str | None = None


class DraftCreate(BaseModel):
    posting_id: int
    contact_id: int | None = None
    kind: DraftKind
    channel: DraftChannel | None = None
    recipient: str | None = None
    text: str


class DraftPatch(BaseModel):
    text: str | None = None
    recipient: str | None = None
    status: EditableDraftStatus | None = None


class DraftForContact(BaseModel):
    posting_id: int
    contact_id: int
    kind: DraftKind


class ApplicationCreate(BaseModel):
    company: str = Field(min_length=1)
    role: str = Field(min_length=1)
    city: str | None = None
    date_sent: date
    channel: str = Field(min_length=1)
    cv_version: str = Field(min_length=1)
    stage: Stage = "Inviata"
    notes: str | None = None
    posting_id: int | None = None


class ApplicationPatch(BaseModel):
    company: str | None = None
    role: str | None = None
    city: str | None = None
    date_sent: date | None = None
    channel: str | None = None
    cv_version: str | None = None
    stage: Stage | None = None
    next_step: str | None = None
    next_step_date: date | None = None
    notes: str | None = None
    responded: bool | None = None
    posting_id: int | None = None


class CompanyPatch(BaseModel):
    notes: str | None = None
    priority: str | None = None
    active: bool | None = None


class SettingsIn(BaseModel):
    weekly_goal: int | None = Field(default=None, ge=0, le=1000)
    sources_enabled: dict[str, bool] | None = None
