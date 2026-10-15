from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pipeline_fakes import FakeConnector, raw
from sqlmodel import Session, select

from radar.db import get_setting
from radar.models import ChangeEvent, Posting
from radar.pipeline.closed import (
    CHECK_SETTING,
    board_companies,
    check_open_postings,
    close_missing,
    is_exhaustive,
)
from radar.pipeline.store import upsert_raw

T0 = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)


def test_close_missing_only_same_company(session: Session) -> None:
    a1 = upsert_raw(session, raw("1"), now=T0).posting
    a2 = upsert_raw(session, raw("2"), now=T0).posting
    b1 = upsert_raw(session, raw("3", company="Beta"), now=T0).posting
    n = close_missing(
        session, source="greenhouse", companies={"acme"}, returned_uids={a1.uid}, run_id=None
    )
    assert n == 1
    assert (a1.status, a2.status, b1.status) == ("open", "closed", "open")
    ev = session.exec(select(ChangeEvent).where(ChangeEvent.kind == "closed")).all()
    assert [e.posting_id for e in ev] == [a2.id]


def test_close_missing_empty_company_set_is_noop(session: Session) -> None:
    upsert_raw(session, raw("1"), now=T0)
    assert (
        close_missing(
            session, source="greenhouse", companies=set(), returned_uids=set(), run_id=None
        )
        == 0
    )


def test_board_companies_and_exhaustive() -> None:
    conn = FakeConnector("greenhouse", company="Acme S.p.A.")
    assert board_companies(conn, []) == {"acme"}
    assert board_companies(FakeConnector("lever"), [raw("1", company="Beta")]) == {"beta"}
    assert board_companies(FakeConnector("lever"), []) == set()
    assert is_exhaustive(conn) and not is_exhaustive(FakeConnector("linkedin"))


def test_check_open_postings_cap_rotation_and_results(session: Session) -> None:
    ids = []
    for i in range(4):
        p = upsert_raw(
            session, raw(f"s{i}", source="seed", url=f"https://x.example/{i}"), now=T0
        ).posting
        ids.append(p.id)
    results = {"https://x.example/0": False, "https://x.example/1": None}
    calls: list[str] = []

    def checker(url: str) -> bool | None:
        calls.append(url)
        if url.endswith("/2"):
            raise RuntimeError("boom")
        return results.get(url, True)

    c1 = check_open_postings(session, checker, run_id=None, cap=2, now=T0)
    assert c1["checked"] == 2 and c1["closed"] == 1 and c1["unknown"] == 1
    assert session.get(Posting, ids[0]).status == "closed"  # type: ignore[union-attr]
    assert session.get(Posting, ids[1]).status == "open"  # type: ignore[union-attr]
    # next run: the two never-checked postings go first
    c2 = check_open_postings(session, checker, run_id=None, cap=2, now=T0 + timedelta(days=1))
    assert calls[2:] == ["https://x.example/2", "https://x.example/3"]
    assert c2["unknown"] == 1 and c2["open"] == 1
    last = get_setting(CHECK_SETTING, session=session)
    assert set(last) == {str(ids[1]), str(ids[2]), str(ids[3])}


def test_check_skips_excluded_and_ats(session: Session) -> None:
    gh = upsert_raw(session, raw("1"), now=T0).posting
    li = upsert_raw(
        session, raw("9", source="linkedin", url="https://www.linkedin.com/jobs/view/9"), now=T0
    ).posting
    calls: list[str] = []
    counts = check_open_postings(
        session, lambda u: calls.append(u) or False, run_id=None, exclude_ids={li.id or 0}
    )
    assert counts["checked"] == 0 and calls == []
    assert gh.status == "open" and li.status == "open"
