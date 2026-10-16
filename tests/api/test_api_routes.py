from __future__ import annotations

import sys
import types
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from radar.api.routes.common import local_today
from radar.demo import AVAILABILITY_LINE
from radar.models import Application, ApplicationPack, Draft, Posting


def _by_title(items: list[dict[str, Any]], title: str) -> dict[str, Any]:
    return next(p for p in items if p["title"] == title)


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


# --- postings ------------------------------------------------------------------------------


def test_postings_default_excludes_rejected_and_duplicates(client: TestClient) -> None:
    items = client.get("/api/postings").json()
    assert len(items) == 36  # 40 demo postings - 4 rejected; duplicate hidden
    assert all(p["filter_state"] != "rejected" for p in items)
    assert sum(1 for p in items if p["title"] == "Machine Learning Intern") == 1
    sample = items[0]
    for key in ("priority", "score", "filter_hits", "has_pack", "first_seen", "zone"):
        assert key in sample
    assert sample["first_seen"].endswith("Z") or "+00:00" in sample["first_seen"]


def test_postings_sorted_by_priority_then_deadline(client: TestClient) -> None:
    items = client.get("/api/postings").json()
    rank = {"Alta": 0, "Media": 1, "Bassa": 2, None: 3}
    ranks = [rank[p["priority"]] for p in items]
    assert ranks == sorted(ranks)
    alta = [p for p in items if p["priority"] == "Alta"]
    dated = [p["deadline"] for p in alta if p["deadline"]]
    assert dated == sorted(dated)
    # undated Alta come after dated Alta
    first_undated = next(i for i, p in enumerate(alta) if p["deadline"] is None)
    assert all(p["deadline"] is None for p in alta[first_undated:])


def test_postings_other_sorts(client: TestClient) -> None:
    by_deadline = client.get("/api/postings", params={"sort": "deadline"}).json()
    dated = [p["deadline"] for p in by_deadline if p["deadline"]]
    assert dated == sorted(dated)
    assert by_deadline[-1]["deadline"] is None
    by_company = client.get("/api/postings", params={"sort": "company"}).json()
    names = [p["company_name"].lower() for p in by_company]
    assert names == sorted(names)
    newest = client.get("/api/postings", params={"sort": "first_seen"}).json()
    seen = [p["first_seen"] for p in newest]
    assert seen == sorted(seen, reverse=True)
    assert client.get("/api/postings", params={"sort": "bogus"}).status_code == 422


def test_postings_filters(client: TestClient) -> None:
    alta = client.get("/api/postings", params={"priority": "Alta"}).json()
    assert alta and all(p["priority"] == "Alta" for p in alta)
    it = client.get("/api/postings", params={"zone": "Italia"}).json()
    assert it and all(p["country_code"] == "IT" for p in it)
    closed = client.get("/api/postings", params={"status": "closed"}).json()
    assert len(closed) == 3
    sent = client.get("/api/postings", params={"user_status": "sent"}).json()
    assert len(sent) == 6
    lever = client.get("/api/postings", params={"source": "lever", "type": "Stage"}).json()
    assert lever and all(p["source"] == "lever" and p["type"] == "Stage" for p in lever)
    multi = client.get("/api/postings", params={"priority": "Alta,Media"}).json()
    assert {p["priority"] for p in multi} == {"Alta", "Media"}


def test_postings_q_search(client: TestClient) -> None:
    by_company = client.get("/api/postings", params={"q": "nebbia"}).json()
    assert by_company and all(p["company_name"] == "Nebbia Labs" for p in by_company)
    by_title = client.get("/api/postings", params={"q": "FRAUD"}).json()
    assert [p["title"] for p in by_title] == ["Fraud Analytics Intern"]
    by_location = client.get("/api/postings", params={"q": "utrecht"}).json()
    assert [p["location"] for p in by_location] == ["Utrecht"]
    assert client.get("/api/postings", params={"q": "zzz-nothing"}).json() == []


def test_postings_rejected_switches(client: TestClient) -> None:
    only = client.get("/api/postings", params={"only_rejected": "true"}).json()
    assert len(only) == 4 and all(p["filter_state"] == "rejected" for p in only)
    assert all(h["action"] == "reject" and h["evidence"] for p in only for h in p["filter_hits"])
    both = client.get("/api/postings", params={"include_rejected": "true"}).json()
    assert len(both) == 40


