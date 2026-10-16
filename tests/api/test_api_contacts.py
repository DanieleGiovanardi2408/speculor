"""POST /api/postings/{id}/contacts/find and PATCH /api/contacts/{id} (offline fake search)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from radar import config as radar_config
from radar.models import ApplicationPack, Contact, LLMUsage


@pytest.fixture()
def fake_search(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RADAR_CONTACTS_PROVIDER", "fake")
    monkeypatch.setenv("RADAR_CONTACTS_MONTHLY_CAP_EUR", "5")
    monkeypatch.setenv("RADAR_CONTACTS_DAILY_CALL_CAP", "10")
    radar_config.get_settings.cache_clear()
    yield
    radar_config.get_settings.cache_clear()


def _pack_posting(db: Session) -> int:
    pack = db.exec(select(ApplicationPack)).first()
    assert pack is not None
    return pack.posting_id


def test_find_contacts_stores_needs_review(
    client: TestClient, db: Session, fake_search: None
) -> None:
    pid = _pack_posting(db)
    r = client.post(f"/api/postings/{pid}/contacts/find")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"] == 2 and len(body["contacts"]) == 2
    c = body["contacts"][0]
    assert c["needs_review"] is True and c["source"] == "search" and c["source_query"]
    assert c["profile_url"].startswith("https://example.com/")
    assert body["budget"]["calls_today"] == 1 and body["budget"]["monthly_cap_eur"] == 5
    pack = client.get(f"/api/postings/{pid}/pack").json()
    assert sum(1 for x in pack["contacts"] if x["needs_review"]) == 2
    # second search for the same company: deduplicated, nothing new
    again = client.post(f"/api/postings/{pid}/contacts/find").json()
    assert again["stored"] == 0 and again["contacts"] == []


def test_find_contacts_refused_over_cap(client: TestClient, db: Session, fake_search: None) -> None:
    db.add(LLMUsage(provider="groq", model="m", purpose="contacts", est_cost_eur=5))
    db.commit()
    pid = _pack_posting(db)
    r = client.post(f"/api/postings/{pid}/contacts/find")
    assert r.status_code == 429
    assert "mensile" in r.json()["detail"]
    assert not db.exec(select(Contact).where(Contact.source == "search")).all()


def test_find_contacts_daily_cap(
    client: TestClient, db: Session, fake_search: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_CONTACTS_DAILY_CALL_CAP", "0")
    radar_config.get_settings.cache_clear()
    r = client.post(f"/api/postings/{_pack_posting(db)}/contacts/find")
    assert r.status_code == 429 and "giornaliero" in r.json()["detail"]


def test_find_contacts_unknown_posting(client: TestClient, fake_search: None) -> None:
    assert client.post("/api/postings/99999/contacts/find").status_code == 404


def test_find_contacts_missing_key(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_CONTACTS_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "")
    radar_config.get_settings.cache_clear()
    try:
        r = client.post(f"/api/postings/{_pack_posting(db)}/contacts/find")
    finally:
        radar_config.get_settings.cache_clear()
    assert r.status_code == 503


def test_patch_contact_review_flag(client: TestClient, db: Session) -> None:
    pid = _pack_posting(db)
    created = client.post(
        "/api/contacts", json={"posting_id": pid, "name": "Anna Prova", "role": "Lead"}
    ).json()
    assert created["needs_review"] is False and created["source"] == "manual"
    r = client.patch(f"/api/contacts/{created['id']}", json={"needs_review": True})
    assert r.status_code == 200 and r.json()["needs_review"] is True
    r = client.patch(f"/api/contacts/{created['id']}", json={"needs_review": False})
    assert r.json()["needs_review"] is False
    assert client.patch("/api/contacts/99999", json={"needs_review": False}).status_code == 404
