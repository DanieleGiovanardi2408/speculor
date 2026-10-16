"""Personio, Workable, Teamtailor, Recruitee and inRecruiting on fictional fixtures."""

from __future__ import annotations

import httpx
import pytest
import respx
from conn_helpers import load_json, load_text

from radar.connectors.base import Connector, SourceError
from radar.connectors.closed import check_open
from radar.connectors.feeds import parse_xml
from radar.connectors.http import PoliteClient, RobotsDisallowed
from radar.connectors.inrecruiting import InRecruitingConnector, parse_slug
from radar.connectors.personio import PersonioConnector
from radar.connectors.personio import parse_posting_url as personio_parse
from radar.connectors.recruitee import RecruiteeConnector
from radar.connectors.teamtailor import TeamtailorConnector
from radar.connectors.workable import WorkableConnector
from radar.connectors.workable import parse_posting_url as workable_parse

FILTER = ["intern", "junior", "analyst", "graduate", "stage"]
PERSONIO = "https://fiordo.jobs.personio.de"
WORKABLE = "https://apply.workable.com/api/v1/widget/accounts/nebula"
TEAMTAILOR = "https://lumenlabs.teamtailor.com"
RECRUITEE = "https://orbis.recruitee.com"
INREC = "https://bancaesempio.intervieweb.it"


def robots(mock: respx.MockRouter, base: str, body: str = "User-agent: *\nAllow: /\n") -> None:
    mock.get(f"{base}/robots.txt").mock(return_value=httpx.Response(200, text=body))


DISALLOW = "User-agent: *\nDisallow: /\n"


# ------------------------------------------------------------------ personio
def test_personio_parses_feed(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, PERSONIO)
    route = mock.get(f"{PERSONIO}/xml").mock(
        return_value=httpx.Response(200, text=load_text("personio/feed.xml"))
    )
    conn = PersonioConnector(client, "Fiordo Payments", "fiordo")
    assert isinstance(conn, Connector)
    posts = conn.fetch()
    assert route.call_count == 1
    assert len(posts) == 2
    p = posts[0]
    assert p.source == "personio" and p.external_id == "1100001"
    assert p.url == f"{PERSONIO}/job/1100001"
    assert p.title == "Data Science Intern (m/f/d)"
    assert p.company == "Fiordo Payments"
    assert p.location == "Munich, Milan"
    assert p.department == "Data" and p.employment_type == "intern"
    assert p.posted_at is not None and p.posted_at.isoformat().startswith("2026-09-14T09:30")
    assert "Your mission" in p.raw_text and "- Python" in p.raw_text
    assert "<p>" not in p.raw_text and "CDATA" not in p.raw_text
    assert p.extra["seniority"] == "entry-level"


