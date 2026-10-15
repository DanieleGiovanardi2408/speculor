from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pipeline_fakes import raw
from sqlmodel import Session

from radar.models import Run
from radar.pipeline.closed import close_missing
from radar.pipeline.diff import diff_for_run, diff_since
from radar.pipeline.store import upsert_raw

T0 = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=1)


def _run(session: Session) -> int:
    run = Run()
    session.add(run)
    session.flush()
    assert run.id is not None
    return run.id


def test_diff_for_run(session: Session) -> None:
    r1 = _run(session)
    a = upsert_raw(session, raw("1"), run_id=r1, now=T0).posting
    b = upsert_raw(session, raw("2"), run_id=r1, now=T0).posting
    upsert_raw(session, raw("3"), run_id=r1, now=T0)
    d1 = diff_for_run(session, r1)
    assert d1.counts() == {"new": 3, "changed": 0, "closed": 0, "reopened": 0}

    r2 = _run(session)
    upsert_raw(session, raw("1", raw_text="new text"), run_id=r2, now=T1)
    upsert_raw(session, raw("2"), run_id=r2, now=T1)
    # duplicate of #1 from another source: not reported as new
    upsert_raw(session, raw("d", source="linkedin", url=a.url), run_id=r2, now=T1)
    upsert_raw(session, raw("4"), run_id=r2, now=T1)
    close_missing(
        session,
        source="greenhouse",
        companies={"acme"},
        returned_uids={a.uid, b.uid, "greenhouse:4"},
        run_id=r2,
        now=T1,
    )
    d2 = diff_for_run(session, r2)
    assert [p.uid for p in d2.new] == ["greenhouse:4"]
    assert [p.uid for p in d2.changed] == ["greenhouse:1"]
    assert d2.changes[a.id or 0]["raw_text"] == [None, "content changed"]
    assert [p.uid for p in d2.closed] == ["greenhouse:3"]

    since = diff_since(session, T1 - timedelta(hours=1))
    assert {p.uid for p in since.new} == {"greenhouse:4"}
    assert len(diff_since(session, T0 - timedelta(hours=1)).new) == 4
