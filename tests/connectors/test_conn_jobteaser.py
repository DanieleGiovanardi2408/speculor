"""JobTeaser connector: parsers, mapping, pause detection, robots, registry (fake browser only)."""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from conn_helpers import FIXTURES, load_json, load_text

from radar.config import Settings
from radar.connectors import jobteaser as jt
from radar.connectors.email_parsers.jobteaser import offer_key
from radar.connectors.http import PoliteClient
from radar.connectors.registry import ALL_SOURCES, build_connectors
from radar.connectors.status import SourcePaused
from radar.pipeline.normalize import canonical_url, jobteaser_offer_id

BASE = "https://luiss.jobteaser.com"
UUID1 = "0a1b2c3d-1111-4a2a-9f1d-000000000001"
UUID2 = "0a1b2c3d-1111-4a2a-9f1d-000000000002"
NUM = "10234567"
ROBOTS = load_text("jobteaser/robots.txt")
ALLOW_ALL = "User-agent: *\nAllow: /\n"


def page(rel: str, url: str, status: int = 200, payloads: Any = None) -> jt.Page:
    return jt.Page(
        url=url, status=status, html=load_text(f"jobteaser/{rel}"), payloads=payloads or []
    )


class FakeBrowser:
    """Serves fixture pages by URL prefix; records visits; never touches the network."""

    def __init__(self, routes: dict[str, jt.Page], default: jt.Page | None = None) -> None:
        self.routes = routes
        self.default = default
        self.visits: list[str] = []
        self.closed = False

    def visit(self, url: str) -> jt.Page:
        self.visits.append(url)
        for prefix, pg in sorted(self.routes.items(), key=lambda kv: -len(kv[0])):
            if url.startswith(prefix):
                return pg
        if self.default is not None:
            return self.default
        return jt.Page(url=url, status=404, html="<html><body>Not found</body></html>")

    def close(self) -> None:
        self.closed = True


def connector(
    browser: FakeBrowser,
    *,
    robots: str = ROBOTS,
    cfg: dict[str, Any] | None = None,
    known: set[str] | None = None,
    alerts: list[str] | None = None,
    state: Path | None = None,
    sleeps: list[float] | None = None,
) -> jt.JobTeaserConnector:
    rec = sleeps if sleeps is not None else []
    return jt.JobTeaserConnector(
        lambda: browser,
        None,
        cfg
        if cfg is not None
        else {
            "reserved": [{"url": "{base}/{locale}/job-offers?page={page}"}],
            "searches": ["data scientist"],
            "max_pages": 2,
        },
        ids=lambda: (set(known or ()), list(alerts or [])),
        state_path=state,
        sleep=rec.append,
        robots_text=robots,
    )


# --------------------------------------------------------------------------- URLs


def test_canonical_url_same_for_alert_connector_and_seed_variants() -> None:
    variants = [
        f"https://luiss.jobteaser.com/it/job-offers/{UUID1}-nebula-data-intern?utm_source=x",
        f"https://www.jobteaser.com/job-offers/{UUID1}-nebula-data-intern",
        f"https://luiss.jobteaser.com/it/job-offers/{UUID1}",
        f"https://LUISS.jobteaser.com/job-offers/{UUID1.upper()}/",
    ]
    assert {canonical_url(v) for v in variants} == {f"https://www.jobteaser.com/job-offers/{UUID1}"}
    assert jobteaser_offer_id(f"{BASE}/it/job-offers/{NUM}-example") == NUM
    assert jobteaser_offer_id(f"{BASE}/it/job-offers") is None
    assert jobteaser_offer_id("https://example.com/it/job-offers/123456") is None


def test_connector_url_matches_email_parser_url() -> None:
    conn = connector(FakeBrowser({}))
    href = f"/it/job-offers/{UUID1}-nebula-data-intern?from=search"
    email_id, email_url = offer_key(f"{BASE}{href}&utm_medium=email") or ("", "")
    assert conn.offer_url(UUID1, href) == email_url
    assert email_id == UUID1
    # no slug known: still the same canonical URL
    assert canonical_url(conn.offer_url(UUID1)) == canonical_url(email_url)


