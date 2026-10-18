from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pipeline_fakes import TODAY, FakeConnector, raw
from sqlalchemy import Engine
from sqlmodel import Session, select

from radar.connectors.base import SourceError
from radar.db import set_setting
from radar.models import FilterResult, Posting, Run
from radar.pipeline.filters import Rules
from radar.pipeline.run import PipelineOptions, error_label, run_pipeline

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
OPTS = PipelineOptions(llm=False, check_closed=True)


def _run(
    engine: Engine,
    rules: Rules,
    tmp_path: Path,
    conns: list[FakeConnector],
    now: datetime = NOW,
    checker=None,
    opts: PipelineOptions = OPTS,
):  # type: ignore[no-untyped-def]
    return run_pipeline(
        opts,
        connectors=conns,  # type: ignore[arg-type]
        engine=engine,
        checker=checker,
        rules=rules,
        today=TODAY,
        reports_dir=tmp_path,
        now=now,
    )


def test_end_to_end(engine: Engine, rules: Rules, tmp_path: Path) -> None:
    acme = [
        raw("1", title="AI Engineer Intern"),
        raw("2", title="Data Intern", raw_text="Fluent German required. Start: Feb 2027."),
        raw("3", title="ML Intern", location="London"),
    ]
    beta = FakeConnector(
        "lever",
        [raw("b1", source="lever", company="Beta", url="https://jobs.lever.co/beta/1")],
        company="Beta",
    )
    broken = FakeConnector("ashby", error=SourceError("acme.example: HTTP 503"), company="Zeta")
    li = FakeConnector(
        "linkedin",
        [
            raw(
                "li1",
                source="linkedin",
                url="https://www.linkedin.com/jobs/view/1",
                title="AI Engineer Intern",
            )
        ],
    )
    with Session(engine) as s:  # a seed posting checked by URL
        s.add(
            Posting(
                uid="seed:x",
                source="seed",
                external_id="x",
                url="https://x.example/1",
                canonical_url="https://x.example/1",
                title="Seed role",
                company_name="Seedco",
                location="Roma",
                country_code="IT",
            )
        )
        s.commit()

    res = _run(
        engine,
        rules,
        tmp_path,
        [FakeConnector("greenhouse", acme, company="Acme"), beta, broken, li],
        checker=lambda url: False,
    )
    assert res.status == "partial"
    st = res.stats
    assert st["sources"]["greenhouse"] == {"boards": 1, "fetched": 3, "new": 3, "errors": 0}
    assert st["sources"]["ashby"]["errors"] == 1
    assert st["sources"]["linkedin"]["new"] == 0  # same company/title/country: duplicate
    assert st["errors"] == [
        {
            "source": "ashby",
            "error": "SourceError (HTTP 503)",
            "board": "ashby board",
            "company": "Zeta",
        }
    ]
    assert st["new"] == 4 and st["rejected"] == 2  # German + UK
    assert st["closed_by_check"]["closed"] == 1  # the seed posting; linkedin seen this run
    assert res.report_path is not None and res.report_path.exists()
    line = res.summary_line()
    assert "Acme" not in line and "Intern" not in line and "new 4" in line

    with Session(engine) as s:
        run = s.get(Run, res.run_id)
        assert run is not None and run.status == "partial" and run.report_md
        assert run.finished_at is not None
        assert s.exec(select(FilterResult)).first() is not None
        li_row = s.exec(select(Posting).where(Posting.uid == "linkedin:li1")).one()
        assert li_row.duplicate_of is not None

    # second run: Acme #2 disappeared, broken board still failing, linkedin returns nothing
    broken_posting = raw("z1", source="ashby", company="Zeta", url="https://jobs.ashbyhq.com/z/1")
    with Session(engine) as s:
        s.add(
            Posting(
                uid="ashby:z1",
                source="ashby",
                external_id="z1",
                url=broken_posting.url,
                canonical_url=broken_posting.url,
                title="Zeta role",
                company_name="Zeta",
            )
        )
        s.commit()
    res2 = _run(
        engine,
        rules,
        tmp_path,
        [
            FakeConnector("greenhouse", [acme[0], acme[2]], company="Acme"),
            beta,
            broken,
            FakeConnector("linkedin", []),
        ],
        now=NOW + timedelta(days=1),
    )
    assert res2.stats["new"] == 0
    assert res2.stats["closed"] == 1
    with Session(engine) as s:
        by_uid = {p.uid: p for p in s.exec(select(Posting)).all()}
        assert by_uid["greenhouse:2"].status == "closed"
        assert by_uid["ashby:z1"].status == "open"  # connector failed: never closed
        assert by_uid["linkedin:li1"].status == "open"  # search is not exhaustive
        assert by_uid["lever:b1"].status == "open"

    # third run: posting #2 is back -> reopened
    res3 = _run(
        engine,
        rules,
        tmp_path,
        [FakeConnector("greenhouse", acme, company="Acme")],
        now=NOW + timedelta(days=2),
    )
    assert res3.stats["reopened"] == 1 and res3.status == "ok"


def test_disabled_sources_and_filter(engine: Engine, rules: Rules, tmp_path: Path) -> None:
    with Session(engine) as s:
        set_setting(s, "sources_enabled", {"lever": False})
        s.commit()
    gh = FakeConnector("greenhouse", [raw("1")], company="Acme")
    lv = FakeConnector("lever", [raw("2", source="lever")], company="Acme")
    ab = FakeConnector("ashby", [raw("3", source="ashby")], company="Acme")
    res = _run(
        engine,
        rules,
        tmp_path,
        [gh, lv, ab],
        opts=PipelineOptions(sources=["greenhouse", "lever"], llm=False),
    )
    assert set(res.stats["sources"]) == {"greenhouse"}
    assert res.status == "ok"


def test_all_failed_is_failed(engine: Engine, rules: Rules, tmp_path: Path) -> None:
    res = _run(
        engine,
        rules,
        tmp_path,
        [FakeConnector("greenhouse", error=SourceError("x: HTTP 429"), company="A")],
    )
    assert res.status == "failed"
    assert res.stats["errors"][0]["error"] == "SourceError (HTTP 429)"


def test_llm_errors_do_not_stop_run(
    engine: Engine, rules: Rules, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import sys
    import types

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("rate limited")

    scoring = types.ModuleType("radar.llm.scoring")
    scoring.score_pending = boom  # type: ignore[attr-defined]
    packs = types.ModuleType("radar.llm.packs")
    packs.build_packs = lambda session, run_id, limit, posting_id=None: {"built": 0}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "radar.llm.scoring", scoring)
    monkeypatch.setitem(sys.modules, "radar.llm.packs", packs)
    res = _run(
        engine,
        rules,
        tmp_path,
        [FakeConnector("greenhouse", [raw("1")], company="Acme")],
        opts=PipelineOptions(llm=True),
    )
    assert res.status == "partial"
    assert {"source": "llm:scoring", "error": "RuntimeError"} in res.stats["errors"]
    assert res.stats["llm"]["packs"] == {"built": 0}


def test_error_label_never_leaks_message() -> None:
    assert error_label(SourceError("secret-company.com: HTTP 404")) == "SourceError (HTTP 404)"
    assert error_label(ValueError("Acme Corp broke")) == "ValueError"
