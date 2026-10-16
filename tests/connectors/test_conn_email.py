"""EmailAlertsConnector: fake IMAP server, synthetic .eml fixtures (no network, no real mailbox)."""

from __future__ import annotations

import email
import email.policy
import email.utils
import imaplib
import logging
from datetime import date, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
from conn_helpers import FIXTURES

from radar.config import Settings
from radar.connectors.base import SourceError
from radar.connectors.email_alerts import EmailAlertsConnector, imap_date
from radar.connectors.email_parsers import classify, parse_message
from radar.connectors.email_parsers.common import clean_url, unwrap_url
from radar.connectors.http import PoliteClient
from radar.connectors.registry import build_connectors
from radar.pipeline.closed import is_exhaustive
from radar.pipeline.normalize import canonical_url

EMAIL = FIXTURES / "email"
TODAY = date(2026, 10, 16)
PASSWORD = "s3cret-App-Pass!"  # fake
ALL_FIXTURES = (
    "linkedin_alert.eml",
    "linkedin_alert_repeat.eml",
    "jobteaser_digest.eml",
    "generic_greenhouse.eml",
    "non_alert.eml",
)


def load(name: str) -> Message:
    return email.message_from_bytes((EMAIL / name).read_bytes(), policy=email.policy.default)


class FakeImap:
    """Minimal IMAP4 stand-in that records calls and evaluates SINCE/FROM searches."""

    def __init__(
        self,
        messages: list[bytes],
        *,
        fail_login: bool = False,
        ignore_criteria: bool = False,
    ) -> None:
        self.messages = messages
        self.fail_login = fail_login
        self.ignore_criteria = ignore_criteria
        self.calls: list[tuple[Any, ...]] = []

    def login(self, user: str, password: str) -> tuple[str, list[Any]]:
        self.calls.append(("login", user))
        if self.fail_login:
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return "OK", [b"LOGIN completed"]

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[Any]]:
        self.calls.append(("select", mailbox, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def search(self, charset: str | None, *criteria: str) -> tuple[str, list[Any]]:
        self.calls.append(("search", charset, *criteria))
        if self.ignore_criteria:
            return "OK", [" ".join(str(i + 1) for i in range(len(self.messages))).encode()]
        args = list(criteria)
        since = datetime.strptime(args[args.index("SINCE") + 1], "%d-%b-%Y").date()
        sender = args[args.index("FROM") + 1].strip('"').lower()
        hits = []
        for i, raw in enumerate(self.messages, start=1):
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            sent = email.utils.parsedate_to_datetime(str(msg["Date"])).date()
            if sent >= since and sender in str(msg["From"]).lower():
                hits.append(str(i))
        return "OK", [" ".join(hits).encode()]

    def fetch(self, message_set: str, message_parts: str) -> tuple[str, list[Any]]:
        self.calls.append(("fetch", message_set, message_parts))
        raw = self.messages[int(message_set) - 1]
        return "OK", [(f"{message_set} (BODY[] {{{len(raw)}}}".encode(), raw), b")"]

    def logout(self) -> tuple[str, list[Any]]:
        self.calls.append(("logout",))
        return "BYE", [b"logging out"]


def make(
    fake: FakeImap, *, extra_senders: tuple[str, ...] = ("alerts@jobs.example.com",)
) -> EmailAlertsConnector:
    return EmailAlertsConnector(
        "imap.example.com",
        "alerts@example.com",
        PASSWORD,
        folder="Job Alerts",
        since_days=14,
        extra_senders=extra_senders,
        factory=lambda host, port: fake,
        today=lambda: TODAY,
    )


def mailbox(*names: str) -> list[bytes]:
    return [(EMAIL / n).read_bytes() for n in names]


# ------------------------------------------------------------------ IMAP behaviour
def test_readonly_select_search_criteria_and_peek_fetch() -> None:
    fake = FakeImap(mailbox(*ALL_FIXTURES))
    make(fake).fetch()
    assert fake.calls[0] == ("login", "alerts@example.com")
    assert fake.calls[1] == ("select", '"Job Alerts"', True)
    searches = [c for c in fake.calls if c[0] == "search"]
    assert {c[5] for c in searches} == {
        '"jobalerts-noreply@linkedin.com"',
        '"jobs-listings@linkedin.com"',
        '"jobs-noreply@linkedin.com"',
        '"jobteaser.com"',
        '"alerts@jobs.example.com"',
    }
    for c in searches:
        assert c[1:5] == (None, "SINCE", "02-Oct-2026", "FROM")
    fetches = [c for c in fake.calls if c[0] == "fetch"]
    assert fetches and all(c[2] == "(BODY.PEEK[])" for c in fetches)
    assert {c[1] for c in fetches} == {"1", "2", "3", "4"}  # non-alert not matched by FROM
    assert fake.calls[-1] == ("logout",)
    # nothing that could change server state
    assert not {c[0] for c in fake.calls} & {"store", "copy", "move", "expunge", "uid"}


def test_imap_date_is_locale_independent() -> None:
    assert imap_date(date(2026, 3, 5)) == "05-Mar-2026"


def test_old_messages_are_not_returned() -> None:
    fake = FakeImap(mailbox(*ALL_FIXTURES))
    conn = make(fake)
    conn.since_days = 1  # since 15-Oct: only the generic alert (15 Oct) matches
    postings = conn.fetch()
    assert {p.extra["via"] for p in postings} == {"generic_alert"}


def test_fetch_parses_all_kinds_and_dedupes_within_run() -> None:
    postings = make(FakeImap(mailbox(*ALL_FIXTURES))).fetch()
    by_via: dict[str, list[str]] = {}
    for p in postings:
        assert p.source == "email"
        by_via.setdefault(p.extra["via"], []).append(p.external_id)
    # 3 + 2 LinkedIn jobs, one of them in both emails -> 4
    assert sorted(by_via["linkedin_alert"]) == [
        "linkedin:4012345678",
        "linkedin:4023456789",
        "linkedin:4034567890",
        "linkedin:4045678901",
    ]
    assert len(by_via["jobteaser_alert"]) == 2
    assert len(by_via["generic_alert"]) == 2
    assert len({p.url for p in postings}) == len(postings) == 8


def test_non_alert_ignored_even_if_search_returns_it() -> None:
    fake = FakeImap(mailbox("non_alert.eml"), ignore_criteria=True)
    assert make(fake).fetch() == []
    assert classify(load("non_alert.eml")) is None


def test_login_failure_is_generic_source_error(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeImap([], fail_login=True)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SourceError) as info:
        make(fake).fetch()
    err = info.value
    assert str(err) == "email: IMAP login failed"
    assert PASSWORD not in repr(err) and err.__cause__ is None and err.__suppress_context__
    assert PASSWORD not in caplog.text
    assert fake.calls[-1] == ("logout",)


def test_connection_failure_is_generic_source_error() -> None:
    def boom(host: str, port: int) -> FakeImap:
        raise OSError(f"cannot reach {host} with {PASSWORD}")

    conn = EmailAlertsConnector("imap.example.com", "u", PASSWORD, factory=boom)
    with pytest.raises(SourceError) as info:
        conn.fetch()
    assert str(info.value) == "email: cannot connect to the IMAP server"
    assert info.value.__cause__ is None
    assert PASSWORD not in repr(conn)


def test_logs_counts_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="radar.connectors.email_alerts")
    make(FakeImap(mailbox(*ALL_FIXTURES))).fetch()
    text = caplog.text
    assert "4 messages" in text and "8 postings" in text
    for leak in ("Fictional", "linkedin.com", "Data Science", "Nuove offerte", "alerts@"):
        assert leak not in text


def test_not_exhaustive_so_never_closed_by_absence() -> None:
    assert is_exhaustive(make(FakeImap([]))) is False


# ------------------------------------------------------------------ parsers
def test_linkedin_alert_fields() -> None:
    postings = parse_message(load("linkedin_alert.eml"))
    assert [(p.title, p.company, p.location) for p in postings] == [
        ("Data Science Intern", "Fictional Analytics", "Milan, Lombardy, Italy (Hybrid)"),
        (
            "Machine Learning Engineer Intern",
            "Example Robotics",
            "Amsterdam, North Holland, Netherlands (On-site)",
        ),
        (
            "AI Research Internship (6 months)",
            "Nimbus Labs Example",
            "Paris, Île-de-France, France",
        ),
    ]
    first = postings[0]
    assert first.url == "https://www.linkedin.com/jobs/view/4012345678"
    assert first.url == canonical_url(first.url)  # matches manual/LinkedIn canonical URLs
    assert first.extra["via"] == "linkedin_alert"
    assert first.extra["linkedin_job_id"] == "4012345678"
    assert first.extra["alert_date"].startswith("2026-10-12")
    assert "Data Science Intern" in first.raw_text
    assert "Actively recruiting" not in first.raw_text


def test_linkedin_plain_text_fallback() -> None:
    (p,) = parse_message(load("linkedin_alert_text_only.eml"))
    assert (p.external_id, p.title, p.company) == (
        "linkedin:4023456789",
        "Machine Learning Engineer Intern",
        "Example Robotics",
    )
    assert p.location == "Amsterdam, North Holland, Netherlands (On-site)"


def test_jobteaser_digest_fields() -> None:
    postings = parse_message(load("jobteaser_digest.eml"))
    assert [(p.title, p.company, p.location, p.employment_type) for p in postings] == [
        ("Stage Risk Management", "Fictional Bank Example", "Roma, Italia", "Stage"),
        (
            "Internship - Strategy Analyst",
            "Example Consulting Group",
            "Milano, Italia",
            "Internship",
        ),
    ]
    a, b = postings
    assert a.external_id == "jobteaser:8d3f2a10-4b6c-4e2a-9f1d-0c5e7a9b1234"
    assert a.url == (
        "https://luiss.jobteaser.com/it/job-offers/"
        "8d3f2a10-4b6c-4e2a-9f1d-0c5e7a9b1234-fictional-bank-stage-risk-management"
    )
    assert b.external_id == "jobteaser:10234567"
    assert "utm_" not in b.url
    assert a.extra["start_info"] == "Inizio: Gennaio 2027"
    assert a.extra["via"] == "jobteaser_alert"


def test_jobteaser_text_fallback_when_html_has_no_cards() -> None:
    from radar.connectors.email_parsers import jobteaser

    text = (
        "Ciao\n\nStage Marketing\nExample Foods\nStage · Torino, Italia\n"
        "Vedi l'offerta: https://luiss.jobteaser.com/it/job-offers/123-example-foods?utm_source=x\n"
    )
    (p,) = jobteaser.parse("<html><body><p>no links</p></body></html>", text, {})
    assert (p.title, p.company, p.location, p.employment_type) == (
        "Stage Marketing",
        "Example Foods",
        "Torino, Italia",
        "Stage",
    )


def test_generic_alert_unwraps_redirect_and_strips_tracking() -> None:
    msg = load("generic_greenhouse.eml")
    assert parse_message(msg) == []  # sender not configured -> ignored
    gh, lv = parse_message(msg, ["alerts@jobs.example.com"])
    assert gh.url == "https://job-boards.greenhouse.io/fictionalco/jobs/4123456001"
    assert gh.title == "Software Engineering Intern (Summer 2027)"
    assert (gh.company, gh.location) == ("FictionalCo", "Dublin, Ireland")
    assert gh.external_id == "greenhouse:fictionalco:4123456001"
    assert lv.url == "https://jobs.lever.co/example-labs/1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d"
    assert lv.extra == {
        "via": "generic_alert",
        "ats": "lever",
        "alert_date": lv.extra["alert_date"],
    }
    # domain-style sender config works too
    assert len(parse_message(msg, ["@example.com"])) == 2


def test_unwrap_known_wrappers() -> None:
    target = "https://jobs.lever.co/acme/1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d"
    safelink = (
        "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fjobs.lever.co%2Facme"
        "%2F1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d%3Futm_source%3Dx&data=05%7C01&reserved=0"
    )
    google = "https://www.google.com/url?q=" + target + "&sa=D&source=editors"
    assert clean_url(safelink) == target
    assert unwrap_url(google) == target
    li = "https://it.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc&midToken=x&otpToken=y"
    assert clean_url(li) == "https://www.linkedin.com/jobs/view/4012345678"


# ------------------------------------------------------------------ registry / settings
def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key in ("HOST", "PORT", "USER", "PASSWORD", "FOLDER", "SINCE_DAYS"):
        monkeypatch.delenv(f"RADAR_IMAP_{key}", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_registry_enables_email_only_when_env_set(
    client: PoliteClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = _settings(monkeypatch)
    assert build_connectors(client, {}, settings=empty) == []
    partial = _settings(monkeypatch, RADAR_IMAP_HOST="imap.example.com", RADAR_IMAP_USER="u")
    assert build_connectors(client, {}, settings=partial) == []

    full = _settings(
        monkeypatch,
        RADAR_IMAP_HOST="imap.example.com",
        RADAR_IMAP_USER="alerts@example.com",
        RADAR_IMAP_PASSWORD=PASSWORD,
        RADAR_IMAP_FOLDER="Alerts",
        RADAR_IMAP_SINCE_DAYS="7",
    )
    assert PASSWORD not in repr(full)
    cfg = {"email": {"extra_senders": ["alerts@jobs.example.com"]}}
    (conn,) = build_connectors(client, cfg, settings=full)
    assert isinstance(conn, EmailAlertsConnector)
    assert (conn.source, conn.folder, conn.since_days, conn.port) == ("email", "Alerts", 7, 993)
    assert conn.extra_senders == ()  # config cannot add senders: LinkedIn + JobTeaser only
    assert build_connectors(client, cfg, sources=["lever"], settings=full) == []
    disabled = {"email": {"enabled": False}}
    assert build_connectors(client, disabled, settings=full) == []


def test_fixtures_are_synthetic() -> None:
    for path in Path(EMAIL).glob("*.eml"):
        if path.name.startswith("real_"):
            continue
        assert "example.com" in path.read_text(encoding="utf-8")
