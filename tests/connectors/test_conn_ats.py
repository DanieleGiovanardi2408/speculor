"""Greenhouse, Lever, Ashby, SmartRecruiters on recorded (trimmed) real responses."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from conn_helpers import load_json

from radar.connectors.ashby import AshbyConnector
from radar.connectors.base import Connector, SourceError
from radar.connectors.greenhouse import GreenhouseConnector
from radar.connectors.greenhouse import parse_posting_url as gh_parse
from radar.connectors.http import PoliteClient
from radar.connectors.lever import LeverConnector
from radar.connectors.lever import parse_posting_url as lever_parse
from radar.connectors.smartrecruiters import SmartRecruitersConnector

GH_URL = "https://boards-api.greenhouse.io/v1/boards/rondinepay/jobs"
LEVER_EU_URL = "https://api.eu.lever.co/v0/postings/velainsurance"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/tascapay"
SR_URL = "https://api.smartrecruiters.com/v1/companies/AlturaConsulting/postings"
FILTER = ["intern", "junior", "analyst", "graduate"]


# ------------------------------------------------------------------ greenhouse
def test_greenhouse_parses_board(client: PoliteClient, mock: respx.MockRouter) -> None:
    route = mock.get(GH_URL, params={"content": "true"}).mock(
        return_value=httpx.Response(200, json=load_json("greenhouse/board.json"))
    )
    conn = GreenhouseConnector(client, "Rondine Pay", "rondinepay")
    assert isinstance(conn, Connector)
    posts = conn.fetch()
    assert route.call_count == 1
    assert len(posts) == 3
    p = next(x for x in posts if x.external_id == "4739799101")
    assert p.source == "greenhouse"
    assert p.company == "Rondine Pay"
    assert p.title.startswith("In-Store Operations intern")
    assert p.location == "Milan, Italy; Paris, Paris, France"
    assert p.url.startswith("https://job-boards.eu.greenhouse.io/rondinepay/jobs/")
    assert p.posted_at is not None and p.posted_at.year == 2025
    assert "&lt;" not in p.raw_text and "<p>" not in p.raw_text
    assert "At Rondine Pay" in p.raw_text
    assert p.department


def test_greenhouse_title_filter_and_deadline(client: PoliteClient, mock: respx.MockRouter) -> None:
    data = load_json("greenhouse/board.json")
    data["jobs"][0]["application_deadline"] = "2026-11-15"
    mock.get(GH_URL).mock(return_value=httpx.Response(200, json=data))
    posts = GreenhouseConnector(client, "Rondine Pay", "rondinepay", title_filter=FILTER).fetch()
    titles = {p.title for p in posts}
    assert titles == {
        "Credit Risk Analyst",
        "In-Store Operations intern - bilingual French/Italian (6-months)",
    }
    analyst = next(p for p in posts if p.title == "Credit Risk Analyst")
    assert analyst.deadline is not None and analyst.deadline.isoformat() == "2026-11-15"


def test_greenhouse_bad_slug_raises(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get("https://boards-api.greenhouse.io/v1/boards/nope/jobs").mock(
        return_value=httpx.Response(404, json={"status": 404, "error": "Job board not found"})
    )
    with pytest.raises(SourceError, match="404"):
        GreenhouseConnector(client, "X", "nope").fetch()


def test_greenhouse_url_parsing() -> None:
    assert gh_parse("https://job-boards.eu.greenhouse.io/ondatrading/jobs/4939821101") == (
        "ondatrading",
        "4939821101",
    )
    assert gh_parse("https://job-boards.greenhouse.io/flussodata/jobs/7989895003?gh_jid=1") == (
        "flussodata",
        "7989895003",
    )
    assert gh_parse("https://careers.monitorly.example/detail/8181440/?gh_jid=8181440") is None


# ------------------------------------------------------------------ lever
def test_lever_eu_parses_board(client: PoliteClient, mock: respx.MockRouter) -> None:
    route = mock.get(LEVER_EU_URL, params={"mode": "json"}).mock(
        return_value=httpx.Response(200, json=load_json("lever/board.json"))
    )
    posts = LeverConnector(client, "Vela Insurance", "velainsurance", region="eu").fetch()
    assert route.call_count == 1
    assert len(posts) == 3
    p = next(x for x in posts if x.title == "Junior Product Manager")
    assert p.source == "lever"
    assert p.country == "IT"
    assert p.location == "Milan"
    assert p.url == "https://jobs.eu.lever.co/velainsurance/c596f285-996a-448f-9ca0-326f20f1ed3b"
    assert p.posted_at is not None and p.posted_at.tzinfo is not None
    assert p.raw_text and "<" not in p.raw_text
    assert p.department


def test_lever_global_host_and_filter(client: PoliteClient, mock: respx.MockRouter) -> None:
    route = mock.get("https://api.lever.co/v0/postings/velainsurance").mock(
        return_value=httpx.Response(200, json=load_json("lever/board.json"))
    )
    posts = LeverConnector(client, "Vela Insurance", "velainsurance", title_filter=FILTER).fetch()
    assert route.call_count == 1
    assert {p.title for p in posts} == {"Data Analyst", "Junior Product Manager"}


def test_lever_unexpected_format(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get("https://api.lever.co/v0/postings/x").mock(
        return_value=httpx.Response(200, json={"ok": False})
    )
    with pytest.raises(SourceError, match="unexpected"):
        LeverConnector(client, "X", "x").fetch()


def test_lever_url_parsing() -> None:
    assert lever_parse("https://jobs.eu.lever.co/velainsurance/1b650a6c-4649-43f4-854b-a3decfb9a006") == (
        "velainsurance",
        "1b650a6c-4649-43f4-854b-a3decfb9a006",
        "eu",
    )
    parsed = lever_parse("https://jobs.lever.co/quarzo/1b6f1d82-d459-4dea-8bc2-8d2ffe6f881a")
    assert parsed is not None and parsed[2] is None


# ------------------------------------------------------------------ ashby
def test_ashby_parses_board_and_skips_unlisted(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    data: dict[str, Any] = load_json("ashby/board.json")
    data["jobs"][2]["isListed"] = False
    mock.get(ASHBY_URL, params={"includeCompensation": "false"}).mock(
        return_value=httpx.Response(200, json=data)
    )
    posts = AshbyConnector(client, "Tasca Pay", "tascapay").fetch()
    assert len(posts) == 2
    p = posts[0]
    assert p.source == "ashby"
    assert p.country == "Italy"
    assert p.location == "Milan, Italy"
    assert p.url.startswith("https://jobs.ashbyhq.com/tascapay/")
    assert p.posted_at is not None
    assert p.raw_text


def test_ashby_title_filter(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(ASHBY_URL).mock(return_value=httpx.Response(200, json=load_json("ashby/board.json")))
    posts = AshbyConnector(client, "Tasca Pay", "tascapay", title_filter=["junior"]).fetch()
    assert len(posts) == 2 and all("Junior" in p.title for p in posts)


# ------------------------------------------------------------------ smartrecruiters
def _mock_sr(mock: respx.MockRouter) -> dict[str, respx.Route]:
    routes = {
        "page1": mock.get(SR_URL, params={"offset": "0", "limit": "2"}).mock(
            return_value=httpx.Response(200, json=load_json("smartrecruiters/page1.json"))
        ),
        "page2": mock.get(SR_URL, params={"offset": "2", "limit": "2"}).mock(
            return_value=httpx.Response(200, json=load_json("smartrecruiters/page2.json"))
        ),
    }
    for pid in ("744000155707965", "744000155242046", "744000155205063"):
        routes[pid] = mock.get(f"{SR_URL}/{pid}").mock(
            return_value=httpx.Response(200, json=load_json(f"smartrecruiters/detail_{pid}.json"))
        )
    routes["not_prefiltered"] = mock.get(f"{SR_URL}/744000155707851").mock(
        return_value=httpx.Response(500)
    )
    return routes


def test_smartrecruiters_paginates_and_fetches_details_for_prefiltered_only(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    routes = _mock_sr(mock)
    conn = SmartRecruitersConnector(client, "Altura Consulting", "AlturaConsulting", page_size=2)
    items = conn.list_postings()
    assert len(items) == 4  # two pages
    posts = conn.fetch()
    assert routes["page1"].call_count == 1 and routes["page2"].call_count == 1  # cached 2nd time
    assert routes["not_prefiltered"].call_count == 0  # "Training Specialist" fails prefilter
    assert len(posts) == 3
    zurich = next(p for p in posts if p.external_id == "744000155205063")
    assert zurich.country == "CH"
    assert zurich.url.startswith("https://jobs.smartrecruiters.com/AlturaConsulting/744000155205063")
    assert zurich.employment_type
    assert "Stellenbeschreibung" in zurich.raw_text and "<p>" not in zurich.raw_text
    assert zurich.extra["detail_fetched"] is True


def test_smartrecruiters_detail_cap(client: PoliteClient, mock: respx.MockRouter) -> None:
    routes = _mock_sr(mock)
    posts = SmartRecruitersConnector(
        client, "RB", "AlturaConsulting", max_details=1, page_size=2
    ).fetch()
    assert len(posts) == 3
    assert sum(1 for p in posts if p.extra["detail_fetched"]) == 1
    assert routes["744000155205063"].call_count == 0


def test_smartrecruiters_custom_title_filter(client: PoliteClient, mock: respx.MockRouter) -> None:
    _mock_sr(mock)
    posts = SmartRecruitersConnector(
        client, "RB", "AlturaConsulting", title_filter=["recruiting"], page_size=2
    ).fetch()
    assert [p.external_id for p in posts] == ["744000155242046"]


def test_smartrecruiters_429_raises(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(SR_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
    with pytest.raises(SourceError, match="429"):
        SmartRecruitersConnector(client, "RB", "AlturaConsulting").fetch()
