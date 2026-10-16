from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pipeline_fakes import raw
from sqlmodel import Session, select

from radar.models import ChangeEvent, Company, Posting
from radar.pipeline.normalize import canonical_url
from radar.pipeline.store import CONTENT_CHANGED, upsert_raw

T0 = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=1)


def events(session: Session, posting_id: int) -> list[ChangeEvent]:
    return list(session.exec(select(ChangeEvent).where(ChangeEvent.posting_id == posting_id)).all())


def test_new_posting(session: Session) -> None:
    session.add(Company(name="Acme S.p.A."))
    session.flush()
    out = upsert_raw(session, raw("1"), run_id=None, now=T0)
    p = out.posting
    assert out.kind == "new" and not out.is_duplicate
    assert p.uid == "greenhouse:1" and p.status == "open"
    assert p.country_code == "IT" and p.zone == "Italia" and p.type == "Stage"
    assert p.company_id is not None  # linked by normalized name
    assert [e.kind for e in events(session, p.id or 0)] == ["new"]


def test_unchanged_updates_last_seen(session: Session) -> None:
    upsert_raw(session, raw("1"), now=T0)
    out = upsert_raw(session, raw("1"), now=T1)
    assert out.kind == "unchanged"
    assert out.posting.last_seen.replace(tzinfo=UTC) == T1
    assert len(events(session, out.posting.id or 0)) == 1


def test_changes_recorded_without_text(session: Session) -> None:
    upsert_raw(session, raw("1"), now=T0)
    out = upsert_raw(
        session,
        raw("1", title="Data Science Intern (new)", raw_text="Totally new description"),
        now=T1,
    )
    assert out.kind == "changed"
    ev = next(e for e in events(session, out.posting.id or 0) if e.kind == "changed")
    assert ev.changes is not None
    assert ev.changes["title"] == ["Data Science Intern 1", "Data Science Intern (new)"]
    assert ev.changes["raw_text"] == [None, CONTENT_CHANGED]
    assert "Totally" not in str(ev.changes)


def test_reopened(session: Session) -> None:
    p = upsert_raw(session, raw("1"), now=T0).posting
    p.status = "closed"
    p.closed_at = T0
    session.add(p)
    session.flush()
    out = upsert_raw(session, raw("1"), now=T1)
    assert out.kind == "reopened" and out.posting.status == "open"
    assert out.posting.closed_at is None
    assert "reopened" in [e.kind for e in events(session, p.id or 0)]


def test_dedupe_same_url_keeps_first_id_and_takes_ats_link(session: Session) -> None:
    li = upsert_raw(
        session,
        raw(
            "li1", source="linkedin", url="https://it.linkedin.com/jobs/view/data-intern-123?trk=x"
        ),
        now=T0,
    ).posting
    li2 = upsert_raw(
        session,
        raw("li2", source="linkedin", url="https://www.linkedin.com/jobs/view/123"),
        now=T0,
    ).posting
    assert li2.duplicate_of == li.id  # same canonical URL
    gh = upsert_raw(
        session, raw("9", url="https://www.linkedin.com/jobs/view/123/"), now=T1
    ).posting
    session.refresh(li)
    # the better source arrives later: the original id stays primary, the others point at it
    assert li.duplicate_of is None and gh.duplicate_of == li.id and li2.duplicate_of == li.id
    assert {e["uid"] for e in li.sources or []} == {"linkedin:li1", "linkedin:li2", "greenhouse:9"}


def test_dedupe_key_across_sources(session: Session) -> None:
    seed = upsert_raw(
        session,
        raw("s1", source="seed", url="https://acme.example/jobs/1", title="AI Engineer Intern"),
        now=T0,
    ).posting
    gh = upsert_raw(
        session, raw("77", title="AI Engineer Intern (m/f/d)", company="Acme SpA"), now=T1
    )
    assert gh.is_duplicate and gh.posting.duplicate_of == seed.id
    # a second board job with the same title is another opening, not the same job
    other = upsert_raw(session, raw("78", title="AI Engineer Intern"), now=T1)
    assert other.posting.duplicate_of is None
    # same key within one source is NOT a duplicate (different jobs, same title)
    same_src = upsert_raw(
        session,
        raw("x1", source="lever", url="https://jobs.lever.co/a/1", title="Ops Intern"),
        now=T1,
    )
    same_src2 = upsert_raw(
        session,
        raw("x2", source="lever", url="https://jobs.lever.co/a/2", title="Ops Intern"),
        now=T1,
    )
    assert same_src.posting.duplicate_of is None and same_src2.posting.duplicate_of is None


def test_duplicate_reopens_closed_primary(session: Session) -> None:
    seed = upsert_raw(
        session, raw("s1", source="seed", url="https://acme.example/jobs/1"), now=T0
    ).posting
    seed.status = "closed"
    session.add(seed)
    session.flush()
    upsert_raw(session, raw("5", source="manual", url="https://acme.example/jobs/1"), now=T1)
    session.refresh(seed)
    assert seed.status == "open"
    kinds = [e.kind for e in events(session, seed.id or 0)]
    assert "reopened" in kinds
    assert session.exec(select(Posting)).all()


JT_ID = "0a1b2c3d-1111-4a2a-9f1d-00000000abcd"


def test_jobteaser_connector_dedupes_with_alert_and_legacy_seed(session: Session) -> None:
    # A seed row with an old-style link (locale/slug); its canonical URL is the shared one
    # (rows stored before the change get it from `radar dedupe backfill`).
    seed = Posting(
        uid="seed:x",
        source="seed",
        external_id="x",
        url=f"https://www.jobteaser.com/job-offers/{JT_ID}-acme-data-intern",
        canonical_url=canonical_url(
            f"https://www.jobteaser.com/job-offers/{JT_ID}-acme-data-intern"
        ),
        title="Data Intern",
        company_name="Acme",
        content_hash="h",
        dedupe_key="acme|data intern|",
        first_seen=T0,
        last_seen=T0,
    )
    session.add(seed)
    session.flush()
    alert = upsert_raw(
        session,
        raw(
            f"jobteaser:{JT_ID}",
            source="email",
            url=f"https://luiss.jobteaser.com/it/job-offers/{JT_ID}-acme-data-intern",
            extra={"via": "jobteaser_alert"},
        ),
        now=T0,
    ).posting
    conn = upsert_raw(
        session,
        raw(JT_ID, source="jobteaser", url=f"https://luiss.jobteaser.com/it/job-offers/{JT_ID}"),
        now=T1,
    ).posting
    assert alert.canonical_url == conn.canonical_url
    assert alert.duplicate_of == seed.id and conn.duplicate_of == seed.id
    assert conn.via == alert.via == "Career Service Luiss"


def test_jobteaser_connector_joins_its_alert_keeping_the_primary_id(session: Session) -> None:
    alert = upsert_raw(
        session,
        raw(
            f"jobteaser:{JT_ID}",
            source="email",
            url=f"https://luiss.jobteaser.com/it/job-offers/{JT_ID}-x",
            extra={"via": "jobteaser_alert"},
        ),
        now=T0,
    ).posting
    conn = upsert_raw(
        session,
        raw(JT_ID, source="jobteaser", url=f"https://luiss.jobteaser.com/it/job-offers/{JT_ID}"),
        now=T1,
    ).posting
    # stable ids: the alert seen first stays the primary, the offer page joins its group
    assert alert.duplicate_of is None and conn.duplicate_of == alert.id
