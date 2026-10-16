"""Workday CXS connector on recorded (trimmed) real responses; respx mocks, no live network."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from conn_helpers import SleepRecorder, load_json

from radar.connectors.base import Connector, SourceError
from radar.connectors.closed import check_open
from radar.connectors.http import PoliteClient, RobotsDisallowed
from radar.connectors.registry import build_connectors
from radar.connectors.workday import (
    Board,
    WorkdayConnector,
    board_from_entry,
    parse_posting_url,
    parse_slug,
)

HOST = "https://civetta.wd3.myworkdayjobs.com"
API = f"{HOST}/wday/cxs/civetta/ExternalCareers"
JOBS = f"{API}/jobs"
ROBOTS = f"{HOST}/robots.txt"
ROBOTS_OK = "User-agent: *\nAllow: /ExternalCareers/\nDisallow: /refreshFacet/\n"
HR_PATH = "/job/Roma--Via-Esempio/HR-Talent-Acquisition-Intern_JR105286"
FILTER = ["intern", "internship", "junior", "graduate", "stage"]
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _body(request: httpx.Request) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(request.content)
    return data


def _pages(request: httpx.Request) -> httpx.Response:
    """Serve recorded search pages by (searchText, offset)."""
    body = _body(request)
    name = {
        ("intern", 0): "jobs_intern_p1.json",
        ("intern", 20): "jobs_intern_p2.json",
        ("junior", 0): "jobs_junior_p1.json",
    }.get((body["searchText"], body["offset"]))
    if name is None:
        return httpx.Response(200, json={"total": 0, "jobPostings": [], "facets": []})
    return httpx.Response(200, json=load_json(f"workday/{name}"))


def _detail(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith(HR_PATH):
        return httpx.Response(200, json=load_json("workday/detail.json"))
    return httpx.Response(404, json={"errorCode": "S21", "httpStatus": 404})


def _mock_board(mock: respx.MockRouter, robots: str = ROBOTS_OK) -> tuple[respx.Route, respx.Route]:
    mock.get(ROBOTS).mock(return_value=httpx.Response(200, text=robots))
    search = mock.post(JOBS).mock(side_effect=_pages)
    detail = mock.get(url__startswith=f"{API}/job/").mock(side_effect=_detail)
    return search, detail


def _conn(client: PoliteClient, **kw: Any) -> WorkdayConnector:
    kw.setdefault("title_filter", FILTER)
    kw.setdefault("search_terms", ["intern"])
    return WorkdayConnector(client, "Civetta Tech", "civetta|wd3|ExternalCareers", now=NOW, **kw)


# ------------------------------------------------------------------ slug / URL parsing
def test_slug_and_url_parsing() -> None:
    b = parse_slug("civetta|wd3|ExternalCareers")
    assert b == Board("civetta", "wd3", "ExternalCareers")
    assert b.api == JOBS.removesuffix("/jobs")
    assert parse_slug("https://airone.wd1.myworkdayjobs.com/aironecareers").slug == "airone|wd1|aironecareers"
    with pytest.raises(ValueError):
        parse_slug("civetta/ExternalCareers")
    assert (
        board_from_entry(
            {"tenant": "civetta", "host": "civetta.wd3.myworkdayjobs.com", "site": "ExternalCareers"}
        )
        == b
    )
    assert board_from_entry({"tenant": "civetta", "host": "wd3", "site": "ExternalCareers"}) == b
    assert board_from_entry({"slug": "bad"}) is None

    got = parse_posting_url(f"{HOST}/en-US/ExternalCareers{HR_PATH}")
    assert got == (b, HR_PATH)
    assert parse_posting_url(f"{HOST}/ExternalCareers{HR_PATH}?source=x") == (b, HR_PATH)
    assert parse_posting_url(f"{HOST}/ExternalCareers") == (b, None)
    assert parse_posting_url("https://example.com/ExternalCareers/job/x") is None


# ------------------------------------------------------------------ fetch
def test_fetch_parses_paginates_and_filters(
    client: PoliteClient, mock: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    search, detail = _mock_board(mock)
    conn = _conn(client)
    assert isinstance(conn, Connector)
    posts = conn.fetch()

    # total=26 on page 1 -> exactly one more page at offset 20, then stop
    offsets = [_body(c.request)["offset"] for c in search.calls]
    assert offsets == [0, 20]
    assert all(_body(c.request)["limit"] == 20 for c in search.calls)
    assert all(_body(c.request)["appliedFacets"] == {} for c in search.calls)
    assert search.calls[0].request.headers["User-Agent"].startswith("speculor-radar-test")
    assert "cookie" not in search.calls[0].request.headers

    titles = [p.title for p in posts]
    raw_items = (
        load_json("workday/jobs_intern_p1.json")["jobPostings"]
        + load_json("workday/jobs_intern_p2.json")["jobPostings"]
    )
    assert len(raw_items) == 26
    # Workday search also matches descriptions: the local title filter drops "Internal ..." etc.
    assert "Internal Audit Manager" not in titles
    assert 0 < len(posts) < len(raw_items)

    hr = next(p for p in posts if p.url.endswith(HR_PATH))
    assert hr.source == "workday"
    assert hr.external_id == "civetta:HR-Talent-Acquisition-Intern_JR105286"
    assert hr.company == "Civetta Tech"
    assert hr.title == "HR Talent Acquisition Intern"
    assert hr.country == "IT"
    assert hr.location and hr.location.startswith("Roma")
    assert hr.employment_type == "Full time"
    assert hr.posted_at == datetime(2026, 10, 6, tzinfo=UTC)
    assert hr.deadline is not None and hr.deadline.isoformat() == "2026-10-21"
    assert "Talent Acquisition" in hr.raw_text and "<p" not in hr.raw_text
    assert hr.extra["req_id"] == "JR105286" and hr.extra["detail_fetched"] is True

    # postings whose detail 404s are kept without description; posted_at from "Posted N Days Ago"
    others = [p for p in posts if p is not hr]
    assert others and all(p.raw_text == "" for p in others)
    assert all(p.country is None for p in others)
    assert detail.call_count == len(posts)
    # >= 2 s between two requests to the same Workday host
    assert client.intervals["myworkdayjobs.com"] >= 2.0
    assert any(s > 1.5 for s in sleeps.calls)


def test_relative_posted_on(client: PoliteClient, mock: respx.MockRouter) -> None:
    _mock_board(mock)
    posts = _conn(client, max_details=0).fetch()
    assert all(not p.extra["detail_fetched"] for p in posts)
    by_label = {p.extra["posted_on"]: p.posted_at for p in posts}
    for label, when in by_label.items():
        if "30+" in str(label):
            assert when is None
        elif "Today" in str(label):
            assert when == datetime(2026, 10, 16, tzinfo=UTC)
        elif "Days Ago" in str(label):
            assert when is not None and when < NOW


def test_search_terms_loop_dedupes(client: PoliteClient, mock: respx.MockRouter) -> None:
    search, _ = _mock_board(mock)
    conn = _conn(client, search_terms=["intern", "junior"], title_filter=None, max_details=0)
    items = conn.list_postings()
    paths = [
        j["externalPath"]
        for name in ("jobs_intern_p1.json", "jobs_intern_p2.json", "jobs_junior_p1.json")
        for j in load_json(f"workday/{name}")["jobPostings"]
    ]
    assert len(paths) > len(set(paths))  # the recorded searches overlap
    assert len(items) == len(set(paths))
    assert [(_body(c.request)["searchText"], _body(c.request)["offset"]) for c in search.calls] == [
        ("intern", 0),
        ("intern", 20),
        ("junior", 0),
    ]


def test_cache_avoids_repeat_posts(client: PoliteClient, mock: respx.MockRouter) -> None:
    search, _ = _mock_board(mock)
    _conn(client, max_details=0).fetch()
    n = search.call_count
    _conn(client, max_details=0).fetch()
    assert search.call_count == n


def test_country_facet_detection(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get("https://gufo.wd1.myworkdayjobs.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /GUFO/\n")
    )
    first = load_json("workday/facets_country.json")
    facet = next(f for f in first["facets"] if f["facetParameter"] == "Location_Country")
    italy = next(v["id"] for v in facet["values"] if v["descriptor"] == "Italy")

    def answer(request: httpx.Request) -> httpx.Response:
        if _body(request)["appliedFacets"]:
            return httpx.Response(200, json={**first, "total": 3})
        return httpx.Response(200, json=first)

    route = mock.post("https://gufo.wd1.myworkdayjobs.com/wday/cxs/gufo/GUFO/jobs").mock(
        side_effect=answer
    )
    conn = WorkdayConnector(
        client, "X", "gufo|wd1|GUFO", countries=["Italy"], search_terms=["intern"], max_details=0
    )
    conn.list_postings()
    assert conn.scope == "country facet"
    assert _body(route.calls[0].request)["appliedFacets"] == {}
    assert _body(route.calls[-1].request)["appliedFacets"] == {"Location_Country": [italy]}
    # "europe" expands to the built-in list (Italy included)
    assert "Italy" in WorkdayConnector(client, "X", "gufo|wd1|GUFO", countries="europe").countries


# ------------------------------------------------------------------ politeness / failures
def test_robots_disallow_stops_before_any_request(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    search, _ = _mock_board(mock, robots="User-agent: *\nDisallow: /wday/cxs/\n")
    with pytest.raises(RobotsDisallowed, match=r"robots\.txt"):
        _conn(client).fetch()
    assert search.call_count == 0


def test_429_backoff_then_success(
    client: PoliteClient, mock: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    mock.get(ROBOTS).mock(return_value=httpx.Response(200, text=ROBOTS_OK))
    route = mock.post(JOBS).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=load_json("workday/jobs_junior_p1.json")),
        ]
    )
    items = _conn(client, search_terms=["junior"]).list_postings()
    assert route.call_count == 2
    assert 7.0 in sleeps.calls
    assert items


def test_403_retries_then_reports_session(
    client: PoliteClient, mock: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    mock.get(ROBOTS).mock(return_value=httpx.Response(200, text=ROBOTS_OK))
    route = mock.post(JOBS).mock(return_value=httpx.Response(403))
    with pytest.raises(SourceError, match="403 after 3 retries"):
        _conn(client).list_postings()
    assert route.call_count == 4  # 1 + max_retries
    assert [s for s in sleeps.calls if s >= 5.0] == [5.0, 10.0, 20.0]


def test_csrf_required_stops_board(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(ROBOTS).mock(return_value=httpx.Response(200, text=ROBOTS_OK))
    route = mock.post(JOBS).mock(return_value=httpx.Response(422, json={"error": "csrf"}))
    with pytest.raises(SourceError, match="session/CSRF"):
        _conn(client).list_postings()
    assert route.call_count == 1  # no retry, no workaround


def test_unexpected_shape_raises(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(ROBOTS).mock(return_value=httpx.Response(200, text=ROBOTS_OK))
    mock.post(JOBS).mock(return_value=httpx.Response(200, json={"oops": 1}))
    with pytest.raises(SourceError, match="unexpected"):
        _conn(client).list_postings()


# ------------------------------------------------------------------ closed check / registry
def test_check_open_workday(client: PoliteClient, mock: respx.MockRouter) -> None:
    _mock_board(mock)
    assert check_open(client, f"{HOST}/en-US/ExternalCareers{HR_PATH}") is True
    assert check_open(client, f"{HOST}/ExternalCareers/job/Roma/Gone_JR1") is False
    assert check_open(client, f"{HOST}/ExternalCareers") is None  # board, not posting


def test_check_open_workday_robots_disallow(client: PoliteClient, mock: respx.MockRouter) -> None:
    _, detail = _mock_board(mock, robots="User-agent: *\nDisallow: /wday/\n")
    assert check_open(client, f"{HOST}/ExternalCareers{HR_PATH}") is None
    assert detail.call_count == 0


def test_registry_builds_workday(client: PoliteClient) -> None:
    cfg = {
        "title_filter": ["intern"],
        "companies": [
            {"name": "A", "ats": "workday", "slug": "civetta|wd3|ExternalCareers"},
            {
                "name": "B",
                "ats": "workday",
                "tenant": "airone",
                "host": "wd1",
                "site": "aironecareers",
                "countries": "europe",
                "search_terms": ["intern", "graduate"],
            },
            {"name": "C", "ats": "workday", "slug": "malformed"},
            {"name": "D", "ats": "greenhouse", "slug": "d"},
        ],
    }
    conns = build_connectors(client, cfg)
    wd = [c for c in conns if isinstance(c, WorkdayConnector)]
    assert [c.board.slug for c in wd] == ["civetta|wd3|ExternalCareers", "airone|wd1|aironecareers"]
    assert [c.label for c in wd] == ["workday board #1", "workday board #2"]
    assert wd[0].title_filter == ["intern"]
    assert wd[1].search_terms == ("intern", "graduate") and "Italy" in wd[1].countries
    assert len(conns) == 3
    assert all(c.source == "greenhouse" for c in build_connectors(client, cfg, ["greenhouse"]))
