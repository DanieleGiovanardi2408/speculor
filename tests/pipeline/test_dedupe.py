"""Cross-source de-duplication: matching, stable primaries, sources, closing, backfill."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pipeline_fakes import raw
from sqlmodel import Session, select

from radar.models import ApplicationPack, ChangeEvent, Posting
from radar.pipeline import dedupe
from radar.pipeline.closed import check_open_postings, close_missing
from radar.pipeline.dedupe import backfill, public_sources, source_rank
from radar.pipeline.normalize import (
    canonical_url,
    company_key,
    dedupe_key,
    normalize_company,
    title_similarity,
    title_tokens,
)
from radar.pipeline.store import upsert_raw
from radar.schemas import RawPosting

T0 = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=1)
T2 = T0 + timedelta(days=2)
LI_ID = "4099887766"
BOARD_TEXT = "About the role. " + "You will build models in Python and SQL. " * 20


def li_alert(job_id: str = LI_ID, **kw: Any) -> RawPosting:
    """A LinkedIn job-alert email card (as the email connector emits it)."""
    data: dict[str, Any] = {
        "source": "email",
        "external_id": f"linkedin:{job_id}",
        "url": f"https://www.linkedin.com/jobs/view/{job_id}",
        "title": "Data Scientist Intern",
        "company": "Acme",
        "location": "Milan, Lombardy, Italy",
        "raw_text": "Data Scientist Intern\nAcme · Milan",
        "extra": {"via": "linkedin_alert", "linkedin_job_id": job_id},
    }
    data.update(kw)
    return RawPosting(**data)


def li_job(job_id: str = LI_ID, **kw: Any) -> RawPosting:
    """A posting from the linkedin_jobs connector."""
    data: dict[str, Any] = {
        "source": "linkedin_jobs",
        "external_id": job_id,
        "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
        "title": "Data Scientist Intern",
        "company": "Acme S.p.A.",
        "location": "Milano, Lombardia, Italia",
        "raw_text": "Short LinkedIn description.",
    }
    data.update(kw)
    return RawPosting(**data)


def board(external_id: str = "501", **kw: Any) -> RawPosting:
    kw.setdefault("title", "Data Scientist Intern")
    kw.setdefault("raw_text", BOARD_TEXT)
    return raw(external_id, **kw)


def jobteaser(offer: str = "3f2a1b4c-1111-2222-3333-444455556666", **kw: Any) -> RawPosting:
    data: dict[str, Any] = {
        "source": "jobteaser",
        "external_id": offer,
        "url": f"https://www.jobteaser.com/job-offers/{offer}-data-scientist-intern",
        "title": "Stage - Data Scientist (6 mesi)",
        "company": "ACME SpA",
        "location": "Milano",
        "raw_text": "JobTeaser offer text.",
    }
    data.update(kw)
    return RawPosting(**data)


def primaries(session: Session) -> list[Posting]:
    return list(session.exec(select(Posting).where(Posting.duplicate_of == None)).all())  # noqa: E711


def sources_of(p: Posting) -> set[str]:
    return {e["source"] for e in p.sources or []}


# --- normalization ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.linkedin.com/comm/jobs/view/{LI_ID}/?trackingId=abc%3D&refId=xyz",
        f"https://it.linkedin.com/jobs/view/data-scientist-intern-at-acme-{LI_ID}?trk=public",
        f"https://www.linkedin.com/jobs/search/?currentJobId={LI_ID}&geoId=103350119",
        f"https://www.linkedin.com/jobs/collections/recommended/?currentJobId={LI_ID}",
        f"https://www.linkedin.com/jobs/view/{LI_ID}/",
        f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{LI_ID}",
    ],
)
def test_canonical_url_linkedin_variants(url: str) -> None:
    assert canonical_url(url) == f"https://www.linkedin.com/jobs/view/{LI_ID}"


def test_canonical_url_boards_locales_and_tracking() -> None:
    uuid = "3f2a1b4c-1111-2222-3333-444455556666"
    jt = f"https://www.jobteaser.com/job-offers/{uuid}"
    assert canonical_url(f"https://uni.jobteaser.com/it/job-offers/{uuid}-intern?utm_x=1") == jt
    assert canonical_url(f"https://www.jobteaser.com/job-offers/{uuid.upper()}") == jt
    gh = "https://boards.greenhouse.io/jobs/12345"
    assert canonical_url("https://job-boards.eu.greenhouse.io/acme/jobs/12345?gh_src=li") == gh
    assert canonical_url("https://acme.example/careers/?gh_jid=12345") == gh
    assert canonical_url(f"https://jobs.eu.lever.co/Acme/{uuid}/apply?lever-source=x") == (
        f"https://jobs.lever.co/acme/{uuid}"
    )
    assert canonical_url(f"https://jobs.ashbyhq.com/acme/{uuid}/application") == (
        f"https://jobs.ashbyhq.com/acme/{uuid}"
    )
    assert canonical_url("https://www.acme.example/en/careers/42/?ref=newsletter#apply") == (
        canonical_url("https://acme.example/it-it/careers/42")
    )
    assert canonical_url("https://acme.wd3.myworkdayjobs.com/en-US/Ext/job/Milan/X_JR1") == (
        "https://acme.wd3.myworkdayjobs.com/Ext/job/Milan/X_JR1"
    )
    # a two-letter board slug is not a locale
    assert canonical_url(f"https://jobs.lever.co/it/{uuid}").endswith(f"/it/{uuid}")


def test_company_normalization() -> None:
    assert normalize_company("Nebbia Labs S.p.A.") == "nebbia labs"
    assert company_key("Nebbia Labs S.p.A.") == company_key("NebbiaLabs")
    assert company_key("Fiordo Payments B.V.") == company_key("fiordo payments")
    assert company_key("Brina GmbH & Co. KG") == company_key("BRINA")
    assert company_key("Société Ventaglio S.A.") == company_key("Societe Ventaglio")
    assert company_key("Orbis Consulting Ltd.") != company_key("Orbis Analytics Ltd.")


def test_title_normalization_and_similarity() -> None:
    base = title_tokens("Data Scientist Intern")
    for variant in (
        "Data Scientist Intern (m/w/d)",
        "Data Scientist Intern (f/m/x)",
        "Stage Data Scientist H/F",
        "Data Scientist Internship - 6 months",
        "Data Scientist Intern - Summer 2027",
        "Data Scientist Intern, Milan",
        "DATA SCIENTIST INTERN | Hybrid",
    ):
        assert set(title_tokens(variant)) == set(base), variant
    assert title_similarity("Data Scientist Intern", "Data Scientist Intern (f/m/x)") == 1.0
    assert title_similarity("Data Scientist", "Data Scientist Intern") == 0.0
    assert title_similarity("Junior Data Analyst", "Senior Data Analyst") == 0.0
    assert title_similarity("Data Analyst I", "Data Analyst II") == 0.0
    assert title_similarity("Data Scientist Intern", "Marketing Intern") < 0.9
    assert dedupe_key("Acme S.p.A.", "AI Intern (m/f/d)", "IT") == dedupe_key(
        "ACME", "AI Internship", "IT"
    )


def test_source_rank_order() -> None:
    board_url = "https://boards.greenhouse.io/acme/jobs/1"
    li_url = "https://www.linkedin.com/jobs/view/1"
    jt_url = "https://www.jobteaser.com/job-offers/1"
    ranks = [
        source_rank("greenhouse", board_url),
        source_rank("seed", "https://acme.example/jobs/1"),
        source_rank("jobteaser", jt_url),
        source_rank("linkedin_jobs", li_url),
        source_rank("email", li_url),
    ]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)


# --- matching in the pipeline -----------------------------------------------------------------


def test_same_linkedin_job_from_alert_and_connector(session: Session) -> None:
    alert = upsert_raw(session, li_alert(), now=T0).posting
    job = upsert_raw(session, li_job(), now=T1)
    assert job.is_duplicate and job.posting.duplicate_of == alert.id
    assert job.primary_id == alert.id
    session.refresh(alert)
    assert sources_of(alert) == {"email", "linkedin_jobs"}
    assert len(primaries(session)) == 1
    labels = [s["label"] for s in public_sources(alert)]
    assert labels == ["LinkedIn", "Email alert"]  # best link first


def test_board_and_linkedin_with_title_variants(session: Session) -> None:
    li = upsert_raw(session, li_job(title="Data Scientist Intern (f/m/x)"), now=T0).posting
    gh = upsert_raw(session, board(title="Data Scientist Intern - 6 months"), now=T1).posting
    assert gh.duplicate_of == li.id
    session.refresh(li)
    assert li.duplicate_of is None
    assert li.url == "https://boards.greenhouse.io/acme/jobs/501"  # best direct link
    assert li.via == "Sito aziendale"
    assert [s["label"] for s in public_sources(li)] == ["Sito aziendale", "LinkedIn"]


def test_jobteaser_board_linkedin_and_alert_become_one(session: Session) -> None:
    first = upsert_raw(session, li_alert(), now=T0).posting
    upsert_raw(session, li_job(), now=T0)
    upsert_raw(session, jobteaser(), now=T1)
    upsert_raw(session, board(), now=T2)
    (only,) = primaries(session)
    assert only.id == first.id
    assert sources_of(only) == {"email", "linkedin_jobs", "jobteaser", "greenhouse"}
    assert [s["label"] for s in public_sources(only)] == [
        "Sito aziendale",
        "JobTeaser",
        "LinkedIn",
        "Email alert",
    ]


def test_different_cities_are_not_merged(session: Session) -> None:
    milan = upsert_raw(session, board("1", location="Milan, Italy"), now=T0).posting
    paris = upsert_raw(session, li_job("111", location="Paris, France"), now=T1).posting
    rome = upsert_raw(session, li_job("222", location="Roma, Lazio, Italia"), now=T1).posting
    multi = upsert_raw(
        session, li_alert("333", title="Data Scientist Intern - Roma / Milano"), now=T1
    ).posting
    assert paris.duplicate_of is None and paris.country_code == "FR"
    assert rome.duplicate_of is None
    assert multi.duplicate_of is None  # "Rome or Milan" is not the Milan-only requisition
    assert milan.duplicate_of is None


def test_different_roles_and_companies_are_not_merged(session: Session) -> None:
    upsert_raw(session, board("1", title="Data Analyst Intern"), now=T0)
    a = upsert_raw(session, li_job("111", title="Data Analyst"), now=T1).posting
    b = upsert_raw(session, li_job("222", title="Data Analyst Intern", company="Acme Bank"), now=T1)
    c = upsert_raw(session, li_job("333", title="Senior Data Analyst Intern"), now=T1).posting
    assert a.duplicate_of is None and b.posting.duplicate_of is None and c.duplicate_of is None


def test_two_board_openings_stay_apart(session: Session) -> None:
    """A LinkedIn copy joins one requisition; the second requisition stays its own job."""
    one = upsert_raw(session, board("1"), now=T0).posting
    li = upsert_raw(session, li_job(), now=T1).posting
    two = upsert_raw(session, board("2"), now=T1).posting
    assert li.duplicate_of == one.id
    assert two.duplicate_of is None


def test_better_source_later_keeps_id_and_upgrades_description(session: Session) -> None:
    alert = upsert_raw(session, li_alert(), now=T0).posting
    pid = alert.id
    upsert_raw(session, board(), now=T1)
    session.refresh(alert)
    assert alert.id == pid and alert.duplicate_of is None
    assert alert.raw_text == BOARD_TEXT and alert.description_from == "greenhouse:501"
    own = next(e for e in alert.sources or [] if e["uid"] == alert.uid)
    assert own["own_text"].startswith("Data Scientist Intern")  # the snippet is kept
    kinds = [
        e.kind for e in session.exec(select(ChangeEvent).where(ChangeEvent.posting_id == pid)).all()
    ]
    assert kinds == ["new", "changed"]
    # the alert seen again: no revert of link or text, no spurious change event
    out = upsert_raw(session, li_alert(), now=T2)
    assert out.kind == "unchanged"
    session.refresh(alert)
    assert alert.url == "https://boards.greenhouse.io/acme/jobs/501"
    assert alert.raw_text == BOARD_TEXT
    # the board's text changes: the primary follows
    upsert_raw(session, board(raw_text=BOARD_TEXT + " Updated."), now=T2)
    session.refresh(alert)
    assert alert.raw_text == BOARD_TEXT + " Updated."


def test_new_posting_bridging_two_groups_keeps_user_work(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as m:  # two separate groups, as an old database may have them
        m.setattr(dedupe, "link_new", _no_link)
        old = upsert_raw(session, jobteaser(), now=T0).posting
        worked = upsert_raw(session, li_job(), now=T1).posting
    session.add(ApplicationPack(posting_id=worked.id or 0))
    session.flush()
    bridge = upsert_raw(session, board(), now=T2).posting  # matches both
    session.refresh(old)
    session.refresh(worked)
    assert worked.duplicate_of is None  # has a pack: keeps its id although it is newer
    assert old.duplicate_of == worked.id and bridge.duplicate_of == worked.id
    assert sources_of(worked) == {"jobteaser", "linkedin_jobs", "greenhouse"}


# --- closing ------------------------------------------------------------------------------


def test_closing_requires_all_exhaustive_sources(session: Session) -> None:
    gh = upsert_raw(session, board(), now=T0).posting
    jt = upsert_raw(session, jobteaser(), now=T0).posting
    upsert_raw(session, li_alert(), now=T0)
    assert jt.duplicate_of == gh.id
    # JobTeaser and alerts are not exhaustive (the connector never lists everything): only the
    # board decides, so its absence closes the job even though JobTeaser showed it once.
    assert not dedupe.is_exhaustive_source("jobteaser")
    close_missing(
        session, source="greenhouse", companies=None, returned_uids=set(), run_id=None, now=T1
    )
    session.refresh(gh)
    session.refresh(jt)
    assert gh.status == "closed"
    # listed again on the board: the job reopens, the board link is back
    upsert_raw(session, board(), now=T2)
    session.refresh(gh)
    assert gh.status == "open" and gh.url == "https://boards.greenhouse.io/acme/jobs/501"


def test_board_absence_closes_linkedin_first_job(session: Session) -> None:
    li = upsert_raw(session, li_job(), now=T0).posting
    gh = upsert_raw(session, board(), now=T1).posting
    assert gh.duplicate_of == li.id
    close_missing(
        session, source="greenhouse", companies=None, returned_uids=set(), run_id=None, now=T2
    )
    session.refresh(li)
    assert li.status == "closed"  # LinkedIn search is not exhaustive: the board decides
    kinds = [
        e.kind
        for e in session.exec(select(ChangeEvent).where(ChangeEvent.posting_id == li.id)).all()
    ]
    assert "closed" in kinds
    # LinkedIn still showing it does not reopen it
    assert upsert_raw(session, li_job(), now=T2).kind == "unchanged"
    session.refresh(li)
    assert li.status == "closed"
    # a member reopening reopens the job
    upsert_raw(session, board(), now=T2 + timedelta(days=1))
    session.refresh(li)
    assert li.status == "open"


def test_non_exhaustive_absence_never_closes(session: Session) -> None:
    alert = upsert_raw(session, li_alert(), now=T0).posting
    upsert_raw(session, li_job(), now=T0)
    n = close_missing(
        session, source="email", companies=None, returned_uids=set(), run_id=None, now=T1
    )
    session.refresh(alert)
    assert n == 0 and alert.status == "open"  # no exhaustive source: nothing closes


def test_url_check_skips_jobs_a_board_lists(session: Session) -> None:
    li = upsert_raw(session, li_job(), now=T0).posting
    upsert_raw(session, board(), now=T0)
    alone = upsert_raw(session, li_job("777", title="Marketing Intern"), now=T0).posting
    checked: list[str] = []

    def checker(url: str) -> bool | None:
        checked.append(url)
        return True

    check_open_postings(session, checker, run_id=None, now=T1)
    assert checked == [alone.url]
    session.refresh(li)
    assert li.status == "open"


# --- backfill -------------------------------------------------------------------------------


def _no_link(session: Session, posting: Posting, *args: Any, **kwargs: Any) -> Posting:
    return posting


@pytest.fixture
def legacy(session: Session, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Insert rows as the old code stored them: no sources, title variants never matched."""
    monkeypatch.setattr(dedupe, "link_new", _no_link)

    def add(r: RawPosting, now: datetime) -> Posting:
        p = upsert_raw(session, r, now=now).posting
        p.dedupe_key = "legacy|" + p.dedupe_key
        p.canonical_url = p.url  # old canonical form
        session.add(p)
        session.flush()
        return p

    yield add
    monkeypatch.undo()


