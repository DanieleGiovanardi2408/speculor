from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pipeline_fakes import TODAY, raw
from sqlmodel import Session

from radar.models import Application, LLMUsage, Run, Score
from radar.pipeline.closed import close_missing
from radar.pipeline.diff import diff_for_run
from radar.pipeline.filters import Rules, apply_filters
from radar.pipeline.report import render_report, write_report
from radar.pipeline.store import upsert_raw

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)


def test_report_sections(session: Session, rules: Rules, tmp_path: Path) -> None:
    old_run = Run()
    session.add(old_run)
    session.flush()
    gone = upsert_raw(session, raw("old", title="Old Intern"), run_id=old_run.id, now=NOW).posting

    run = Run(
        started_at=NOW,
        status="partial",
        stats={
            "sources": {"greenhouse": {"boards": 2, "fetched": 3, "new": 3, "errors": 1}},
            "errors": [{"source": "greenhouse", "error": "SourceError (HTTP 503)"}],
            "llm": {"scoring": {"scored": 1, "input_tokens": 900}},
        },
    )
    session.add(run)
    session.flush()
    assert run.id is not None
    good = upsert_raw(
        session,
        raw("1", title="AI Engineer Intern", deadline=TODAY + timedelta(days=5)),
        run_id=run.id,
        now=NOW,
    ).posting
    flagged = upsert_raw(
        session, raw("2", title="Data Intern", raw_text="Python, SQL."), run_id=run.id, now=NOW
    ).posting
    rejected = upsert_raw(
        session,
        raw("3", title="Analyst Intern", raw_text="Start: Feb 2027. Fluent French required."),
        run_id=run.id,
        now=NOW,
    ).posting
    close_missing(
        session,
        source="greenhouse",
        companies={"acme"},
        returned_uids={good.uid, flagged.uid, rejected.uid},
        run_id=run.id,
        now=NOW,
    )
    for p in (good, flagged, rejected):
        apply_filters(session, p, rules, TODAY)
    assert (good.filter_state, flagged.filter_state, rejected.filter_state) == (
        "pass",
        "flagged",
        "rejected",
    )
    session.add(
        Score(
            posting_id=good.id or 0,
            content_hash=good.content_hash,
            priority="Alta",
            why_fits="LLM agents in Rome",
            what_doesnt_fit="Short internship",
            provider="fake",
            model="m1",
        )
    )
    session.add(
        Application(
            company="Gamma", role="ML Intern", date_sent=date(2026, 9, 20), channel="LinkedIn"
        )
    )
    session.add(
        Application(
            company="Delta", role="DS Intern", date_sent=date(2026, 10, 10), channel="Email"
        )
    )
    session.add(
        LLMUsage(
            run_id=run.id,
            provider="fake",
            model="m1",
            purpose="scoring",
            requests=2,
            input_tokens=900,
            output_tokens=120,
        )
    )
    session.flush()

    diff = diff_for_run(session, run.id)
    md = render_report(session, run, diff, TODAY)
    assert md.startswith("# Radar daily report — 2026-10-15")
    assert "status **partial**" in md
    assert "| greenhouse | 2 | 3 | 3 | 1 |" in md
    assert "### Alta" in md and "AI Engineer Intern" in md and "LLM agents in Rome" in md
    assert "What doesn't fit: Short internship" in md
    flagged_part = md.split("## New flagged")[1].split("## New rejected")[0]
    assert "Data Intern" in flagged_part and "start not stated" in flagged_part
    rejected_part = md.split("## New rejected")[1].split("##")[0]
    assert "French required" in rejected_part and "`language_required`" in rejected_part
    assert "Fluent French required" in rejected_part  # evidence
    closed_part = md.split("## Closed since yesterday")[1].split("##")[0]
    assert gone.title in closed_part
    deadlines = md.split("## Upcoming deadlines")[1].split("##")[0]
    assert "AI Engineer Intern" in deadlines
    follow = md.split("## Follow-ups due")[1].split("##")[0]
    assert "ML Intern" in follow and "DS Intern" not in follow
    assert "| fake | m1 | scoring | 2 | 900 | 120 |" in md
    assert "SourceError (HTTP 503)" in md

    path = write_report(session, run, diff, tmp_path, TODAY)
    assert path == tmp_path / "2026-10-15.md"
    assert path.read_text("utf-8") == run.report_md


def test_report_empty(session: Session) -> None:
    run = Run(started_at=NOW, status="ok", stats={})
    session.add(run)
    session.flush()
    md = render_report(session, run, diff_for_run(session, run.id or 0), TODAY)
    assert "_No sources ran._" in md and "## Follow-ups due" in md
