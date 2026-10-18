"""describe_url: one posting's description from its URL (ATS API or careers page)."""

from __future__ import annotations

import httpx
import respx
from conn_helpers import load_json, load_text

from radar.connectors.http import PoliteClient
from radar.pipeline.describe import describe_url

GH_API = "https://boards-api.greenhouse.io/v1/boards"


def _robots(mock: respx.MockRouter, host: str, text: str = "") -> None:
    mock.get(f"https://{host}/robots.txt").mock(return_value=httpx.Response(200, text=text))


def test_ats_url_uses_the_api(client: PoliteClient, mock: respx.MockRouter) -> None:
    job = load_json("greenhouse/board.json")["jobs"][0]
    mock.get(f"{GH_API}/rondinepay/jobs/{job['id']}").mock(
        return_value=httpx.Response(200, json=job)
    )
    got = describe_url(client, f"https://job-boards.eu.greenhouse.io/rondinepay/jobs/{job['id']}")
    assert got.raw is not None and "Rondine Pay" in got.raw.raw_text
    assert not got.closed


def test_ats_404_is_closed(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(f"{GH_API}/rondinepay/jobs/1").mock(return_value=httpx.Response(404))
    got = describe_url(client, "https://job-boards.eu.greenhouse.io/rondinepay/jobs/1")
    assert got.closed and got.raw is None


def test_careers_page_jsonld(client: PoliteClient, mock: respx.MockRouter) -> None:
    _robots(mock, "careers.fiordo.example")
    url = "https://careers.fiordo.example/jobs/junior-data-analyst"
    mock.get(url).mock(return_value=httpx.Response(200, text=load_text("manual/jsonld_page.html")))
    got = describe_url(client, url)
    assert got.raw is not None and "analytics" in got.raw.raw_text
    assert got.raw.deadline is not None


def test_careers_page_robots_disallow(client: PoliteClient, mock: respx.MockRouter) -> None:
    _robots(mock, "blocked.example", "User-agent: *\nDisallow: /\n")
    page = mock.get("https://blocked.example/jobs/1")
    got = describe_url(client, "https://blocked.example/jobs/1")
    assert got.reason == "robots" and page.call_count == 0


def test_closed_marker_and_410(client: PoliteClient, mock: respx.MockRouter) -> None:
    _robots(mock, "careers.example.com")
    mock.get("https://careers.example.com/jobs/1").mock(
        return_value=httpx.Response(200, text="<main><p>This job has expired.</p></main>")
    )
    mock.get("https://careers.example.com/jobs/2").mock(return_value=httpx.Response(410))
    assert describe_url(client, "https://careers.example.com/jobs/1").closed
    assert describe_url(client, "https://careers.example.com/jobs/2").closed


def test_linkedin_and_jobteaser_are_not_fetched(client: PoliteClient) -> None:
    # respx is not active: any request would hit the network and fail the test run
    assert describe_url(client, "https://it.linkedin.com/jobs/view/x-4012345678").reason == (
        "linkedin"
    )
    assert describe_url(client, "https://luiss.jobteaser.com/it/job-offers/abc").reason == (
        "jobteaser"
    )


def test_careers_home_is_not_a_description(client: PoliteClient, mock: respx.MockRouter) -> None:
    _robots(mock, "careers.lumen.example")
    page = "<main>" + "<p>Find your place at Lumen. 381 open roles.</p>" * 20 + "</main>"
    mock.get("https://careers.lumen.example/?gh_jid=4012").mock(
        return_value=httpx.Response(200, text=page)
    )
    assert describe_url(client, "https://careers.lumen.example/?gh_jid=4012").reason == "landing"


def test_errors_never_raise(client: PoliteClient, mock: respx.MockRouter) -> None:
    _robots(mock, "down.example")
    mock.get("https://down.example/jobs/1").mock(return_value=httpx.Response(500))
    assert describe_url(client, "https://down.example/jobs/1").reason == "error"