def test_priority_comes_from_latest_score(client: TestClient) -> None:
    cv = _by_title(client.get("/api/postings").json(), "Computer Vision Intern")
    assert cv["priority"] == "Alta"
    assert cv["score"]["priority"] == "Alta"


def test_posting_detail_and_404(client: TestClient) -> None:
    items = client.get("/api/postings").json()
    ml = _by_title(items, "Machine Learning Intern")
    d = client.get(f"/api/postings/{ml['id']}").json()
    assert d["has_pack"] is True and d["pack_id"] is not None
    assert d["raw_text"] and d["history"][0]["kind"] == "new"
    assert client.get("/api/postings/99999").status_code == 404


def test_patch_posting(client: TestClient) -> None:
    pid = _by_title(client.get("/api/postings").json(), "Simulation Intern")["id"]
    r = client.patch(f"/api/postings/{pid}", json={"user_status": "skipped", "note": "Too long"})
    assert r.status_code == 200
    assert r.json()["user_status"] == "skipped" and r.json()["note"] == "Too long"
    r = client.patch(f"/api/postings/{pid}", json={"status": "closed"})
    assert r.json()["status"] == "closed"
    assert client.patch(f"/api/postings/{pid}", json={"user_status": "x"}).status_code == 422
    assert client.patch("/api/postings/99999", json={"note": "x"}).status_code == 404


def test_override(client: TestClient) -> None:
    rejected = client.get("/api/postings", params={"only_rejected": "true"}).json()
    pid = rejected[0]["id"]
    r = client.post(f"/api/postings/{pid}/override")
    assert r.status_code == 200
    body = r.json()
    assert body["filter_state"] == "overridden"
    assert all(h["overridden"] for h in body["filter_hits"] if h["action"] == "reject")
    assert any(p["id"] == pid for p in client.get("/api/postings").json())
    assert client.post("/api/postings/99999/override").status_code == 404


def test_mark_sent_creates_application(client: TestClient, db: Session) -> None:
    pid = _by_title(client.get("/api/postings").json(), "Fraud Analytics Intern")["id"]
    r = client.post(
        f"/api/postings/{pid}/mark-sent", json={"channel": "Sito aziendale", "cv_version": "CV v2"}
    )
    assert r.status_code == 200
    app = r.json()
    assert app["posting_id"] == pid and app["company"] == "Ventaglio Pay"
    assert app["stage"] == "Inviata" and app["date_sent"] == local_today().isoformat()
    assert app["followup_due"] is False
    assert db.get(Posting, pid).user_status == "sent"  # type: ignore[union-attr]
    again = client.post(
        f"/api/postings/{pid}/mark-sent",
        json={"channel": "Sito aziendale", "cv_version": "CV v2", "date_sent": "2026-01-01"},
    )
    assert again.json()["id"] == app["id"]  # idempotent
    assert len(db.exec(select(Application).where(Application.posting_id == pid)).all()) == 1
    assert client.post(f"/api/postings/{pid}/mark-sent", json={}).status_code == 422


def test_manual_add_uses_pipeline(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def add_posting_from_url(session: Session, url: str) -> Posting:
        p = Posting(
            uid="manual:x1",
            source="manual",
            external_id="x1",
            url=url,
            canonical_url=url,
            title="Manual Intern",
            company_name="Pianura Data",
        )
        session.add(p)
        session.flush()
        return p

    def apply_filters(session: Session, posting: Posting) -> str:
        posting.filter_state = "flagged"
        return "flagged"

    monkeypatch.setitem(
        sys.modules,
        "radar.pipeline.manual",
        types.SimpleNamespace(add_posting_from_url=add_posting_from_url),
    )
    monkeypatch.setitem(
        sys.modules, "radar.pipeline.filters", types.SimpleNamespace(apply_filters=apply_filters)
    )
    r = client.post("/api/postings", json={"url": "https://example.com/jobs/new"})
    assert r.status_code == 200
    assert r.json()["title"] == "Manual Intern" and r.json()["filter_state"] == "flagged"


def test_manual_add_501_when_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "radar.pipeline.manual", None)
    r = client.post("/api/postings", json={"url": "https://example.com/jobs/new"})
    assert r.status_code == 501