# --------------------------------------------------------------------------- robots


def test_robots_wildcards_listing_disallowed_detail_allowed() -> None:
    rules = jt.RobotsRules.parse(ROBOTS)
    assert not rules.allowed(f"{BASE}/it/job-offers?page=1")
    assert not rules.allowed(f"{BASE}/it/job-offers/")
    assert not rules.allowed(f"{BASE}/it/dashboard")
    assert rules.allowed(f"{BASE}/it/job-offers/{UUID1}-slug")
    assert rules.allowed(f"{BASE}/it")
    assert rules.allowed(f"{BASE}/it/users/sign_in")
    assert jt.RobotsRules.parse("").allowed(f"{BASE}/anything")
    specific = jt.RobotsRules.parse(
        "User-agent: speculor-radar\nDisallow: /\n\nUser-agent: *\nAllow: /\n", "speculor-radar"
    )
    assert not specific.allowed(f"{BASE}/it")


# --------------------------------------------------------------------------- parsers


def test_offers_from_json_api_payload() -> None:
    offers = {o["id"]: o for o in jt.offers_from_json(load_json("jobteaser/search_api.json"))}
    assert set(offers) == {UUID1, UUID2, NUM}
    a = offers[UUID1]
    assert (a["title"], a["company"], a["location"], a["contract"]) == (
        "Data Scientist Intern",
        "Nebula Analytics",
        "Roma, Italia",
        "Stage",
    )
    assert offers[UUID2]["company"] == "Fiordo Consulting"
    assert offers[UUID2]["closing"] == "2026-11-30"
    assert offers[NUM]["company"] == "Example Ventures"
    assert jt.json_total(load_json("jobteaser/search_api.json")) == 3


def test_parse_listing_prefers_json_then_next_data_then_dom() -> None:
    api = load_json("jobteaser/search_api.json")
    offers, how = jt.parse_listing(
        page("dashboard.html", f"{BASE}/it/job-offers", payloads=[("x", api)])
    )
    assert how == "json" and len(offers) == 3
    offers, how = jt.parse_listing(page("listing_next.html", f"{BASE}/it/job-offers"))
    assert how == "json" and len(offers) == 3
    offers, how = jt.parse_listing(page("listing_dom.html", f"{BASE}/it/job-offers"))
    assert how == "dom"
    by_id = {o["id"]: o for o in offers}
    assert set(by_id) == {UUID1, NUM}
    assert by_id[UUID1]["title"] == "Data Scientist Intern"
    assert by_id[UUID1]["company"] == "Nebula Analytics"
    assert by_id[UUID1]["location"] == "Roma, Italia"
    assert by_id[NUM]["contract"] == "Tirocinio"
    assert jt.parse_listing(page("dashboard.html", f"{BASE}/it"))[1] == "none"


def test_parse_detail_jsonld() -> None:
    d = jt.parse_detail(page("detail_jsonld.html", f"{BASE}/it/job-offers/{UUID1}-x"))
    assert d["how"] == "jsonld" and not d["closed"]
    assert d["company"] == "Nebula Analytics" and d["location"] == "Roma"
    assert "forecasting models" in d["description"] and "<b>" not in d["description"]
    assert jt.parse_human_date(d["closing"]) == date(2026, 11, 15)


def test_parse_detail_dom_fallback_and_closed_markers() -> None:
    d = jt.parse_detail(page("detail_dom.html", f"{BASE}/it/job-offers/{NUM}-x"))
    assert d["how"] == "dom" and d["title"] == "Venture Capital Analyst Stage"
    assert "startup fintech" in d["description"]
    assert jt.parse_human_date(d["closing"]) == date(2026, 11, 15)
    assert jt.parse_detail(page("detail_expired.html", f"{BASE}/it/job-offers/{UUID2}"))["closed"]
    gone = jt.Page(url=f"{BASE}/it/job-offers/{UUID2}", status=404, html="<html></html>")
    assert jt.parse_detail(gone)["closed"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-11-15", date(2026, 11, 15)),
        ("15/11/2026", date(2026, 11, 15)),
        ("15 novembre 2026", date(2026, 11, 15)),
        ("November 15, 2026", date(2026, 11, 15)),
        ("31/02/2026", None),
        ("presto", None),
        (None, None),
    ],
)
def test_parse_human_date(text: str | None, expected: date | None) -> None:
    assert jt.parse_human_date(text) == expected


