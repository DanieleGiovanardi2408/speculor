"""POST /api/ingest/jobteaser (the "Salva in Radar" extension) and GET /api/jobteaser/pass."""

from __future__ import annotations

import email
import email.policy
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, select
from typer.testing import CliRunner

from radar import config as radar_config
from radar import db as radar_db
from radar.cli import app as cli_app
from radar.connectors.email_parsers import parse_message
from radar.db import get_setting
from radar.extension_token import SETTING_KEY, check_token, new_token
from radar.models import Posting
from radar.pipeline.dedupe import NON_EXHAUSTIVE_SOURCES
from radar.pipeline.jobteaser_ingest import LAST_PASS_KEY
from radar.pipeline.store import upsert_raw

BASE = "https://luiss.jobteaser.com"
ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
ALERT_ID = "8d3f2a10-4b6c-4e2a-9f1d-0c5e7a9b1234"  # in tests/fixtures/email/jobteaser_digest.eml
EML = Path(__file__).parents[1] / "fixtures" / "email" / "jobteaser_digest.eml"


@pytest.fixture()
def token(api_engine: Engine) -> str:
    with Session(api_engine) as s:
        tok = new_token(s)
        s.commit()
    return tok


def offer(n: int, **kw: Any) -> dict[str, Any]:
    return {
        "title": f"Data Intern {n}",
        "company": "Nebula Analytics",
        "url": f"{BASE}/it/job-offers/{90000000 + n}-nebula-data-intern?from=search",
        "location": "Roma, Italia",
        "date": "12/10/2026",
        **kw,
    }


def post(
    client: TestClient,
    offers: list[dict[str, Any]],
    token: str | None,
    origin: str | None = ORIGIN,
) -> Any:
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if token is not None:
        headers["X-Radar-Token"] = token
    return client.post("/api/ingest/jobteaser", json={"offers": offers}, headers=headers)


# --------------------------------------------------------------------------- auth


def test_rejects_missing_or_wrong_token(client: TestClient, token: str) -> None:
    assert post(client, [offer(1)], None).status_code == 401
    assert post(client, [offer(1)], token + "x").status_code == 401
    assert post(client, [offer(1)], "").status_code == 401


def test_rejects_without_configured_token(client: TestClient) -> None:
    res = post(client, [offer(1)], "anything")
    assert res.status_code == 401 and "radar extension token" in res.json()["detail"]


@pytest.mark.parametrize(
    "origin", [None, "https://luiss.jobteaser.com", "http://localhost:5173", "null", "moz-x://a"]
)
def test_rejects_non_extension_origin(client: TestClient, token: str, origin: str | None) -> None:
    assert post(client, [offer(1)], token, origin=origin).status_code == 403


