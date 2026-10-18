"""JobTeaser connector: detail parser, mapping, pause detection, robots, fixed pace, daily cap,
no listing pages, registry (fake browser only)."""

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
from radar.connectors.base import SourceError
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


def page(rel: str, url: str, status: int = 200) -> jt.Page:
    return jt.Page(url=url, status=status, html=load_text(f"jobteaser/{rel}"))


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


def cand(offer_id: str, source_list: str = "extension", **kw: Any) -> jt.Candidate:
    return {"id": offer_id, "url": None, "title": None, "company": None, "location": None,
            "list": source_list, **kw}  # fmt: skip


def connector(
    browser: FakeBrowser,
    *,
    robots: str = ROBOTS,
    cfg: dict[str, Any] | None = None,
    pending: list[jt.Candidate] | None = None,
    state: Path | None = None,
    sleeps: list[float] | None = None,
    today: date = date(2026, 10, 19),
) -> jt.JobTeaserConnector:
    rec = sleeps if sleeps is not None else []
    items = list(pending or [])
    return jt.JobTeaserConnector(
        lambda: browser,
        None,
        cfg or {},
        candidates=lambda include_alerts: [
            c for c in items if include_alerts or c["list"] == "extension"
        ],
        state_path=state,
        sleep=rec.append,
        robots_text=robots,
        today=lambda: today,
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


@pytest.mark.parametrize(
    "url,html",
    [
        # sign-in page with a captcha widget: the fix is logging in again, not waiting
        (
            f"{BASE}/it/users/sign-in",
            '<form><div class="cf-turnstile"></div><input type="password" name="p"></form>',
        ),
        (
            f"{BASE}/it/dashboard",
            '<script src="https://www.google.com/recaptcha/api.js"></script>'
            '<div class="g-recaptcha"></div><form action="/it/users/sign_in"><button>Accedi'
            "</button></form>",
        ),
        (f"{BASE}/it", '<div class="g-recaptcha"></div><input type="password" id="pw">'),
    ],
)
def test_login_page_with_captcha_is_an_expired_session(url: str, html: str) -> None:
    page = jt.Page(url=url, status=200, html=f"<html><title>Accedi</title>{html}</html>")
    with pytest.raises(SourcePaused) as exc:
        jt.check_page(page)
    assert exc.value.reason == "sessione scaduta"
    assert "make jobteaser-login" in exc.value.action


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


def _routes() -> dict[str, jt.Page]:
    return {f"{BASE}/it": page("dashboard.html", f"{BASE}/it"), **_detail_routes()}


def _is_listing(url: str) -> bool:
    """Anything on the host that is neither the dashboard nor a single offer page."""
    return url != f"{BASE}/it" and jobteaser_offer_id(url) is None


def test_no_listing_code_path_left() -> None:
    for name in ("parse_listing", "listing_urls", "offers_from_dom", "json_total", "random"):
        assert not hasattr(jt, name), name
    conn = connector(FakeBrowser({}))
    assert not hasattr(conn, "listings") and not hasattr(conn, "collect_listings")
    assert "payloads" not in jt.Page.__dataclass_fields__  # no intercepted listing JSON


def test_fetch_opens_only_dashboard_and_offer_pages(tmp_path: Path) -> None:
    browser = FakeBrowser(_routes())
    sleeps: list[float] = []
    state = tmp_path / "state.json"
    pending = [cand(UUID1, title="Old card title", location="Roma"), cand(UUID2, "alert")]
    conn = connector(browser, pending=pending, state=state, sleeps=sleeps)
    out = conn.fetch()
    assert browser.visits[0] == f"{BASE}/it"
    assert not any(_is_listing(v) for v in browser.visits)
    assert all(jt.RobotsRules.parse(ROBOTS).allowed(v) for v in browser.visits)
    assert [p.external_id for p in out] == [UUID1]
    p = out[0]
    assert p.source == "jobteaser" and p.company == "Nebula Analytics"
    assert p.title == "Data Scientist Intern"  # the detail page wins over the card
    assert p.url == f"{BASE}/it/job-offers/{UUID1}-nebula-data-scientist-intern"
    assert p.deadline == date(2026, 11, 15) and "forecasting" in p.raw_text
    assert p.extra["via"] == "jobteaser" and p.extra["list"] == "extension"
    assert conn.exhaustive is False
    saved = json.loads(state.read_text())
    assert saved["closed_ids"] == [UUID2] and saved["opened"] == 2
    assert browser.closed
    # next run: the closed offer is not opened again
    browser2 = FakeBrowser(_routes())
    connector(
        browser2, pending=[cand(UUID2, "alert")], state=state, today=date(2026, 10, 20)
    ).fetch()
    assert browser2.visits == []


def test_fixed_pace_without_randomness() -> None:
    runs = []
    for _ in range(2):
        browser = FakeBrowser(_routes())
        sleeps: list[float] = []
        connector(browser, pending=[cand(UUID1), cand(NUM), cand(UUID2)], sleeps=sleeps).fetch()
        assert len(sleeps) == len(browser.visits) - 1 == 3
        runs.append(sleeps)
    assert runs[0] == runs[1] == [jt.PAGE_PAUSE_S] * 3
    assert jt.PAGE_PAUSE_S >= 5


def test_daily_cap_holds_across_runs_and_resets_next_day(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    pending = [cand(UUID1), cand(NUM), cand("999999", "alert")]
    first = FakeBrowser(_routes())
    conn = connector(first, cfg={"max_details": 2}, pending=pending, state=state)
    assert len(conn.fetch()) == 2
    assert conn.stats["detail_skipped_cap"] == 1
    # same day (e.g. `radar jobteaser details` after the daily run): cap used, browser not started
    created: list[int] = []
    again = connector(FakeBrowser(_routes()), cfg={"max_details": 2}, pending=pending, state=state)
    again.browser_factory = lambda: created.append(1) or FakeBrowser({})  # type: ignore[assignment,func-returns-value]
    assert again.fetch() == [] and created == []
    nxt = FakeBrowser(_routes())
    connector(
        nxt, cfg={"max_details": 2}, pending=pending, state=state, today=date(2026, 10, 20)
    ).fetch()
    assert len([v for v in nxt.visits if v != f"{BASE}/it"]) == 2


def test_nothing_pending_does_not_start_the_browser() -> None:
    def boom() -> FakeBrowser:
        raise AssertionError("browser started")

    conn = jt.JobTeaserConnector(boom, None, {}, candidates=lambda _a: [], robots_text=ROBOTS)
    assert conn.fetch() == []


def test_alert_offers_only_when_enrich_alerts() -> None:
    browser = FakeBrowser(_routes())
    pending = [cand(NUM, "alert")]
    connector(browser, cfg={"enrich_alerts": False}, pending=pending).fetch()
    assert browser.visits == []


def test_robots_disallowed_offer_and_dashboard_are_never_opened() -> None:
    robots = f"User-agent: *\nDisallow: /it/job-offers/{NUM}\n"
    browser = FakeBrowser(_routes())
    conn = connector(browser, robots=robots, pending=[cand(NUM), cand(UUID1)])
    out = conn.fetch()
    assert [p.external_id for p in out] == [UUID1]
    assert conn.stats["detail_robots_disallowed"] == 1
    assert not any(NUM in v for v in browser.visits)
    closed = FakeBrowser(_routes())
    with pytest.raises(SourceError):
        connector(closed, robots="User-agent: *\nDisallow: /\n", pending=[cand(UUID1)]).fetch()
    assert closed.visits == [] and closed.closed


def test_page_without_description_is_not_retried(tmp_path: Path) -> None:
    empty = jt.Page(url=f"{BASE}/it/job-offers/{NUM}", status=200, html="<h1>Analyst</h1>")
    routes = {f"{BASE}/it": page("dashboard.html", f"{BASE}/it"), f"{BASE}/it/job-offers/": empty}
    state = tmp_path / "s.json"
    out = connector(FakeBrowser(routes), pending=[cand(NUM)], state=state).fetch()
    assert out[0].title == "Analyst" and out[0].raw_text == ""
    again = FakeBrowser(routes)
    connector(again, pending=[cand(NUM)], state=state, today=date(2026, 10, 21)).fetch()
    assert again.visits == []


def test_fetch_pauses_on_expired_session_and_closes_browser() -> None:
    browser = FakeBrowser({}, default=page("login.html", f"{BASE}/it/users/sign_in"))
    with pytest.raises(SourcePaused) as exc:
        connector(browser, pending=[cand(UUID1)]).fetch()
    assert exc.value.reason == "sessione scaduta"
    assert browser.visits == [f"{BASE}/it"] and browser.closed


def test_fetch_pauses_on_block_mid_run_without_retry() -> None:
    routes = {
        f"{BASE}/it": page("dashboard.html", f"{BASE}/it"),
        f"{BASE}/it/job-offers/": page("block_cloudflare.html", f"{BASE}/it/job-offers/x", 403),
    }
    browser = FakeBrowser(routes)
    with pytest.raises(SourcePaused) as exc:
        connector(browser, pending=[cand(UUID1), cand(NUM)]).fetch()
    assert exc.value.reason == jt.OFFER_BLOCK_REASON  # the session itself works
    assert "Il login funziona" in exc.value.action
    assert len(browser.visits) == 2 and browser.closed  # stopped at the first block


def test_smoke_test_reports_counts_only() -> None:
    browser = FakeBrowser(_routes())
    report = jt.smoke_test(connector(browser, pending=[cand(UUID1)]), browser)
    assert report["session"] == "ok" and report["detail_pending"] == 1
    assert report["detail_parse"] == "jsonld" and report["detail_has_closing_date"] is True
    assert report["pages_opened"] == 2
    assert not any(_is_listing(v) for v in browser.visits)
    dumped = json.dumps(report)
    assert "Nebula" not in dumped and "Data Scientist" not in dumped


def test_to_raw_requires_title_and_keeps_stored_fields() -> None:
    detail = {"title": None, "description": "", "closing": None, "posted": None}
    assert jt.to_raw(UUID1, f"{BASE}/it/job-offers/{UUID1}", None, detail) is None
    known = {"title": "T", "company": "C", "location": "Roma", "list": "extension"}
    detail = {**detail, "closing": "30/11/2026"}
    raw = jt.to_raw(UUID1, f"{BASE}/it/job-offers/{UUID1}", known, detail)
    assert raw is not None and raw.company == "C" and raw.location == "Roma"
    assert raw.deadline == date(2026, 11, 30) and raw.extra["list"] == "extension"


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
    assert conn.max_details == 40  # example config loaded (no real config in tests)
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


def test_db_detail_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from sqlmodel import Session

    from radar.db import get_engine, init_db
    from radar.pipeline.store import upsert_raw
    from radar.schemas import RawPosting

    url = f"sqlite:///{tmp_path / 'ids.db'}"
    monkeypatch.setenv("RADAR_DATABASE_URL", url)
    from radar.config import get_settings

    get_settings.cache_clear()
    engine = init_db(get_engine(url))
    saved = "20000001"

    def rp(source: str, ext: str, link: str, text: str = "") -> RawPosting:
        return RawPosting(
            source=source, external_id=ext, url=link, title="T " + ext, company="C", raw_text=text
        )

    with Session(engine) as s:
        # described by an earlier detail fetch: not a candidate, nor its alert copy
        upsert_raw(s, rp("jobteaser", UUID1, f"{BASE}/it/job-offers/{UUID1}", "x" * 400))
        upsert_raw(s, rp("email", "jobteaser:" + UUID1, f"{BASE}/it/job-offers/{UUID1}-x"))
        upsert_raw(s, rp("email", f"jobteaser:{NUM}", f"{BASE}/it/job-offers/{NUM}-x"))
        # saved with the extension (no description yet), plus its alert copy
        upsert_raw(s, rp("jobteaser", saved, f"{BASE}/it/job-offers/{saved}-y"))
        upsert_raw(s, rp("email", f"jobteaser:{saved}", f"{BASE}/it/job-offers/{saved}"))
        upsert_raw(s, rp("greenhouse", "1", "https://boards.greenhouse.io/x/jobs/1"))
        s.commit()
    got = jt.db_detail_candidates()
    assert [(c["id"], c["list"]) for c in got] == [(saved, "extension"), (NUM, "alert")]
    assert got[0]["url"] == f"{BASE}/it/job-offers/{saved}-y" and got[0]["title"] == "T " + saved
    assert [c["id"] for c in jt.db_detail_candidates(include_alerts=False)] == [saved]


# --------------------------------------------------------------------------- probe script


def test_probe_dumps_structure_without_content() -> None:
    path = FIXTURES.parents[1] / "scripts" / "jobteaser_probe.py"
    spec = importlib.util.spec_from_file_location("jobteaser_probe", path)
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    # the probe never opens listing pages; its DOM summary still works on any page
    card_page = page("listing_dom.html", f"{BASE}/it/job-offers/{UUID1}?targeted=true")
    out = probe.page_structure(card_page, "detail", BASE)
    dumped = json.dumps(out)
    assert out["page"] == "ok" and out["dom"]["offer_links"] == 2
    assert "json_responses" not in out and "listing_parse" not in out
    assert any(
        "targeted=true" in f and "Offerte mirate" in f for f, _ in out["dom"]["filter_links"]
    )
    for secret in ("Nebula", "Data Scientist", "Roma", "Fiordo", UUID1):
        assert secret not in dumped
    detail = probe.page_structure(
        page("detail_jsonld.html", f"{BASE}/it/job-offers/{UUID1}-x"), "detail", BASE
    )
    assert detail["detail_parse"]["how"] == "jsonld"
    assert "forecasting" not in json.dumps(detail)
    login = probe.page_structure(page("login.html", f"{BASE}/it/users/sign_in"), "dashboard", BASE)
    assert login["page"] == "login"