# --------------------------------------------------------------------------- pause detection


def test_login_redirect_and_sso_pause() -> None:
    login = page("login.html", f"{BASE}/it/users/sign_in?redirect_after_authentication=x")
    with pytest.raises(SourcePaused) as exc:
        jt.check_page(login)
    assert exc.value.source == "jobteaser" and exc.value.reason == "sessione scaduta"
    assert "make jobteaser-login" in exc.value.action
    assert "radar connectors resume jobteaser" in exc.value.action
    sso = jt.Page(url="https://idp.example-university.it/saml/login", status=200, html="<html>")
    with pytest.raises(SourcePaused):
        jt.check_page(sso)
    jt.check_page(page("dashboard.html", f"{BASE}/it"))  # no raise


@pytest.mark.parametrize("status", [200, 403])
def test_block_page_pauses(status: int) -> None:
    blocked = page("block_cloudflare.html", f"{BASE}/it/job-offers/{UUID1}", status=status)
    with pytest.raises(SourcePaused) as exc:
        jt.check_page(blocked)
    assert exc.value.reason == "captcha o blocco"
    with pytest.raises(SourcePaused):
        jt.check_page(jt.Page(url=f"{BASE}/it", status=429, html="<html></html>"))


# --------------------------------------------------------------------------- fetch


def _detail_routes() -> dict[str, jt.Page]:
    return {
        f"{BASE}/it/job-offers/{UUID1}": page(
            "detail_jsonld.html", f"{BASE}/it/job-offers/{UUID1}-nebula-data-scientist-intern"
        ),
        f"{BASE}/it/job-offers/{NUM}": page("detail_dom.html", f"{BASE}/it/job-offers/{NUM}-ev"),
        f"{BASE}/it/job-offers/{UUID2}": page(
            "detail_expired.html", f"{BASE}/it/job-offers/{UUID2}"
        ),
    }


def test_fetch_with_real_robots_skips_listings_and_enriches_alert_offers(tmp_path: Path) -> None:
    routes = {f"{BASE}/it": page("dashboard.html", f"{BASE}/it"), **_detail_routes()}
    browser = FakeBrowser(routes)
    sleeps: list[float] = []
    state = tmp_path / "state.json"
    conn = connector(browser, alerts=[UUID1, UUID2], state=state, sleeps=sleeps)
    out = conn.fetch()
    assert browser.visits[0] == f"{BASE}/it"
    assert not any("?" in v for v in browser.visits)  # listing URLs never opened
    assert conn.stats["listing_robots_disallowed"] == 2
    assert [p.external_id for p in out] == [UUID1]
    p = out[0]
    assert p.source == "jobteaser" and p.company == "Nebula Analytics"
    assert p.url == f"{BASE}/it/job-offers/{UUID1}-nebula-data-scientist-intern"
    assert p.deadline == date(2026, 11, 15) and "forecasting" in p.raw_text
    assert p.extra["via"] == "jobteaser" and p.extra["list"] == "alert"
    assert conn.exhaustive is False
    assert json.loads(state.read_text())["closed_ids"] == [UUID2]
    assert len(sleeps) == len(browser.visits) - 1 and all(3 <= s <= 6 for s in sleeps)
    assert browser.closed
    # next day: the closed offer is not opened again
    browser2 = FakeBrowser(routes)
    connector(browser2, alerts=[UUID2], state=state).fetch()
    assert browser2.visits == [f"{BASE}/it"]