def test_pinned_extension_id(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_EXTENSION_ID", "abcdefghijklmnopabcdefghijklmnop")
    assert post(client, [offer(1)], token, origin="chrome-extension://other").status_code == 403
    assert post(client, [offer(1)], token).status_code == 200


def test_not_behind_supabase_auth(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_AUTH_REQUIRED", "true")
    assert client.get("/api/today").status_code in (401, 500)  # the app's routes need a JWT
    assert post(client, [offer(1)], token).status_code == 200  # the extension's does not


def test_valid_request_upserts_and_records_last_pass(
    client: TestClient, token: str, api_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    res = post(client, [offer(1), offer(2)], token)
    assert res.status_code == 200
    assert res.json() == {"received": 2, "new": 2, "updated": 0, "ignored": 0}
    with Session(api_engine) as s:
        rows = s.exec(select(Posting).where(Posting.source == "jobteaser")).all()
        assert {p.uid for p in rows} == {"jobteaser:90000001", "jobteaser:90000002"}
        p = next(r for r in rows if r.uid == "jobteaser:90000001")
        assert p.url == f"{BASE}/it/job-offers/90000001-nebula-data-intern"  # no query
        assert p.canonical_url == "https://www.jobteaser.com/job-offers/90000001"
        assert p.company_name == "Nebula Analytics" and p.location == "Roma, Italia"
        assert p.via == "Career Service Luiss" and p.status == "open"
        assert p.filter_state in ("pass", "flagged", "rejected")  # went through the filters
        last = get_setting(LAST_PASS_KEY, session=s)
    assert last["received"] == 2 and last["new"] == 2 and last["at"]
    assert "Nebula" not in caplog.text and "Data Intern" not in caplog.text
    assert "received=2 new=2" in caplog.text


# --------------------------------------------------------------------------- payload


def test_payload_limits(client: TestClient, token: str) -> None:
    assert post(client, [offer(i) for i in range(501)], token).status_code == 422
    assert post(client, [offer(1, title="x" * 1001)], token).status_code == 422
    assert post(client, [{"company": "C"}], token).status_code == 422
    res = client.post(
        "/api/ingest/jobteaser",
        json={"nope": []},
        headers={"Origin": ORIGIN, "X-Radar-Token": token},
    )
    assert res.status_code == 422


def test_invalid_offers_are_ignored_and_strings_capped(
    client: TestClient, token: str, api_engine: Engine
) -> None:
    offers = [
        offer(1, title="T" * 900, company="C" * 900),
        offer(1),  # same offer twice
        offer(2, url=f"{BASE}/it/job-offers?page=2"),  # a listing, not an offer
        offer(3, url="https://example.com/it/job-offers/90000003"),
        offer(4, url="https://luiss.jobteaser.com.evil.example/it/job-offers/90000004"),
        offer(5, url="javascript:alert(1)"),
        offer(6, title="   "),
    ]
    res = post(client, offers, token)
    assert res.json() == {"received": 7, "new": 1, "updated": 0, "ignored": 6}
    with Session(api_engine) as s:
        p = s.exec(select(Posting).where(Posting.uid == "jobteaser:90000001")).one()
    assert len(p.title) == 300 and len(p.company_name) == 200


def test_seen_again_keeps_the_fetched_description(
    client: TestClient, token: str, api_engine: Engine
) -> None:
    post(client, [offer(1)], token)
    with Session(api_engine) as s:
        p = s.exec(select(Posting).where(Posting.uid == "jobteaser:90000001")).one()
        p.raw_text = "Full description from the offer page. " * 20
        p.title = "Data Intern 1 (detail title)"
        s.add(p)
        s.commit()
    res = post(client, [offer(1, location=None)], token)
    assert res.json() == {"received": 1, "new": 0, "updated": 1, "ignored": 0}
    with Session(api_engine) as s:
        p = s.exec(select(Posting).where(Posting.uid == "jobteaser:90000001")).one()
    assert p.raw_text.startswith("Full description") and p.title.endswith("(detail title)")
    assert p.location == "Roma, Italia"


def test_alert_offer_and_extension_offer_dedupe_into_one_posting(
    client: TestClient, token: str, api_engine: Engine
) -> None:
    msg = email.message_from_bytes(EML.read_bytes(), policy=email.policy.default)
    alerts = parse_message(msg)
    with Session(api_engine) as s:
        for raw in alerts:
            upsert_raw(s, raw)
        s.commit()
    first = alerts[0]
    assert first.external_id == f"jobteaser:{ALERT_ID}"
    saved = {"title": first.title, "company": first.company, "url": first.url + "?from=list"}
    res = post(client, [saved], token)
    assert res.json() == {"received": 1, "new": 0, "updated": 1, "ignored": 0}
    with Session(api_engine) as s:
        rows = s.exec(
            select(Posting).where(
                Posting.canonical_url == f"https://www.jobteaser.com/job-offers/{ALERT_ID}"
            )
        ).all()
        assert len(rows) == 2
        primaries = [p for p in rows if p.duplicate_of is None]
        assert len(primaries) == 1
        primary = primaries[0]
        member = next(p for p in rows if p.duplicate_of is not None)
        assert member.uid == f"jobteaser:{ALERT_ID}" and member.duplicate_of == primary.id
        assert {e["source"] for e in primary.sources or []} == {"email", "jobteaser"}
    assert "jobteaser" in NON_EXHAUSTIVE_SOURCES


# --------------------------------------------------------------------------- pass card + CLI


def test_pass_endpoint(client: TestClient, token: str) -> None:
    body = client.get("/api/jobteaser/pass").json()
    assert body["last_pass"] is None and body["extension_configured"] is True
    assert body["remind"] in ("monday", "never")
    labels = [link["label"] for link in body["links"]]
    assert labels[:2] == ["Offerte mirate", "Offerte dei partner"]  # from the example config
    assert all(link["url"].startswith(f"{BASE}/it/job-offers") for link in body["links"])
    post(client, [offer(1)], token)
    body = client.get("/api/jobteaser/pass").json()
    assert body["last_pass"]["new"] == 1 and body["last_pass"]["received"] == 1
    assert body["remind"] is None


def test_pass_endpoint_without_any_config(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_CONFIG_DIR", str(tmp_path / "empty"))
    radar_config.get_settings.cache_clear()
    body = client.get("/api/jobteaser/pass").json()
    assert body["links"] == [] and body["extension_configured"] is False


def test_cli_extension_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    radar_config.get_settings.cache_clear()
    radar_db.get_engine.cache_clear()
    res = CliRunner().invoke(cli_app, ["extension", "token"])
    assert res.exit_code == 0
    tok = res.stdout.strip().splitlines()[-1]
    assert len(tok) >= 40
    with Session(radar_db.init_db()) as s:
        stored = get_setting(SETTING_KEY, session=s)
        assert tok not in str(stored)  # only the hash is stored
        assert check_token(s, tok) and not check_token(s, tok[:-1] + "?")
    second = CliRunner().invoke(cli_app, ["extension", "token"]).stdout.strip().splitlines()[-1]
    with Session(radar_db.init_db()) as s:
        assert check_token(s, second) and not check_token(s, tok)  # replaced
    radar_db.get_engine.cache_clear()
