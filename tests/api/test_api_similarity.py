from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from radar.models import Posting


def test_similarity_in_schemas_and_sort(client: TestClient, db: Session) -> None:
    postings = list(db.exec(select(Posting).order_by(Posting.id)).all())[:3]
    for p, sim in zip(postings, (0.31, 0.82, 0.55), strict=True):
        p.similarity = sim
        db.add(p)
    db.commit()
    items = client.get("/api/postings", params={"sort": "similarity"}).json()
    ranked = [p["similarity"] for p in items if p["similarity"] is not None]
    assert ranked == [0.82, 0.55, 0.31]
    assert items[0]["id"] == postings[1].id
    assert all(p["similarity"] is None for p in items[3:])
    detail = client.get(f"/api/postings/{postings[0].id}").json()
    assert detail["similarity"] == 0.31
