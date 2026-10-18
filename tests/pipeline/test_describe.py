from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pipeline_fakes import raw
from sqlmodel import Session, select

from radar.llm.scoring import pending_postings
from radar.models import Posting, Score
from radar.pipeline.describe import (
    FETCHED,
    UNAVAILABLE,
    Described,
    describe_postings,
    has_description,
    missing_descriptions,
)
from radar.pipeline.store import upsert_raw

T0 = datetime(2026, 10, 18, 5, 0, tzinfo=UTC)
LONG = "Join the data team to build forecasting models in Python and SQL. " * 10


def _seed(session: Session, ext: str, url: str, **kw: object) -> Posting:
    p = upsert_raw(
        session,
        raw(ext, source="seed", url=url, raw_text="Tracker note: AI, Roma.", **kw),
        now=T0,
    ).posting
    session.commit()
    return p


def _llm_score(session: Session, p: Posting) -> None:
    assert p.id is not None
    session.add(
        Score(
            posting_id=p.id,
            content_hash=p.content_hash,
            priority="Alta",
            why_fits="x",
            what_doesnt_fit="",
            provider="groq",
            model="m",
        )
    )
    session.commit()


def test_seed_without_description_is_never_scored(session: Session) -> None:
    seed = _seed(session, "s1", "https://careers.acme.example/jobs/1")
    gh = upsert_raw(session, raw("g1", raw_text=LONG), now=T0).posting
    session.commit()
    assert not has_description(seed) and has_description(gh)
    assert [p.id for p in pending_postings(session, 10)] == [gh.id]

    seed.description_status = FETCHED
    session.add(seed)
    session.commit()
    assert {p.id for p in pending_postings(session, 10)} == {gh.id, seed.id}


def test_describe_fetches_text_and_drops_title_only_scores(session: Session) -> None:
    seed = _seed(session, "s1", "https://careers.acme.example/jobs/1")
    _llm_score(session, seed)
    old_hash = seed.content_hash
    calls: list[str] = []

    def describer(url: str) -> Described:
        calls.append(url)
        return Described(raw=raw("x", raw_text=LONG))

    stats = describe_postings(session, describer, cap=5, now=T0)
    assert calls == ["https://careers.acme.example/jobs/1"]
    assert (stats["fetched"], stats["scores_dropped"]) == (1, 1)
    assert stats["touched"] == [seed.id]
    session.refresh(seed)
    assert seed.description_status == FETCHED
    assert seed.raw_text and seed.raw_text.startswith("Join the data team")
    assert seed.content_hash != old_hash
    assert session.exec(select(Score)).all() == []
    assert [p.id for p in pending_postings(session, 10)] == [seed.id]


def test_unavailable_page_keeps_posting_unscored_and_retries_after_a_week(
    session: Session,
) -> None:
    seed = _seed(session, "s1", "https://careers.acme.example/jobs/1")
    _llm_score(session, seed)  # scored on the title alone before this step existed

    stats = describe_postings(session, lambda url: Described(reason="robots"), now=T0)
    assert stats["unavailable"] == 1 and stats["reasons"] == {"robots": 1}
    assert stats["dropped_unavailable"] == 1
    session.refresh(seed)
    assert seed.description_status == UNAVAILABLE
    assert session.exec(select(Score)).all() == []
    assert pending_postings(session, 10) == []

    assert missing_descriptions(session, T0 + timedelta(days=6)) == []
    assert [p.id for p in missing_descriptions(session, T0 + timedelta(days=7))] == [seed.id]


def test_snippet_does_not_count_as_description(session: Session) -> None:
    seed = _seed(session, "s1", "https://careers.acme.example/jobs/1")
    describe_postings(session, lambda url: Described(raw=raw("x", raw_text="Short.")), now=T0)
    session.refresh(seed)
    assert seed.description_status == UNAVAILABLE


def test_closed_page_closes_the_posting(session: Session) -> None:
    seed = _seed(session, "s1", "https://careers.acme.example/jobs/1")
    stats = describe_postings(session, lambda url: Described(closed=True), now=T0)
    session.refresh(seed)
    assert stats["closed"] == 1 and seed.status == "closed"


def test_linkedin_and_jobteaser_pages_are_left_to_their_sources(session: Session) -> None:
    li = _seed(session, "s1", "https://it.linkedin.com/jobs/view/data-intern-at-acme-4012345678")
    jt = _seed(session, "s2", "https://luiss.jobteaser.com/it/job-offers/abc-acme-intern")
    calls: list[str] = []
    stats = describe_postings(session, lambda url: calls.append(url) or Described(), now=T0)
    assert calls == [] and stats["candidates"] == 0
    assert [p.id for p in missing_descriptions(session, T0, channel="linkedin")] == [li.id]
    assert [p.id for p in missing_descriptions(session, T0, channel="jobteaser")] == [jt.id]


def test_borrowed_description_counts(session: Session) -> None:
    seed = _seed(session, "s1", "https://careers.acme.example/jobs/1")
    seed.description_from = "greenhouse:g1"
    seed.raw_text = LONG
    assert has_description(seed)