# --- packs -----------------------------------------------------------------------------------


def test_get_pack_and_rules(client: TestClient) -> None:
    items = client.get("/api/postings").json()
    with_pack = [p for p in items if p["has_pack"]]
    assert len(with_pack) == 3
    letters = []
    for p in with_pack:
        pack = client.get(f"/api/postings/{p['id']}/pack").json()
        assert pack["posting"]["id"] == p["id"]
        assert pack["form_answers"] and pack["contacts"]
        letter = pack["cover_letter"]
        assert letter.splitlines()[0] == AVAILABILITY_LINE
        assert "—" not in letter and len(letter.split()) <= 250
        letters.append(letter)
    assert sum("[CHECK:" in x for x in letters) == 1
    no_pack = next(p for p in items if not p["has_pack"])
    assert client.get(f"/api/postings/{no_pack['id']}/pack").status_code == 404


def test_patch_pack(client: TestClient, db: Session) -> None:
    pack = db.exec(select(ApplicationPack)).first()
    assert pack is not None
    r = client.patch(
        f"/api/packs/{pack.id}",
        json={
            "cover_letter": "New text",
            "form_answers": [{"question": "Q", "answer": "A"}],
            "cv_version": "CV v3",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cover_letter"] == "New text" and body["cv_version"] == "CV v3"
    assert body["form_answers"] == [{"question": "Q", "answer": "A"}]
    assert client.patch("/api/packs/9999", json={}).status_code == 404


def test_generate_pack(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = _by_title(client.get("/api/postings").json(), "Data Engineering Intern")["id"]
    calls: list[dict[str, Any]] = []

    def build_packs(
        session: Session, run_id: Any = None, limit: int = 1, posting_id: int | None = None
    ) -> dict[str, int]:
        calls.append({"limit": limit, "posting_id": posting_id})
        assert posting_id is not None
        session.add(ApplicationPack(posting_id=posting_id, cover_letter="Hi", provider="fake"))
        return {"built": 1}

    monkeypatch.setitem(
        sys.modules, "radar.llm.packs", types.SimpleNamespace(build_packs=build_packs)
    )
    r = client.post(f"/api/postings/{pid}/pack")
    assert r.status_code == 200 and r.json()["cover_letter"] == "Hi"
    assert calls == [{"limit": 1, "posting_id": pid}]


# --- contacts and drafts ----------------------------------------------------------------------


def test_contacts_create_delete(client: TestClient) -> None:
    pid = client.get("/api/postings").json()[0]["id"]
    r = client.post("/api/contacts", json={"posting_id": pid, "name": "Test Persona"})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert client.delete(f"/api/contacts/{cid}").status_code == 204
    assert client.delete(f"/api/contacts/{cid}").status_code == 404
    assert client.post("/api/contacts", json={"posting_id": 99999, "name": "X"}).status_code == 404


def test_drafts_list(client: TestClient) -> None:
    drafts = client.get("/api/drafts").json()
    assert len(drafts) == 4 and all(d["status"] == "draft" for d in drafts)
    assert all(d["posting_title"] and d["company_name"] for d in drafts)
    assert any(d["contact_name"] for d in drafts)
    approved = client.get("/api/drafts", params={"status": "approved"}).json()
    assert len(approved) == 1
    linkedin = client.get("/api/drafts", params={"channel": "linkedin"}).json()
    assert all(d["channel"] == "linkedin" for d in linkedin)


def test_draft_patch_rules(client: TestClient, db: Session) -> None:
    did = client.get("/api/drafts").json()[0]["id"]
    assert client.patch(f"/api/drafts/{did}", json={"status": "sent"}).status_code == 422
    assert client.patch(f"/api/drafts/{did}", json={"status": "failed"}).status_code == 422
    r = client.patch(f"/api/drafts/{did}", json={"status": "approved"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    row = db.get(Draft, did)
    assert row is not None and row.approved_at is not None
    assert client.patch("/api/drafts/9999", json={"text": "x"}).status_code == 404


def test_draft_edit_runs_style_check(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "radar.llm.style",
        types.SimpleNamespace(
            check=lambda text, kind, language: ["too short"] if len(text) < 10 else []
        ),
    )
    did = client.get("/api/drafts").json()[0]["id"]
    r = client.patch(f"/api/drafts/{did}", json={"text": "Hi"})
    assert r.json()["text"] == "Hi" and r.json()["style_issues"] == ["too short"]


def test_draft_create_and_mark_sent(client: TestClient) -> None:
    pid = client.get("/api/postings").json()[0]["id"]
    r = client.post("/api/drafts", json={"posting_id": pid, "kind": "email", "text": "Hello there"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "draft" and d["channel"] == "email"
    assert (
        client.post("/api/drafts", json={"posting_id": pid, "kind": "fax", "text": "x"}).status_code
        == 422
    )
    sent = client.post(f"/api/drafts/{d['id']}/mark-sent").json()
    assert sent["status"] == "sent" and sent["sent_at"] is not None
    assert client.patch(f"/api/drafts/{d['id']}", json={"text": "edit"}).status_code == 409


def test_draft_for_contact(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    pack = client.get("/api/postings", params={"q": "Machine Learning Intern"}).json()[0]
    contact = client.get(f"/api/postings/{pack['id']}/pack").json()["contacts"][0]

    def draft_for_contact(session: Session, posting_id: int, contact_id: int, kind: str) -> Draft:
        d = Draft(posting_id=posting_id, contact_id=contact_id, kind=kind, text="Generated")
        session.add(d)
        session.flush()
        return d

    monkeypatch.setitem(
        sys.modules, "radar.llm.packs", types.SimpleNamespace(draft_for_contact=draft_for_contact)
    )
    r = client.post(
        "/api/drafts/for-contact",
        json={"posting_id": pack["id"], "contact_id": contact["id"], "kind": "linkedin_message"},
    )
    assert r.status_code == 200
    assert r.json()["contact_name"] == contact["name"] and r.json()["status"] == "draft"


# --- applications ------------------------------------------------------------------------------


def test_applications_list_and_followups(client: TestClient) -> None:
    apps = client.get("/api/applications").json()
    assert len(apps) == 8
    due = [a for a in apps if a["followup_due"]]
    assert {a["company"] for a in due} == {"Pianura Data", "Tramonto Energy"}


def test_application_create_and_patch(client: TestClient) -> None:
    body = {
        "company": "Brina AI",
        "role": "Intern",
        "date_sent": "2026-09-01",
        "channel": "Email",
        "cv_version": "CV v1",
    }
    r = client.post("/api/applications", json=body)
    assert r.status_code == 200 and r.json()["stage"] == "Inviata"
    aid = r.json()["id"]
    r = client.patch(
        f"/api/applications/{aid}",
        json={"stage": "Colloquio 1", "responded": True, "next_step": "Call"},
    )
    assert r.json()["stage"] == "Colloquio 1" and r.json()["next_step"] == "Call"
    assert client.patch(f"/api/applications/{aid}", json={"stage": "Boh"}).status_code == 422
    assert client.patch("/api/applications/9999", json={}).status_code == 404
    assert client.post("/api/applications", json={"company": "X"}).status_code == 422


def test_application_stats_math(client: TestClient) -> None:
    stats = client.get("/api/applications/stats").json()
    by_channel = {r["key"]: r for r in stats["by_channel"]}
    assert by_channel["Sito aziendale"] == {
        "key": "Sito aziendale",
        "sent": 4,
        "responses": 2,
        "interviews": 0,
        "interview_rate": 0.0,
    }
    assert by_channel["Email"]["interview_rate"] == 1.0
    assert by_channel["Referral"]["responses"] == 1  # Test/case counts as a response
    by_cv = {r["key"]: r for r in stats["by_cv"]}
    assert by_cv["CV v2"] == {
        "key": "CV v2",
        "sent": 4,
        "responses": 2,
        "interviews": 1,
        "interview_rate": 0.25,
    }
    assert by_cv["CV v1"]["responses"] == 2
    assert stats["by_stage"]["Inviata"] == 3 and stats["by_stage"]["Offerta"] == 0
    assert sum(stats["by_stage"].values()) == 8


# --- today / companies / settings / runs ------------------------------------------------------


def test_today(client: TestClient) -> None:
    t = client.get("/api/today").json()
    assert t["weekly_goal"] == 10
    assert t["applications_this_week"] >= 1
    new_titles = {p["title"] for p in t["new_since_yesterday"]}
    assert "Machine Learning Intern" in new_titles
    assert "Senior Perception Engineer" not in new_titles  # rejected by filters
    assert len(t["new_since_yesterday"]) == 5
    assert {p["title"] for p in t["closed_since_yesterday"]} == {
        "Portfolio Analyst Intern",
        "Operations Strategy Intern",
    }
    today = local_today()
    for p in t["upcoming_deadlines"]:
        d = date.fromisoformat(p["deadline"])
        assert today <= d <= today + timedelta(days=14)
        assert p["status"] == "open" and p["user_status"] == "todo"
        assert p["filter_state"] != "rejected"
    deadlines = [p["deadline"] for p in t["upcoming_deadlines"]]
    assert deadlines == sorted(deadlines) and len(deadlines) >= 5
    assert len(t["followups_due"]) == 2
    assert t["pending_approvals"] == 4
    assert t["last_run"]["status"] == "partial"


def test_applications_this_week_counts_today(client: TestClient) -> None:
    before = client.get("/api/today").json()["applications_this_week"]
    client.post(
        "/api/applications",
        json={
            "company": "Corvo Analytics",
            "role": "X",
            "date_sent": local_today().isoformat(),
            "channel": "Email",
            "cv_version": "CV v1",
        },
    )
    client.post(
        "/api/applications",
        json={
            "company": "Corvo Analytics",
            "role": "Y",
            "date_sent": "2020-01-01",
            "channel": "Email",
            "cv_version": "CV v1",
        },
    )
    assert client.get("/api/today").json()["applications_this_week"] == before + 1


def test_companies(client: TestClient) -> None:
    cos = client.get("/api/companies").json()
    assert len(cos) == 10
    nebbia = next(c for c in cos if c["name"] == "Nebbia Labs")
    assert nebbia["open_postings"] == 3
    r = client.patch(
        f"/api/companies/{nebbia['id']}",
        json={"notes": "Great team", "active": False, "priority": "Media"},
    )
    assert r.status_code == 200
    assert r.json()["notes"] == "Great team" and r.json()["active"] is False
    assert client.patch("/api/companies/9999", json={}).status_code == 404


def test_settings(client: TestClient) -> None:
    s = client.get("/api/settings").json()
    assert s["weekly_goal"] == 10
    assert s["sources_enabled"] == {
        k: True
        for k in ("greenhouse", "lever", "ashby", "smartrecruiters", "workday", "linkedin", "email")
    }
    r = client.put("/api/settings", json={"weekly_goal": 7, "sources_enabled": {"linkedin": False}})
    assert r.json()["weekly_goal"] == 7
    assert r.json()["sources_enabled"]["linkedin"] is False
    assert r.json()["sources_enabled"]["lever"] is True
    r = client.put("/api/settings", json={"sources_enabled": {"lever": False}})
    assert r.json()["weekly_goal"] == 7 and r.json()["sources_enabled"]["linkedin"] is False
    assert client.get("/api/today").json()["weekly_goal"] == 7
    assert client.put("/api/settings", json={"weekly_goal": -1}).status_code == 422


def test_runs(client: TestClient) -> None:
    runs = client.get("/api/runs").json()
    assert len(runs) == 2 and runs[0]["started_at"] > runs[1]["started_at"]
    assert runs[0]["stats"]["new"] == 6
    assert len(client.get("/api/runs", params={"limit": 1}).json()) == 1


def test_demo_text_follows_writing_rules(db: Session) -> None:
    style = pytest.importorskip("radar.llm.style")
    for pack in db.exec(select(ApplicationPack)).all():
        issues = style.check(pack.cover_letter, "cover_letter", pack.language)
        # the first line is asserted exactly in test_get_pack_and_rules
        assert [i for i in issues if not i.startswith("first line")] == [], issues
    for draft in db.exec(select(Draft)).all():
        assert style.check(draft.text, draft.kind, "en") == [], draft.text


def test_demo_is_idempotent(db: Session) -> None:
    from radar.demo import load_demo

    assert load_demo(db) == {"skipped": 1}