def test_fetch_listing_details_only_for_unknown_ids_with_cap() -> None:
    api = load_json("jobteaser/search_api.json")
    listing = jt.Page(
        url=f"{BASE}/it/job-offers",
        status=200,
        html="<html></html>",
        payloads=[("https://luiss.jobteaser.com/api/job_offers", api)],
    )
    routes = {
        f"{BASE}/it/job-offers?": listing,
        f"{BASE}/it/job-offers/{NUM}": page("detail_dom.html", f"{BASE}/it/job-offers/{NUM}-ev"),
        f"{BASE}/it/job-offers/{UUID1}": page(
            "detail_jsonld.html", f"{BASE}/it/job-offers/{UUID1}"
        ),
        f"{BASE}/it": page("dashboard.html", f"{BASE}/it"),
    }
    browser = FakeBrowser(routes)
    cfg = {
        "reserved": [{"url": "{base}/{locale}/job-offers?page={page}"}],
        "searches": ["data scientist"],
        "max_pages": 3,
        "max_details": 1,
    }
    conn = connector(browser, robots=ALLOW_ALL, cfg=cfg, known={UUID2}, alerts=["999999"])
    out = conn.fetch()
    listing_visits = [v for v in browser.visits if "?" in v]
    # page 2 returns the same offers -> stop paging; one page per listing
    assert listing_visits == [
        f"{BASE}/it/job-offers?page=1",
        f"{BASE}/it/job-offers?page=2",
        f"{BASE}/it/job-offers?q=data+scientist&page=1",
    ]
    assert conn.stats["listing_offers"] == 3 and conn.stats["listing_known"] == 1
    assert len(out) == 1 and out[0].external_id == UUID1  # cap 1, known UUID2 never opened
    assert out[0].extra["list"] == "reserved"
    assert conn.stats["detail_skipped_cap"] == 2  # NUM + the alert-only id
    assert not any(UUID2 in v for v in browser.visits)


def test_fetch_pauses_on_expired_session_and_closes_browser() -> None:
    browser = FakeBrowser({}, default=page("login.html", f"{BASE}/it/users/sign_in"))
    with pytest.raises(SourcePaused) as exc:
        connector(browser, alerts=[UUID1]).fetch()
    assert exc.value.reason == "sessione scaduta"
    assert browser.visits == [f"{BASE}/it"] and browser.closed


def test_fetch_pauses_on_block_mid_run_without_retry() -> None:
    routes = {
        f"{BASE}/it": page("dashboard.html", f"{BASE}/it"),
        f"{BASE}/it/job-offers/": page("block_cloudflare.html", f"{BASE}/it/job-offers/x", 403),
    }
    browser = FakeBrowser(routes)
    with pytest.raises(SourcePaused) as exc:
        connector(browser, alerts=[UUID1, NUM]).fetch()
    assert exc.value.reason == "captcha o blocco"
    assert len(browser.visits) == 2 and browser.closed  # stopped at the first block


def test_smoke_test_reports_counts_only() -> None:
    routes = {f"{BASE}/it": page("dashboard.html", f"{BASE}/it"), **_detail_routes()}
    browser = FakeBrowser(routes)
    report = jt.smoke_test(connector(browser, alerts=[UUID1]), browser)
    assert report["session"] == "ok"
    assert "robots.txt disallows" in report["listing"]
    assert report["detail_parse"] == "jsonld" and report["detail_has_closing_date"] is True
    assert report["pages_opened"] == 2
    dumped = json.dumps(report)
    assert "Nebula" not in dumped and "Data Scientist" not in dumped


def test_to_raw_requires_title_and_merges_listing() -> None:
    detail = {"title": None, "description": "", "closing": None, "posted": None}
    assert jt.to_raw(UUID1, f"{BASE}/it/job-offers/{UUID1}", None, detail) is None
    offer = {"title": "T", "company": "C", "location": "Roma", "list": "search",
             "closing": "30/11/2026"}  # fmt: skip
    raw = jt.to_raw(UUID1, f"{BASE}/it/job-offers/{UUID1}", offer, detail)
    assert raw is not None and raw.company == "C" and raw.deadline == date(2026, 11, 30)


# --------------------------------------------------------------------------- registry / settings


