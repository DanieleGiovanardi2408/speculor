from __future__ import annotations

import httpx
import respx
from conn_helpers import load_json, load_text
from sqlmodel import Session, select

from radar.connectors.http import PoliteClient
from radar.models import Posting
from radar.pipeline.manual import add_posting_from_url, parse_jobposting_jsonld
from radar.pipeline.normalize import canonical_url, sha256

PAGE = "https://careers.fiordo.example/jobs/junior-data-analyst"


def _allow_robots(mock: respx.MockRouter, host: str) -> None:
    mock.get(f"https://{host}/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )


def test_manual_add_with_jsonld(
    session: Session, client: PoliteClient, mock: respx.MockRouter
) -> None:
    _allow_robots(mock, "careers.fiordo.example")
    page = mock.get(PAGE).mock(
        return_value=httpx.Response(200, text=load_text("manual/jsonld_page.html"))
    )
    p = add_posting_from_url(session, PAGE + "?utm_source=x", client=client)
    assert p.id is not None
    assert p.source == "manual"
    canon = canonical_url(PAGE)
    assert p.canonical_url == canon
    assert p.uid == f"manual:{sha256(canon)[:16]}"
    assert p.title == "Junior Data Analyst"
    assert p.company_name == "Fiordo Payments"
    assert p.location == "Milano, Lombardia, IT"
    assert p.country_code == "IT" and p.zone == "Italia"
    assert p.deadline is not None and p.deadline.isoformat() == "2026-11-30"
    assert p.raw_text is not None and "- SQL" in p.raw_text
    assert p.content_hash and p.dedupe_key.startswith("fiordo payments|junior data analyst")
    assert p.status == "open"

    again = add_posting_from_url(session, PAGE, client=client)  # same canonical URL
    assert again.id == p.id
    assert page.call_count == 1
    assert len(session.exec(select(Posting)).all()) == 1


def test_manual_add_plain_html_fallback(
    session: Session, client: PoliteClient, mock: respx.MockRouter
) -> None:
    url = "https://orbis.example/careers/strategy-intern"
    _allow_robots(mock, "orbis.example")
    mock.get(url).mock(return_value=httpx.Response(200, text=load_text("manual/plain_page.html")))
    p = add_posting_from_url(session, url, client=client)
    assert p.title == "Strategy Intern (6 months)"
    assert p.company_name == "Orbis Consulting"
    assert p.raw_text is not None
    assert "AI strategy projects" in p.raw_text
    assert "var x" not in p.raw_text and "Imprint" not in p.raw_text


def test_manual_add_robots_disallowed_creates_stub(
    session: Session, client: PoliteClient, mock: respx.MockRouter
) -> None:
    url = "https://blocked.example/jobs/ml-engineer-intern"
    mock.get("https://blocked.example/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    page = mock.get(url)
    p = add_posting_from_url(session, url, client=client)
    assert page.call_count == 0
    assert p.status == "unknown"
    assert p.title == "Ml engineer intern"


def test_manual_add_linkedin_url_is_not_fetched(
    session: Session, client: PoliteClient, mock: respx.MockRouter
) -> None:
    url = "https://it.linkedin.com/jobs/view/junior-ai-engineer-at-nebula-analytics-4464880857"
    p = add_posting_from_url(session, url, client=client)  # respx fails on any request
    assert p.canonical_url == "https://www.linkedin.com/jobs/view/4464880857"
    assert p.title == "Junior ai engineer"
    assert p.company_name == "Nebula Analytics"
    assert p.status == "unknown"


def test_manual_add_greenhouse_uses_api(
    session: Session, client: PoliteClient, mock: respx.MockRouter
) -> None:
    job = load_json("greenhouse/board.json")["jobs"][1]
    mock.get(f"https://boards-api.greenhouse.io/v1/boards/rondinepay/jobs/{job['id']}").mock(
        return_value=httpx.Response(200, json=job)
    )
    url = f"https://job-boards.eu.greenhouse.io/rondinepay/jobs/{job['id']}"
    p = add_posting_from_url(session, url, client=client)
    assert p.title == job["title"]
    assert p.company_name == "Rondine Pay"
    assert p.country_code == "IT"
    assert p.status == "open"


def test_parse_jsonld_ignores_bad_blocks() -> None:
    html = (
        '<script type="application/ld+json">{bad json</script>'
        '<script type="application/ld+json">[{"@type": "WebSite"},'
        '{"@type": ["JobPosting"], "title": "T"}]</script>'
    )
    job = parse_jobposting_jsonld(html)
    assert job is not None and job["title"] == "T"
    assert parse_jobposting_jsonld("<p>none</p>") is None
