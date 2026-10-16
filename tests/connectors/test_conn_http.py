from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from conn_helpers import UA, SleepRecorder

from radar.connectors.base import SourceError
from radar.connectors.http import (
    PoliteClient,
    RobotsDisallowed,
    html_to_text,
    parse_date,
    parse_datetime,
    title_matches,
)

API = "https://api.example-ats.com/v1/board"


def test_sends_honest_user_agent_and_no_cookies(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    route = mock.get(API).mock(return_value=httpx.Response(200, json={"ok": True}))
    assert client.get_json(API) == {"ok": True}
    req = route.calls.last.request
    assert req.headers["User-Agent"] == UA
    assert "cookie" not in {k.lower() for k in req.headers}


def test_cache_hit_second_call_does_not_hit_network(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    route = mock.get(API, params={"page": "1"}).mock(
        return_value=httpx.Response(200, json={"jobs": [1]})
    )
    assert client.get_json(API, {"page": 1}) == {"jobs": [1]}
    assert client.get_json(API, {"page": 1}) == {"jobs": [1]}
    assert route.call_count == 1
    assert client.network_requests == 1


def test_cache_persists_on_disk_across_clients(tmp_path: Path, mock: respx.MockRouter) -> None:
    route = mock.get(API).mock(return_value=httpx.Response(200, text="hello"))
    with PoliteClient(UA, tmp_path, sleep=lambda s: None) as c1:
        assert c1.get_text(API, respect_robots=False) == "hello"
    with PoliteClient(UA, tmp_path, sleep=lambda s: None) as c2:
        assert c2.get_text(API, respect_robots=False) == "hello"
    assert route.call_count == 1


def test_use_cache_false_and_ttl_expiry(tmp_path: Path, mock: respx.MockRouter) -> None:
    route = mock.get(API).mock(return_value=httpx.Response(200, json=[]))
    with PoliteClient(UA, tmp_path, sleep=lambda s: None) as c:
        c.get_json(API)
        c.get_json(API, use_cache=False)
        assert route.call_count == 2
    with PoliteClient(UA, tmp_path, cache_ttl_hours=0, sleep=lambda s: None) as c:
        c.get_json(API)  # TTL 0 -> cache entry is stale
        assert route.call_count == 3


def test_429_backoff_honours_retry_after_then_succeeds(
    client: PoliteClient, mock: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    route = mock.get(API).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    assert client.get_json(API) == {"ok": 1}
    assert route.call_count == 2
    assert 7 in sleeps.calls


def test_persistent_429_raises_source_error(
    client: PoliteClient, mock: respx.MockRouter, sleeps: SleepRecorder
) -> None:
    route = mock.get(API).mock(return_value=httpx.Response(429))
    with pytest.raises(SourceError, match="429"):
        client.get_json(API)
    assert route.call_count == client.max_retries + 1
    backoffs = [s for s in sleeps.calls if s >= client.backoff_base]
    assert backoffs == sorted(backoffs) and len(backoffs) == client.max_retries


@pytest.mark.parametrize("status", [403, 999])
def test_blocking_statuses_back_off_then_fail(
    client: PoliteClient, mock: respx.MockRouter, status: int
) -> None:
    mock.get(API).mock(return_value=httpx.Response(status))
    with pytest.raises(SourceError, match=str(status)):
        client.get_text(API, respect_robots=False)


def test_retry_after_too_long_gives_up(client: PoliteClient, mock: respx.MockRouter) -> None:
    route = mock.get(API).mock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(SourceError, match="Retry-After"):
        client.get_json(API)
    assert route.call_count == 1


def test_404_is_a_source_error_and_not_retried(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    route = mock.get(API).mock(return_value=httpx.Response(404))
    with pytest.raises(SourceError, match="404"):
        client.get_json(API)
    assert route.call_count == 1


def test_non_json_raises_source_error(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get(API).mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(SourceError, match="not JSON"):
        client.get_json(API)


def test_network_error_retries_then_source_error(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    mock.get(API).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(SourceError, match="network error"):
        client.get_json(API)


def test_robots_disallow_blocks_html_fetch(client: PoliteClient, mock: respx.MockRouter) -> None:
    mock.get("https://site.example/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
    )
    page = mock.get("https://site.example/private/job").mock(return_value=httpx.Response(200))
    mock.get("https://site.example/jobs/1").mock(return_value=httpx.Response(200, text="ok"))
    assert client.robots_allowed("https://site.example/private/job") is False
    with pytest.raises(RobotsDisallowed):
        client.get_text("https://site.example/private/job")
    assert page.call_count == 0
    assert client.get_text("https://site.example/jobs/1") == "ok"


def test_robots_agent_specific_rules_and_cache(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    robots = mock.get("https://site.example/robots.txt").mock(
        return_value=httpx.Response(
            200, text="User-agent: speculor-radar-test\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
        )
    )
    assert client.robots_allowed("https://site.example/a") is False
    assert client.robots_allowed("https://site.example/b") is False
    assert robots.call_count == 1


def test_robots_missing_allows_and_5xx_disallows(
    client: PoliteClient, mock: respx.MockRouter
) -> None:
    mock.get("https://a.example/robots.txt").mock(return_value=httpx.Response(404))
    mock.get("https://b.example/robots.txt").mock(return_value=httpx.Response(503))
    assert client.robots_allowed("https://a.example/x") is True
    assert client.robots_allowed("https://b.example/x") is False


def test_rate_limit_per_host(tmp_path: Path, mock: respx.MockRouter) -> None:
    sleeps = SleepRecorder()
    mock.get(url__regex=r"https://slow\.example/.*").mock(return_value=httpx.Response(200, json=1))
    mock.get(url__regex=r"https://fast\.example/.*").mock(return_value=httpx.Response(200, json=1))
    with PoliteClient(
        UA,
        tmp_path,
        min_interval_per_host={"slow.example": 5.0},
        default_interval=0.0,
        sleep=sleeps,
    ) as c:
        c.get_json("https://slow.example/1")
        c.get_json("https://fast.example/1")
        c.get_json("https://slow.example/2")
    assert len(sleeps.calls) == 1 and 4.0 < sleeps.calls[0] <= 5.0


def test_linkedin_default_interval_is_slow(client: PoliteClient) -> None:
    assert client._interval_for("www.linkedin.com") >= 4.0
    assert client._interval_for("api.lever.co") == 1.0


def test_html_to_text_keeps_structure() -> None:
    html = (
        "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;"
        "&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;&lt;script&gt;x()&lt;/script&gt;"
    )
    assert html_to_text(html, unescape_first=True) == "Hello & welcome\n\n- Python\n\n- SQL"
    assert html_to_text("<div>a<br>b</div>") == "a\n\nb"
    assert html_to_text(None) == ""


def test_title_matches() -> None:
    kw = ["intern", "graduate", "working student"]
    assert title_matches("AI Engineering Internship", kw)
    assert title_matches("Graduate Programme 2027", kw)
    assert title_matches("Working Student - Data", kw)
    assert not title_matches("Senior Backend Engineer", kw)
    assert not title_matches("International Sales Lead", ["internship"])
    assert not title_matches("International PR Manager", kw)
    assert not title_matches("Internal Control Specialist", kw)
    assert title_matches("Werkstudentin Data", ["werkstudent"])
    assert title_matches("Data Science Interns 2027", kw)
    assert title_matches("anything", None)


def test_parse_dates() -> None:
    dt = parse_datetime(1734695677693)
    assert dt is not None and dt.year == 2024
    iso = parse_datetime("2026-10-08T00:01:55-04:00")
    assert iso is not None and iso.utcoffset() is not None
    assert parse_datetime("garbage") is None
    d = parse_date("2026-11-30T23:59:59+01:00")
    assert d is not None and d.isoformat() == "2026-11-30"


def test_robots_wildcards_are_enforced(client: PoliteClient, mock: respx.MockRouter) -> None:
    # The stdlib parser ignores "*" inside paths; PoliteClient also applies wildcard rules.
    mock.get("https://wild.example/robots.txt").mock(
        return_value=httpx.Response(
            200,
            text="User-agent: *\nAllow: /it$\nAllow: /*/job-offers/\nDisallow: /*LAC*\n"
            "Disallow: /*\n",
        )
    )
    assert client.robots_allowed("https://wild.example/it") is True
    assert client.robots_allowed("https://wild.example/it/job-offers/123") is True
    assert client.robots_allowed("https://wild.example/annunci.php?LAC=1") is False
    assert client.robots_allowed("https://wild.example/it/job-offers") is False