def _settings(profile: Path, tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None, RADAR_JOBTEASER_PROFILE=str(profile), RADAR_DATA_DIR=str(tmp_path)
    )


def test_registry_only_when_profile_exists(client: PoliteClient, tmp_path: Path) -> None:
    assert "jobteaser" in ALL_SOURCES
    missing = _settings(tmp_path / "nope", tmp_path)
    assert build_connectors(client, {}, settings=missing) == []
    profile = tmp_path / "profile"
    profile.mkdir()
    present = _settings(profile, tmp_path)
    (conn,) = build_connectors(client, {}, settings=present)
    assert isinstance(conn, jt.JobTeaserConnector) and conn.source == "jobteaser"
    assert conn.listings  # example config loaded (no real config in tests)
    assert build_connectors(client, {}, sources=["lever"], settings=present) == []
    off = {"jobteaser": {"enabled": False}}
    assert build_connectors(client, off, settings=present) == []


def test_default_sources_include_jobteaser_only_with_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from radar.api.routes import settings as routes
    from radar.config import get_settings

    assert "jobteaser" not in routes.default_sources()
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv("RADAR_JOBTEASER_PROFILE", str(profile))
    get_settings.cache_clear()
    assert routes.default_sources()[-1] == "jobteaser"


def test_db_offer_ids(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from sqlmodel import Session

    from radar.db import get_engine, init_db
    from radar.pipeline.store import upsert_raw
    from radar.schemas import RawPosting

    url = f"sqlite:///{tmp_path / 'ids.db'}"
    monkeypatch.setenv("RADAR_DATABASE_URL", url)
    from radar.config import get_settings

    get_settings.cache_clear()
    engine = init_db(get_engine(url))

    def rp(source: str, ext: str, link: str) -> RawPosting:
        return RawPosting(source=source, external_id=ext, url=link, title="T " + ext, company="C")

    with Session(engine) as s:
        upsert_raw(s, rp("jobteaser", UUID1, f"{BASE}/it/job-offers/{UUID1}"))
        upsert_raw(s, rp("email", f"jobteaser:{NUM}", f"{BASE}/it/job-offers/{NUM}-x"))
        upsert_raw(s, rp("email", "jobteaser:" + UUID1, f"{BASE}/it/job-offers/{UUID1}-x"))
        upsert_raw(s, rp("greenhouse", "1", "https://boards.greenhouse.io/x/jobs/1"))
        s.commit()
    known, alerts = jt.db_offer_ids()
    assert known == {UUID1} and alerts == [NUM]


# --------------------------------------------------------------------------- probe script


def test_probe_dumps_structure_without_content() -> None:
    path = FIXTURES.parents[1] / "scripts" / "jobteaser_probe.py"
    spec = importlib.util.spec_from_file_location("jobteaser_probe", path)
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    api = load_json("jobteaser/search_api.json")
    listing = page("listing_dom.html", f"{BASE}/it/job-offers?page=1&targeted=true")
    listing.payloads = [(f"{BASE}/api/v2/job_offers/{UUID1}?q=secret", api)]
    out = probe.page_structure(listing, "listing", BASE)
    dumped = json.dumps(out)
    assert out["page"] == "ok" and out["dom"]["offer_links"] == 2
    assert out["json_responses"][0]["offers_found"] == 3
    assert out["json_responses"][0]["url"]["path"] == "/api/v2/job_offers/<id>"
    assert any(
        "targeted=true" in f and "Offerte mirate" in f for f, _ in out["dom"]["filter_links"]
    )
    for secret in ("Nebula", "Data Scientist", "Roma", "secret", "Fiordo", UUID1):
        assert secret not in dumped
    detail = probe.page_structure(
        page("detail_jsonld.html", f"{BASE}/it/job-offers/{UUID1}-x"), "detail", BASE
    )
    assert detail["detail_parse"]["how"] == "jsonld"
    assert "forecasting" not in json.dumps(detail)
    login = probe.page_structure(page("login.html", f"{BASE}/it/users/sign_in"), "dashboard", BASE)
    assert login["page"] == "login"
