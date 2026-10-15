"""LinkedIn guest connector (synthetic fixtures: robots.txt forbids recording live pages)."""

from __future__ import annotations

import httpx
import pytest
import respx
from conn_helpers import load_text

from radar.connectors.base import SourceError
from radar.connectors.http import PoliteClient
from radar.connectors.linkedin_guest import (
    LinkedInGuestConnector,
    parse_detail,
    parse_job_id,
    parse_search_cards,
)

ROBOTS = "https://www.linkedin.com/robots.txt"
SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
QUERIES = [{"keywords": "AI engineer intern", "location": "Italy"}]
# Shape of the real file: named crawlers listed, then a catch-all disallow.
REAL_STYLE_ROBOTS = (
    "User-agent: LinkedInBot\nAllow: /\n\nUser-agent: Googlebot\nDisallow: /jobs-guest/\n\n"
    "User-agent: *\nDisallow: /\n"
)


def test_robots_disallow_is_reported_not_evaded(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    mock.get(ROBOTS).mock(return_value=httpx.Response(200, text=REAL_STYLE_ROBOTS))
    search = mock.get(SEARCH).mock(return_value=httpx.Response(200, text=""))
    with pytest.raises(SourceError, match=r"robots\.txt"):
        LinkedInGuestConnector(client, QUERIES).fetch()
    assert search.call_count == 0


def test_parse_search_cards() -> None:
    cards = parse_search_cards(load_text("linkedin/search.html"))
    assert [c["id"] for c in cards] == ["4100000001", "4100000002", "4100000003"]
    first = cards[0]
    assert first["title"] == "AI Engineer Intern"
    assert first["company"] == "Nebula Analytics"
    assert first["location"] == "Milan, Lombardy, Italy"
    assert first["date"] == "2026-10-10"
    assert first["url"] == (
        "https://it.linkedin.com/jobs/view/ai-engineer-intern-at-nebula-analytics-4100000001"
    )


def test_parse_detail_open_and_closed() -> None:
    open_ = parse_detail(load_text("linkedin/detail_open.html"))
    assert open_["closed"] is False
    assert "Build LLM-powered tools" in open_["description"]
    assert "- Python and PyTorch" in open_["description"]
    assert open_["criteria"]["Employment type"] == "Internship"
    assert parse_detail(load_text("linkedin/detail_closed.html"))["closed"] is True


def test_fetch_when_allowed(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(ROBOTS).mock(return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n"))
    search = mock.get(SEARCH).mock(
        return_value=httpx.Response(200, text=load_text("linkedin/search.html"))
    )
    d1 = mock.get(DETAIL.format(id="4100000001")).mock(
        return_value=httpx.Response(200, text=load_text("linkedin/detail_open.html"))
    )
    d3 = mock.get(DETAIL.format(id="4100000003")).mock(
        return_value=httpx.Response(200, text=load_text("linkedin/detail_closed.html"))
    )
    d2 = mock.get(DETAIL.format(id="4100000002")).mock(return_value=httpx.Response(200))
    conn = LinkedInGuestConnector(
        client, QUERIES, max_details=5, title_filter=["intern", "graduate"]
    )
    posts = conn.fetch()
    req = search.calls.last.request
    assert req.url.params["f_TPR"] == "r604800" and req.url.params["start"] == "0"
    assert d1.call_count == 1 and d3.call_count == 1
    assert d2.call_count == 0  # filtered out by title
    assert [p.external_id for p in posts] == ["4100000001"]  # closed one dropped
    p = posts[0]
    assert p.source == "linkedin"
    assert p.url == "https://www.linkedin.com/jobs/view/4100000001"
    assert p.employment_type == "Internship"
    assert "PyTorch" in p.raw_text


def test_detail_limit(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(ROBOTS).mock(return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n"))
    mock.get(SEARCH).mock(return_value=httpx.Response(200, text=load_text("linkedin/search.html")))
    detail = mock.get(
        url__startswith="https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"
    ).mock(return_value=httpx.Response(200, text=load_text("linkedin/detail_open.html")))
    posts = LinkedInGuestConnector(client, QUERIES, max_details=1).fetch()
    assert detail.call_count == 1
    assert len(posts) == 3
    assert sum(1 for p in posts if p.extra["detail_fetched"]) == 1


def test_parse_job_id() -> None:
    assert parse_job_id(
        "https://it.linkedin.com/jobs/view/junior-ai-engineer-at-kpmg-4464880857"
    ) == ("4464880857")
    assert parse_job_id("https://www.linkedin.com/jobs/view/4461118875/") == "4461118875"
    assert parse_job_id("https://www.linkedin.com/jobs/search?currentJobId=123456") == "123456"
    assert parse_job_id("https://www.linkedin.com/company/x") is None