def test_personio_title_filter_lang_and_com_host(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    base = "https://fiordo.jobs.personio.com"
    robots(mock, base)
    route = mock.get(f"{base}/xml", params={"language": "en"}).mock(
        return_value=httpx.Response(200, text=load_text("personio/feed.xml"))
    )
    posts = PersonioConnector(
        client, "Fiordo", "fiordo", region="com", lang="en", title_filter=FILTER
    ).fetch()
    assert route.call_count == 1
    assert [p.external_id for p in posts] == ["1100001"]
    assert posts[0].url == f"{base}/job/1100001?language=en"


def test_personio_robots_disallow_skips(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, PERSONIO, DISALLOW)
    route = mock.get(f"{PERSONIO}/xml")
    with pytest.raises(RobotsDisallowed):
        PersonioConnector(client, "Fiordo", "fiordo").fetch()
    assert route.call_count == 0


def test_personio_bad_feed_and_404(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, PERSONIO)
    mock.get(f"{PERSONIO}/xml").mock(return_value=httpx.Response(200, text="<html>hi</html>"))
    with pytest.raises(SourceError, match="unexpected feed format"):
        PersonioConnector(client, "Fiordo", "fiordo").fetch()
    robots(mock, "https://nope.jobs.personio.de")
    mock.get("https://nope.jobs.personio.de/xml").mock(return_value=httpx.Response(404))
    with pytest.raises(SourceError, match="404"):
        PersonioConnector(client, "X", "nope").fetch()


def test_personio_url_parsing_and_closed_check(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    assert personio_parse("https://fiordo.jobs.personio.de/job/1100001?language=en") == (
        "fiordo",
        "1100001",
        "de",
    )
    assert personio_parse("https://example.com/job/1") is None
    robots(mock, PERSONIO)
    mock.get(f"{PERSONIO}/xml").mock(
        return_value=httpx.Response(200, text=load_text("personio/feed.xml"))
    )
    assert check_open(client, f"{PERSONIO}/job/1100002") is True
    assert check_open(client, f"{PERSONIO}/job/999") is False


def test_feed_with_dtd_is_refused() -> None:
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><workzag-jobs/>'
    with pytest.raises(SourceError, match="DTD"):
        parse_xml(evil, "personio")


# ------------------------------------------------------------------ workable
def test_workable_parses_widget(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, "https://apply.workable.com")
    route = mock.get(WORKABLE, params={"details": "true"}).mock(
        return_value=httpx.Response(200, json=load_json("workable/widget.json"))
    )
    posts = WorkableConnector(client, "Nebula Analytics", "nebula").fetch()
    assert route.call_count == 1
    assert len(posts) == 2
    p = posts[0]
    assert p.source == "workable" and p.external_id == "A1B2C3D4E5"
    assert p.url == "https://apply.workable.com/j/A1B2C3D4E5"
    assert p.location == "Milan, Italy; Amsterdam, Netherlands"
    assert p.country == "IT"
    assert p.department == "Risk" and p.employment_type == "Full-time"
    assert p.posted_at is not None and p.posted_at.date().isoformat() == "2026-09-30"
    assert "credit risk" in p.raw_text and "<p>" not in p.raw_text
    assert posts[1].location == "Berlin, Germany"


def test_workable_title_filter_and_robots(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, "https://apply.workable.com")
    mock.get(WORKABLE).mock(
        return_value=httpx.Response(200, json=load_json("workable/widget.json"))
    )
    posts = WorkableConnector(client, "Nebula", "nebula", title_filter=FILTER).fetch()
    assert [p.title for p in posts] == ["Graduate Analyst - Credit Risk"]


def test_workable_robots_disallow(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, "https://apply.workable.com", "User-agent: *\nDisallow: /api/\n")
    route = mock.get(WORKABLE)
    with pytest.raises(RobotsDisallowed):
        WorkableConnector(client, "Nebula", "nebula").fetch()
    assert route.call_count == 0


def test_workable_bad_format_and_url_parsing(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, "https://apply.workable.com")
    mock.get(WORKABLE).mock(return_value=httpx.Response(200, json={"error": "x"}))
    with pytest.raises(SourceError, match="unexpected"):
        WorkableConnector(client, "Nebula", "nebula").fetch()
    assert workable_parse("https://apply.workable.com/nebula/j/A1B2C3D4E5/") == (
        "nebula",
        "A1B2C3D4E5",
    )
    assert workable_parse("https://apply.workable.com/j/A1B2C3D4E5") == (None, "A1B2C3D4E5")
    # a short link carries no account slug: the closed check cannot know
    assert check_open(client, "https://apply.workable.com/j/A1B2C3D4E5") is None


# ------------------------------------------------------------------ teamtailor
def test_teamtailor_parses_rss(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, TEAMTAILOR)
    route = mock.get(f"{TEAMTAILOR}/jobs.rss").mock(
        return_value=httpx.Response(200, text=load_text("teamtailor/jobs.rss"))
    )
    posts = TeamtailorConnector(client, "Lumen Labs AI", "lumenlabs").fetch()
    assert route.call_count == 1
    assert len(posts) == 2
    p = posts[0]
    assert p.source == "teamtailor" and p.external_id == "7000001"
    assert p.url == f"{TEAMTAILOR}/jobs/7000001-machine-learning-intern"
    assert p.location == "Amsterdam, Netherlands; Rome, Italy"
    assert p.country == "Netherlands"
    assert p.department == "Research"
    assert p.posted_at is not None and p.posted_at.isoformat() == "2026-10-05T08:15:00+00:00"
    assert "small models" in p.raw_text and "&lt;" not in p.raw_text and "<p>" not in p.raw_text
    assert p.extra["remote_status"] == "hybrid"
    assert posts[1].location is None


def test_teamtailor_custom_host_filter_and_robots(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    base = "https://careers.lumenlabs.example"
    robots(mock, base)
    mock.get(f"{base}/jobs.rss").mock(
        return_value=httpx.Response(200, text=load_text("teamtailor/jobs.rss"))
    )
    posts = TeamtailorConnector(
        client, "Lumen", "careers.lumenlabs.example", title_filter=FILTER
    ).fetch()
    assert [p.title for p in posts] == ["Machine Learning Intern"]

    robots(mock, TEAMTAILOR, DISALLOW)
    with pytest.raises(RobotsDisallowed):
        TeamtailorConnector(client, "Lumen", "lumenlabs").fetch()


def test_teamtailor_not_rss(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, TEAMTAILOR)
    mock.get(f"{TEAMTAILOR}/jobs.rss").mock(return_value=httpx.Response(200, text="not xml <"))
    with pytest.raises(SourceError, match="not valid XML"):
        TeamtailorConnector(client, "Lumen", "lumenlabs").fetch()


# ------------------------------------------------------------------ recruitee
def test_recruitee_parses_offers(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, RECRUITEE)
    route = mock.get(f"{RECRUITEE}/api/offers/").mock(
        return_value=httpx.Response(200, json=load_json("recruitee/offers.json"))
    )
    posts = RecruiteeConnector(client, "Orbis Consulting", "orbis").fetch()
    assert route.call_count == 1
    assert [p.external_id for p in posts] == ["900001", "900002"]  # archived offer skipped
    p = posts[0]
    assert p.source == "recruitee"
    assert p.url == "https://orbis.recruitee.com/o/junior-data-analyst"
    assert p.location == "Amsterdam, Noord-Holland, Netherlands" and p.country == "NL"
    assert p.deadline is not None and p.deadline.isoformat() == "2026-11-30"
    assert p.posted_at is not None and p.posted_at.isoformat() == "2026-10-01T08:00:00+00:00"
    assert "fictional consulting" in p.raw_text and "- SQL" in p.raw_text
    assert posts[1].deadline is None


def test_recruitee_filter_and_robots(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, RECRUITEE)
    mock.get(f"{RECRUITEE}/api/offers/").mock(
        return_value=httpx.Response(200, json=load_json("recruitee/offers.json"))
    )
    posts = RecruiteeConnector(client, "Orbis", "orbis", title_filter=FILTER).fetch()
    assert [p.title for p in posts] == ["Junior Data Analyst"]

    robots(mock, "https://other.recruitee.com", "User-agent: *\nDisallow: /api/\n")
    with pytest.raises(RobotsDisallowed):
        RecruiteeConnector(client, "Other", "other").fetch()


# ------------------------------------------------------------------ inrecruiting
SLUG = "bancaesempio|bancaesempio.example|publicsitekey"


def test_inrecruiting_slug_parsing() -> None:
    feed = parse_slug(SLUG)
    assert feed is not None
    assert feed.url == f"{INREC}/annunci.php"
    assert (feed.domain, feed.key) == ("bancaesempio.example", "publicsitekey")
    assert parse_slug("bancaesempio") is None
    assert parse_slug("bad host|d|k") is None


def test_inrecruiting_parses_feed(client: PoliteClient, mock: respx.MockRouter) -> None:
    robots(mock, INREC)
    feed = parse_slug(SLUG)
    assert feed is not None
    route = mock.get(
        f"{INREC}/annunci.php",
        params={
            "lang": "it",
            "d": "bancaesempio.example",
            "k": "publicsitekey",
            "format": "json_en",
        },
    ).mock(return_value=httpx.Response(200, json=load_json("inrecruiting/annunci.json")))
    posts = InRecruitingConnector(client, "Banca Esempio", feed).fetch()
    assert route.call_count == 1
    assert len(posts) == 2
    p = posts[0]
    assert p.source == "inrecruiting" and p.external_id == "4101"
    assert p.url == f"{INREC}/jobs/stage-risk-management-4101/it/"
    assert p.location == "Milano" and p.country == "Italia"
    # Rome local 18:01 in October (CEST) = 16:01 UTC; deadline keeps the local date
    assert p.posted_at is not None and p.posted_at.isoformat() == "2026-10-12T16:01:00+00:00"
    assert p.deadline is not None and p.deadline.isoformat() == "2026-11-30"
    assert p.employment_type == "Stage" and p.department == "Risk Management"
    assert "tirocinante" in p.raw_text and "<p>" not in p.raw_text
    assert posts[1].deadline is None
    assert "Gestione della filiale" in posts[1].raw_text  # falls back to section fields


def test_inrecruiting_filter_and_robots(client: PoliteClient, mock: respx.MockRouter) -> None:
    feed = parse_slug(SLUG)
    assert feed is not None
    robots(mock, INREC)
    mock.get(f"{INREC}/annunci.php").mock(
        return_value=httpx.Response(200, json=load_json("inrecruiting/annunci.json"))
    )
    posts = InRecruitingConnector(client, "Banca Esempio", feed, title_filter=FILTER).fetch()
    assert [p.title for p in posts] == ["Stage Risk Management"]

    other = parse_slug("altra|altra.example|k")
    assert other is not None
    robots(mock, "https://altra.intervieweb.it", "User-agent: *\nDisallow: /annunci\n")
    route = mock.get("https://altra.intervieweb.it/annunci.php")
    with pytest.raises(RobotsDisallowed):
        InRecruitingConnector(client, "Altra", other).fetch()
    assert route.call_count == 0


def test_inrecruiting_bad_format(client: PoliteClient, mock: respx.MockRouter) -> None:
    feed = parse_slug(SLUG)
    assert feed is not None
    robots(mock, INREC)
    mock.get(f"{INREC}/annunci.php").mock(return_value=httpx.Response(200, json={"e": 1}))
    with pytest.raises(SourceError, match="unexpected"):
        InRecruitingConnector(client, "Banca Esempio", feed).fetch()


# ------------------------------------------------------------------ registry
def test_registry_builds_new_ats_connectors(client: PoliteClient) -> None:
    from radar.api.routes.settings import DEFAULT_SOURCES
    from radar.connectors.registry import ATS_SOURCES, build_connectors
    from radar.pipeline.closed import is_exhaustive

    cfg = {
        "title_filter": ["intern"],
        "companies": [
            {"name": "A", "ats": "personio", "slug": "a", "region": "com", "lang": "en"},
            {"name": "B", "ats": "workable", "slug": "b", "title_filter": False},
            {"name": "C", "ats": "teamtailor", "slug": "careers.c.example"},
            {"name": "D", "ats": "recruitee", "slug": "d", "sector": "consulting"},
            {"name": "E", "ats": "inrecruiting", "slug": "e|e.example|key", "lang": "en"},
            {"name": "F", "ats": "inrecruiting", "slug": "malformed"},
            {"name": "G", "ats": "workable", "slug": "g", "active": False},
        ],
    }
    conns = build_connectors(client, cfg, sources=list(ATS_SOURCES))
    by_type = {type(c).__name__: c for c in conns}
    assert set(by_type) == {
        "PersonioConnector",
        "WorkableConnector",
        "TeamtailorConnector",
        "RecruiteeConnector",
        "InRecruitingConnector",
    }
    p = by_type["PersonioConnector"]
    assert isinstance(p, PersonioConnector)
    assert (p.region, p.lang, p.title_filter) == ("com", "en", ["intern"])
    w = by_type["WorkableConnector"]
    assert isinstance(w, WorkableConnector) and w.title_filter is None
    ir = by_type["InRecruitingConnector"]
    assert isinstance(ir, InRecruitingConnector)
    assert (ir.feed.host, ir.lang) == ("e", "en")
    assert all(is_exhaustive(c) for c in conns)
    assert [c.label for c in conns] == [
        "personio board #1",
        "workable board #1",
        "teamtailor board #1",
        "recruitee board #1",
        "inrecruiting board #1",
    ]
    for name in ("personio", "workable", "teamtailor", "recruitee", "inrecruiting"):
        assert name in ATS_SOURCES and name in DEFAULT_SOURCES
    only = build_connectors(client, cfg, sources=["recruitee"])
    assert [type(c).__name__ for c in only] == ["RecruiteeConnector"]


# ------------------------------------------------------------------ scripts/check_boards.py
def test_check_boards_probe_counts(client: PoliteClient, mock: respx.MockRouter) -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_boards.py"
    spec = importlib.util.spec_from_file_location("check_boards", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    robots(mock, RECRUITEE)
    mock.get(f"{RECRUITEE}/api/offers/").mock(
        return_value=httpx.Response(200, json=load_json("recruitee/offers.json"))
    )
    entry = {"name": "Orbis", "ats": "recruitee", "slug": "orbis", "sector": "consulting"}
    row = mod.probe(client, entry, ["junior"])
    assert (row["status"], row["jobs"], row["kept"], row["sector"]) == ("ok", 2, 1, "consulting")

    robots(mock, "https://blocked.recruitee.com", DISALLOW)
    row = mod.probe(client, {"ats": "recruitee", "slug": "blocked"}, None)
    assert row["status"] == "robots" and row["jobs"] == 0
    row = mod.probe(client, {"ats": "inrecruiting", "slug": "broken"}, None)
    assert row["status"] == "error" and row["reason"] == "malformed entry"
