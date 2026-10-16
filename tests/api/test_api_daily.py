"""Daily-use additions: skip reasons, undo restore, run report, LLM usage aggregates."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session

from radar.api.routes.common import local_today
from radar.api.routes.skip import compose_note, split_skip
from radar.models import Posting


def _pid(client: TestClient, title: str) -> int:
    items: list[dict[str, Any]] = client.get("/api/postings").json()
    return next(p["id"] for p in items if p["title"] == title)


def test_split_and_compose_skip_note() -> None:
    assert split_skip("[Saltato: Sede] troppo lontano") == ("Sede", "troppo lontano")
    assert split_skip("[Saltato: Lingua]") == ("Lingua", None)
    assert split_skip("nota normale") == (None, "nota normale")
    assert split_skip(None) == (None, None)
    assert compose_note("Altro: [x]\nok", "body") == "[Saltato: Altro: x ok] body"
    assert compose_note(None, "body") == "body"
    assert compose_note("Sede", None) == "[Saltato: Sede]"


def test_skip_with_reason_keeps_note_and_undo_clears_it(client: TestClient, db: Session) -> None:
    pid = _pid(client, "Simulation Intern")
    client.patch(f"/api/postings/{pid}", json={"note": "Chiedere a Marta"})
    r = client.patch(f"/api/postings/{pid}", json={"skip_reason": "Sede: troppo lontano"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_status"] == "skipped"
    assert body["skip_reason"] == "Sede: troppo lontano"
    assert body["note"] == "[Saltato: Sede: troppo lontano] Chiedere a Marta"
    stored = db.get(Posting, pid)
    assert stored is not None and stored.note == body["note"]

    # Editing the note body keeps the reason.
    r = client.patch(f"/api/postings/{pid}", json={"note": "Nuova nota"})
    assert r.json()["skip_reason"] == "Sede: troppo lontano"
    assert r.json()["note"] == "[Saltato: Sede: troppo lontano] Nuova nota"
    listed = next(
        p
        for p in client.get("/api/postings", params={"user_status": "skipped"}).json()
        if p["id"] == pid
    )
    assert listed["skip_reason"] == "Sede: troppo lontano"

    # Back to todo: the reason goes away, the note stays.
    r = client.patch(f"/api/postings/{pid}", json={"user_status": "todo"})
    assert r.json()["skip_reason"] is None and r.json()["note"] == "Nuova nota"
    assert client.patch(f"/api/postings/{pid}", json={"skip_reason": "x" * 201}).status_code == 422


def test_demo_has_a_skip_reason(client: TestClient) -> None:
    skipped = client.get("/api/postings", params={"user_status": "skipped"}).json()
    assert any(p["skip_reason"] == "Fuori target" for p in skipped)


def test_undo_override_puts_posting_back(client: TestClient) -> None:
    rejected = client.get("/api/postings", params={"only_rejected": "true"}).json()
    pid = rejected[0]["id"]
    assert client.post(f"/api/postings/{pid}/override").json()["filter_state"] == "overridden"
    r = client.delete(f"/api/postings/{pid}/override")
    assert r.status_code == 200
    assert r.json()["filter_state"] == "rejected"
    assert not any(h["overridden"] for h in r.json()["filter_hits"])
    again = client.get("/api/postings", params={"only_rejected": "true"}).json()
    assert any(p["id"] == pid for p in again)
    assert client.delete("/api/postings/99999/override").status_code == 404


def test_run_detail_has_report(client: TestClient) -> None:
    runs = client.get("/api/runs").json()
    r = client.get(f"/api/runs/{runs[0]['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == runs[0]["id"] and body["status"] == runs[0]["status"]
    assert body["report_md"].startswith("# Radar run")
    assert client.get("/api/runs/99999").status_code == 404


def test_usage_aggregates(client: TestClient) -> None:
    r = client.get("/api/usage")
    assert r.status_code == 200
    u = r.json()
    assert u["until"] == local_today().isoformat()
    purposes = {row["purpose"] for row in u["rows"]}
    assert {"scoring", "pack", "draft"} <= purposes
    period = {t["model"]: t for t in u["period"]}
    assert period["openai/gpt-oss-20b"]["requests"] == 8
    assert period["openai/gpt-oss-20b"]["input_tokens"] == 20_100
    assert period["openai/gpt-oss-120b"]["requests"] == 7
    total_today = sum(t["requests"] for t in u["today"])
    assert 0 <= total_today <= 15
    assert client.get("/api/usage", params={"days": 0}).status_code == 422
    assert len(client.get("/api/usage", params={"days": 1}).json()["rows"]) <= len(u["rows"])
