from __future__ import annotations

import httpx
import respx
from conn_helpers import load_json, load_text

from radar.connectors.ashby import AshbyConnector
from radar.connectors.closed import check_open
from radar.connectors.greenhouse import GreenhouseConnector
from radar.connectors.http import PoliteClient
from radar.connectors.lever import LeverConnector
from radar.connectors.linkedin_guest import LinkedInGuestConnector
from radar.connectors.registry import build_connectors
from radar.connectors.smartrecruiters import SmartRecruitersConnector

GH_API = "https://boards-api.greenhouse.io/v1/boards"
SR_API = "https://api.smartrecruiters.com/v1/companies"


# ------------------------------------------------------------------ check_open
def test_greenhouse_open_and_closed(client: PoliteClient, mock: respx.MockRouter) -> None:
    job = load_json("greenhouse/board.json")["jobs"][0]
    mock.get(f"{GH_API}/rondinepay/jobs/4983933101").mock(
        return_value=httpx.Response(200, json=job)
    )
    mock.get(f"{GH_API}/ondatrading/jobs/1").mock(return_value=httpx.Response(404, json={"status": 404}))
    assert check_open(client, "https://job-boards.eu.greenhouse.io/rondinepay/jobs/4983933101")
    assert check_open(client, "https://job-boards.eu.greenhouse.io/ondatrading/jobs/1") is False


def test_lever_open_and_closed(client: PoliteClient, mock: respx.MockRouter) -> None:
    job = load_json("lever/board.json")[1]
    pid = job["id"]
    mock.get(f"https://api.eu.lever.co/v0/postings/velainsurance/{pid}").mock(
        return_value=httpx.Response(200, json=job)
    )
    gone = "00000000-0000-0000-0000-000000000000"
    mock.get(f"https://api.lever.co/v0/postings/quarzo/{gone}").mock(
        return_value=httpx.Response(404)
    )
    assert check_open(client, f"https://jobs.eu.lever.co/velainsurance/{pid}") is True
    assert check_open(client, f"https://jobs.lever.co/quarzo/{gone}") is False


def test_ashby_open_and_closed_via_board(client: PoliteClient, mock: respx.MockRouter) -> None:
    board = mock.get("https://api.ashbyhq.com/posting-api/job-board/tascapay").mock(
        return_value=httpx.Response(200, json=load_json("ashby/board.json"))
    )
    listed = "45484473-f8e0-4457-983e-ae0ddadbfec1"
    missing = "31e09fdd-2cd3-4156-a99d-c09a1733c50a"
    assert check_open(client, f"https://jobs.ashbyhq.com/tascapay/{listed}") is True
    assert check_open(client, f"https://jobs.ashbyhq.com/tascapay/{missing}") is False
    assert board.call_count == 1  # board cached
    assert check_open(client, "https://jobs.ashbyhq.com/tascapay") is None  # board, not posting


def test_smartrecruiters_open_inactive_and_404(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    detail = load_json("smartrecruiters/detail_744000155707965.json")
    inactive = {**detail, "id": "2", "active": False}
    mock.get(f"{SR_API}/AlturaConsulting/postings/744000155707965").mock(
        return_value=httpx.Response(200, json=detail)
    )
    mock.get(f"{SR_API}/AlturaConsulting/postings/2").mock(
        return_value=httpx.Response(200, json=inactive)
    )
    mock.get(f"{SR_API}/PastoGo/postings/3").mock(return_value=httpx.Response(404))
    base = "https://jobs.smartrecruiters.com"
    assert check_open(client, f"{base}/AlturaConsulting/744000155707965-intern-consultant") is True
    assert check_open(client, f"{base}/AlturaConsulting/2-x") is False
    assert check_open(client, f"{base}/PastoGo/3") is False


def test_linkedin_unknown_when_robots_disallows(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    mock.get("https://www.linkedin.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    detail = mock.get(url__startswith="https://www.linkedin.com/jobs-guest/")
    assert check_open(client, "https://it.linkedin.com/jobs/view/x-at-y-4464880857") is None
    assert detail.call_count == 0


def test_linkedin_open_closed_404_when_allowed(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    mock.get("https://www.linkedin.com/robots.txt").mock(return_value=httpx.Response(404))
    api = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting"
    mock.get(f"{api}/1111111").mock(
        return_value=httpx.Response(200, text=load_text("linkedin/detail_open.html"))
    )
    mock.get(f"{api}/2222222").mock(
        return_value=httpx.Response(200, text=load_text("linkedin/detail_closed.html"))
    )
    mock.get(f"{api}/3333333").mock(return_value=httpx.Response(404))
    assert check_open(client, "https://www.linkedin.com/jobs/view/1111111/") is True
    assert check_open(client, "https://www.linkedin.com/jobs/view/2222222/") is False
    assert check_open(client, "https://www.linkedin.com/jobs/view/3333333/") is False


def test_unknown_cases_never_raise(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(f"{GH_API}/flussodata/jobs/9").mock(return_value=httpx.Response(500))
    # login-walled and unsupported domains are not even fetched (respx would fail on them)
    assert check_open(client, "https://luiss.jobteaser.com/it/job-offers/abc-def") is None
    assert check_open(client, "https://jobs.bazaar.example/en/jobs/10563644/x") is None
    assert check_open(client, "https://job-boards.greenhouse.io/flussodata/jobs/9") is None
    assert check_open(client, "not a url") is None


# ------------------------------------------------------------------ registry
CFG = {
    "title_filter": ["intern", "graduate"],
    "companies": [
        {"name": "A", "ats": "greenhouse", "slug": "a", "region": "eu", "active": True},
        {"name": "B", "ats": "lever", "slug": "b", "region": "eu", "active": True},
        {"name": "C", "ats": "ashby", "slug": "c", "title_filter": False},
        {"name": "D", "ats": "smartrecruiters", "slug": "D", "active": True},
        {"name": "E", "ats": "none", "slug": None, "active": True},
        {"name": "F", "ats": "workday", "slug": "f", "active": True},
        {"name": "G", "ats": "greenhouse", "slug": "g", "active": False},
        {"name": "H", "ats": "lever", "slug": None, "active": True},
    ],
    "linkedin": {
        "max_details": 3,
        "queries": [{"keywords": "AI intern", "location": "Italy"}, {"location": "no keywords"}],
    },
}


def test_build_connectors(client: PoliteClient) -> None:
    conns = build_connectors(client, CFG)
    kinds = [type(c) for c in conns]
    assert kinds == [
        GreenhouseConnector,
        LeverConnector,
        AshbyConnector,
        SmartRecruitersConnector,
        LinkedInGuestConnector,
    ]
    gh, lv, ab, _sr, li = conns
    assert isinstance(gh, GreenhouseConnector) and gh.title_filter == ["intern", "graduate"]
    assert isinstance(lv, LeverConnector) and lv.region == "eu"
    assert isinstance(ab, AshbyConnector) and ab.title_filter is None  # per-company override
    assert isinstance(li, LinkedInGuestConnector)
    assert li.max_details == 3 and len(li.queries) == 1
    for c in conns:  # labels never contain company names
        assert c.label and c.source in c.label and " A" not in c.label


def test_build_connectors_source_filter_and_disabled_linkedin(client: PoliteClient) -> None:
    conns = build_connectors(client, CFG, sources=["lever", "linkedin"])
    assert [c.source for c in conns] == ["lever", "linkedin"]
    cfg = {**CFG, "linkedin": {"enabled": False, "queries": [{"keywords": "x"}]}}
    assert all(c.source != "linkedin" for c in build_connectors(client, cfg))
    assert build_connectors(client, {}) == []
