"""GET /api/sources: the Fonti screen."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from radar import sources
from radar.connectors.status import pause
from radar.models import LLMUsage, Run
from radar.timeutil import utcnow

CFG = {
    "companies": [
        {"name": "Nebula Analytics", "ats": "greenhouse", "slug": "nebula"},
        {"name": "Fiordo Payments", "ats": "greenhouse", "slug": "fiordo"},
        {"name": "Lumen Labs", "ats": "greenhouse", "slug": "lumen"},
        {"name": "Orbis", "ats": "lever", "slug": "orbis", "active": False},
    ]
}


@pytest.fixture(autouse=True)
def _private_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "load_yaml_config", lambda name: CFG)
    monkeypatch.setattr(sources, "linkedin_quota", lambda settings: None)


def _run(session: Session) -> Run:
    start = utcnow() + timedelta(minutes=1)
    run = Run(
        started_at=start,
        finished_at=start + timedelta(hours=4, minutes=30),
        status="partial",
        report_md="# report",
        stats={
            "sources": {
                "greenhouse": {"boards": 3, "fetched": 12, "new": 2, "errors": 1, "closed": 1},
                "linkedin_jobs": {"boards": 1, "fetched": 0, "new": 0, "errors": 1},
            },
            "boards": [
                {"source": "greenhouse", "board": "greenhouse board #1",
                 "company": "Nebula Analytics", "fetched": 12, "new": 2},
                {"source": "greenhouse", "board": "greenhouse board #2",
                 "company": "Fiordo Payments", "fetched": 0},
                {"source": "greenhouse", "board": "greenhouse board #3",
                 "company": "Lumen Labs", "fetched": 0, "error": "SourceError (HTTP 404)"},
            ],
            "errors": [
                {"source": "greenhouse", "error": "SourceError (HTTP 404)",
                 "company": "Lumen Labs"},
                {"source": "linkedin_jobs", "error": "SourceError",
                 "problem": "È installato linkedin-cli 0.2.0, ma serve la 0.3.",
                 "action": "Aggiorna linkedin-cli: `uv tool install --force git+...`"},
            ],
            "awake_seconds": 1500,
            "timings": {"collect": 1400.0},
        },
    )  # fmt: skip
    session.add(run)
    session.commit()
    return run


def test_sources_overview(client, api_engine) -> None:  # type: ignore[no-untyped-def]
    with Session(api_engine) as s:
        _run(s)
        pause(s, "jobteaser", "sessione scaduta", "Esegui `make jobteaser-login`.")
        s.add(
            LLMUsage(
                provider="groq",
                model="openai/gpt-oss-20b",
                purpose="scoring",
                requests=3,
                input_tokens=5000,
                output_tokens=1000,
                at=datetime.now(UTC),
            )
        )
        s.commit()
    data = client.get("/api/sources").json()
    by = {c["key"]: c for c in data["sources"]}

    gh = by["greenhouse"]
    assert gh["state"] == "errors" and (gh["fetched"], gh["new"], gh["closed"]) == (12, 2, 1)
    assert "404" in gh["problem"] and "check_boards.py --ats greenhouse" in gh["action"]
    assert gh["boards"]["configured"] == 3 and gh["boards"]["working"] == 2
    assert gh["boards"]["empty"] == ["Fiordo Payments"]
    assert gh["boards"]["failing"][0]["company"] == "Lumen Labs"
    assert len(gh["chart"]) == 7

    assert by["lever"]["state"] == "disabled"  # no active board
    assert by["smartrecruiters"]["state"] == "disabled"
    li = by["linkedin_jobs"]
    assert li["state"] in ("errors", "disabled")
    jt = by["jobteaser"]
    assert jt["state"] == "paused" and jt["resumable"] and "jobteaser-login" in jt["action"]
    assert by["email"]["state"] in ("disabled", "ok")

    groq = {q["model"]: q for q in data["quotas"]["groq"]}
    assert groq["openai/gpt-oss-20b"]["used"] >= 6000  # demo usage + ours
    assert groq["openai/gpt-oss-20b"]["limit"] == 200000
    assert data["quotas"]["contacts"]["monthly_cap_eur"] > 0
    run = data["last_run"]
    assert run["wall_seconds"] == 4.5 * 3600 and run["awake_seconds"] == 1500 and run["report"]
    assert data["attention"] >= 2


def test_explain_covers_common_errors() -> None:
    assert "robots.txt" in sources.explain("personio", "RobotsDisallowed")[0]
    assert "riprova" in sources.explain("ashby", "SourceError (HTTP 503)")[1]
    assert "HTTP 429" in sources.explain("workday", "SourceError (HTTP 429)")[0]
    assert "--sources lever" in sources.explain("lever", "ValueError")[1]


def test_groq_limit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_GROQ_DAILY_TOKENS", "openai/gpt-oss-20b=500000")
    sources.get_settings.cache_clear()
    try:
        assert sources.groq_limits(sources.get_settings())["openai/gpt-oss-20b"] == 500000
    finally:
        sources.get_settings.cache_clear()
