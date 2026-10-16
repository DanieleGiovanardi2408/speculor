"""Internship vs post-graduation track: GET /api/postings?track=..., summary field, Today."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from radar.api.routes.common import local_today
from radar.models import FilterResult, Posting
from radar.pipeline.filters import POST_GRADUATION_ID


def _mark_post_graduation(db: Session, ref: str | int, *, overridden: bool = False) -> int:
    """Attach a post_graduation flag to a posting (by id, or by title of a non-duplicate)."""
    if isinstance(ref, int):
        posting = db.get(Posting, ref)
    else:
        posting = db.exec(
            select(Posting).where(Posting.title == ref, col(Posting.duplicate_of).is_(None))
        ).first()
    assert posting is not None and posting.id is not None
    db.add(
        FilterResult(
            posting_id=posting.id,
            rule_id=POST_GRADUATION_ID,
            action="flag",
            reason="post-graduation graduate programme (start Sep 2027)",
            evidence="September 2027 intake",
            overridden=overridden,
        )
    )
    if posting.filter_state == "pass":
        posting.filter_state = "flagged"
        db.add(posting)
    db.commit()
    return posting.id


def _ids(items: list[dict[str, Any]]) -> set[int]:
    return {p["id"] for p in items}


def test_summary_has_track_field(client: TestClient) -> None:
    items = client.get("/api/postings").json()
    assert items and all(p["track"] == "internship" for p in items)
    detail = client.get(f"/api/postings/{items[0]['id']}").json()
    assert detail["track"] == "internship"


def test_track_param_splits_postings(client: TestClient, db: Session) -> None:
    before = client.get("/api/postings").json()
    pid = _mark_post_graduation(db, "Machine Learning Intern")

    default = client.get("/api/postings").json()
    assert pid not in _ids(default)
    assert len(default) == len(before) - 1
    assert client.get("/api/postings", params={"track": "internship"}).json() == default

    post = client.get("/api/postings", params={"track": "post_graduation"}).json()
    assert _ids(post) == {pid}
    assert post[0]["track"] == "post_graduation"
    assert post[0]["filter_state"] == "flagged"  # flagged, not an exclusion
    assert any(h["rule_id"] == POST_GRADUATION_ID for h in post[0]["filter_hits"])

    every = client.get("/api/postings", params={"track": "all"}).json()
    assert _ids(every) == _ids(default) | {pid}

    assert client.get(f"/api/postings/{pid}").json()["track"] == "post_graduation"
    assert client.get("/api/postings", params={"track": "nope"}).status_code == 422


def test_track_combines_with_rejected_switches(client: TestClient, db: Session) -> None:
    rejected = client.get("/api/postings", params={"only_rejected": "true"}).json()
    pid = _mark_post_graduation(db, int(rejected[0]["id"]))
    assert pid not in _ids(client.get("/api/postings", params={"only_rejected": "true"}).json())
    got = client.get(
        "/api/postings", params={"only_rejected": "true", "track": "post_graduation"}
    ).json()
    assert _ids(got) == {pid}
    assert got[0]["filter_state"] == "rejected"
    # rejected stays hidden by default in the post-graduation view too
    assert pid not in _ids(client.get("/api/postings", params={"track": "post_graduation"}).json())


def test_overridden_post_graduation_hit_is_internship(client: TestClient, db: Session) -> None:
    pid = _mark_post_graduation(db, "Machine Learning Intern", overridden=True)
    assert pid in _ids(client.get("/api/postings").json())
    assert client.get(f"/api/postings/{pid}").json()["track"] == "internship"


def test_override_moves_post_graduation_to_internship(client: TestClient, db: Session) -> None:
    pid = _mark_post_graduation(db, "Machine Learning Intern")
    body = client.post(f"/api/postings/{pid}/override").json()
    assert body["track"] == "internship"
    hit = next(h for h in body["filter_hits"] if h["rule_id"] == POST_GRADUATION_ID)
    assert hit["overridden"] is True
    assert pid in _ids(client.get("/api/postings").json())


def test_today_new_uses_internship_track(client: TestClient, db: Session) -> None:
    before = client.get("/api/today").json()
    assert before["new_post_graduation"] == 0
    assert "Machine Learning Intern" in {p["title"] for p in before["new_since_yesterday"]}

    _mark_post_graduation(db, "Machine Learning Intern")
    after = client.get("/api/today").json()
    titles = {p["title"] for p in after["new_since_yesterday"]}
    assert "Machine Learning Intern" not in titles
    assert len(after["new_since_yesterday"]) == len(before["new_since_yesterday"]) - 1
    assert after["new_post_graduation"] == 1
    assert all(p["track"] == "internship" for p in after["new_since_yesterday"])


def test_today_upcoming_deadlines_internship_only(client: TestClient, db: Session) -> None:
    before = client.get("/api/today").json()["upcoming_deadlines"]
    assert before
    target = before[0]
    _mark_post_graduation(db, int(target["id"]))
    after = client.get("/api/today").json()["upcoming_deadlines"]
    assert target["id"] not in _ids(after)
    today = local_today()
    for p in after:
        assert p["track"] == "internship"
        assert p["deadline"] is not None
        assert today.isoformat() <= p["deadline"] <= (today + timedelta(days=14)).isoformat()
