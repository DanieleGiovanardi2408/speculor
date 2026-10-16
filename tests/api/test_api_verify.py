"""Fact check in the pack endpoints and POST /api/packs/{id}/verify (demo data, fake LLM)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from radar import config as radar_config
from radar.models import ApplicationPack


@pytest.fixture()
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RADAR_LLM_PROVIDER", "fake")
    monkeypatch.delenv("RADAR_DRAFT_PROVIDER", raising=False)
    radar_config.get_settings.cache_clear()
    yield
    radar_config.get_settings.cache_clear()


def _pack(client: TestClient, db: Session) -> dict:  # type: ignore[type-arg]
    pack = db.exec(select(ApplicationPack).order_by(ApplicationPack.id)).first()  # type: ignore[arg-type]
    assert pack is not None
    return client.get(f"/api/postings/{pack.posting_id}/pack").json()  # type: ignore[no-any-return]


def test_pack_exposes_verification(client: TestClient, db: Session) -> None:
    body = _pack(client, db)
    v = body["verification"]
    assert v is not None and body["verification_stale"] is False
    assert v["status"] == "ok" and v["facts_source"] == "facts.example.yaml"
    assert set(v["counts"]) == {"supported", "unsupported", "check", "total"}
    assert v["counts"]["total"] == len(v["claims"]) > 0
    claim = v["claims"][0]
    assert set(claim) == {"text_id", "claim", "fact_id", "verdict", "source", "reason"}
    assert claim["verdict"] in ("supported", "unsupported", "check")
    drafts = [d for d in body["drafts"] if d["status"] in ("draft", "approved")]
    assert drafts and all(d["verification"] is not None for d in drafts)


def test_patch_makes_verification_stale_and_verify_refreshes(
    client: TestClient, db: Session, fake_llm: None
) -> None:
    body = _pack(client, db)
    text = body["cover_letter"] + "\n\nI am a native English speaker and I won 17 awards."
    patched = client.patch(f"/api/packs/{body['id']}", json={"cover_letter": text}).json()
    assert patched["verification_stale"] is True

    r = client.post(f"/api/packs/{body['id']}/verify")
    assert r.status_code == 200
    out = r.json()
    assert out["verification_stale"] is False
    v = out["verification"]
    assert v["provider"] == "fake" and v["status"] == "ok"
    bad = [c for c in v["claims"] if c["verdict"] == "unsupported" and "17" in c["claim"]]
    assert bad and "native" in (bad[0]["reason"] or "")


def test_verify_precheck_only_and_404(client: TestClient, db: Session) -> None:
    body = _pack(client, db)
    r = client.post(f"/api/packs/{body['id']}/verify", params={"llm": "false"})
    assert r.status_code == 200
    v = r.json()["verification"]
    assert v["status"] == "precheck_only" and v["error"] == "disabled"
    assert client.post("/api/packs/9999/verify").status_code == 404