def _state(session: Session) -> list[tuple[Any, ...]]:
    rows = session.exec(select(Posting).order_by(Posting.id)).all()  # type: ignore[arg-type]
    return [(p.id, p.duplicate_of, p.url, p.raw_text, p.sources, p.dedupe_key) for p in rows]


def test_backfill_dry_run_apply_and_idempotent(session: Session, legacy: Any) -> None:
    a = legacy(li_alert(), T0)
    b = legacy(board(title="Data Scientist Intern (m/w/d)"), T1)
    c = legacy(li_job(), T1)
    d = legacy(board("9", title="Marketing Intern"), T1)
    session.commit()
    before = _state(session)

    counts = backfill(session, apply=False)
    assert counts["groups_merged"] == 2 and counts["marked_duplicate"] == 2
    assert _state(session) == before  # dry run wrote nothing

    counts = backfill(session, apply=True)
    assert counts["groups_merged"] == 2
    for p in (a, b, c, d):
        session.refresh(p)
    assert a.duplicate_of is None and b.duplicate_of == a.id and c.duplicate_of == a.id
    assert d.duplicate_of is None
    assert sources_of(a) == {"email", "greenhouse", "linkedin_jobs"}
    assert a.url == "https://boards.greenhouse.io/acme/jobs/501" and a.raw_text == BOARD_TEXT
    after = _state(session)

    again = backfill(session, apply=True)
    assert again["changed_rows"] == 0 and again["groups_merged"] == 0
    assert _state(session) == after
    assert len(session.exec(select(Posting)).all()) == 4  # nothing deleted


def test_backfill_never_hides_user_work_behind_user_work(session: Session, legacy: Any) -> None:
    a = legacy(li_alert(), T0)
    b = legacy(board(), T1)
    c = legacy(li_job(), T1)
    session.add(ApplicationPack(posting_id=a.id or 0))
    session.add(ApplicationPack(posting_id=b.id or 0))
    session.commit()
    counts = backfill(session, apply=True)
    for p in (a, b, c):
        session.refresh(p)
    assert counts["conflicts_skipped"] >= 1
    assert a.duplicate_of is None and b.duplicate_of is None  # both stay visible
    assert c.duplicate_of in (a.id, b.id)


def test_backfill_keeps_the_row_with_user_work(session: Session, legacy: Any) -> None:
    a = legacy(li_alert(), T0)
    b = legacy(board(), T1)
    b.user_status = "sent"
    session.add(b)
    session.commit()
    backfill(session, apply=True)
    session.refresh(a)
    session.refresh(b)
    assert b.duplicate_of is None and a.duplicate_of == b.id


# --- whole pipeline -----------------------------------------------------------------------


def test_pipeline_counts_one_job_and_closes_on_board_absence(
    engine: Any, rules: Any, tmp_path: Any
) -> None:
    from pipeline_fakes import TODAY, FakeConnector

    from radar.pipeline.run import PipelineOptions, run_pipeline

    def run(conns: list[FakeConnector], now: datetime) -> Any:
        return run_pipeline(
            PipelineOptions(llm=False, check_closed=False),
            connectors=conns,  # type: ignore[arg-type]
            engine=engine,
            rules=rules,
            today=TODAY,
            reports_dir=tmp_path,
            now=now,
        )

    li = FakeConnector("linkedin_jobs", [li_job()])
    first = run([FakeConnector("greenhouse", [board()], company="Acme"), li], T0)
    assert first.stats["sources"]["greenhouse"]["new"] == 1
    assert first.stats["sources"]["linkedin_jobs"]["new"] == 0  # the same job: not new
    with Session(engine) as s:
        (job,) = primaries(s)
        assert sources_of(job) == {"greenhouse", "linkedin_jobs"} and job.source == "greenhouse"
    # the board stops listing it; LinkedIn search still shows it: closed (board decides)
    run([FakeConnector("greenhouse", [], company="Acme"), li], T1)
    with Session(engine) as s:
        (job,) = primaries(s)
        assert job.status == "closed"
