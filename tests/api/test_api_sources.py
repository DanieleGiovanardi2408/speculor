"""Postings expose every source of a de-duplicated job (demo: one job also seen on LinkedIn)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _all(client: TestClient, **params: str) -> list[dict[str, object]]:
    r = client.get("/api/postings", params={"track": "all", "include_rejected": "true", **params})
    assert r.status_code == 200
    data: list[dict[str, object]] = r.json()
    return data


def test_list_and_detail_expose_sources(client: TestClient) -> None:
    items = _all(client)
    assert all(isinstance(p["sources"], list) and p["sources"] for p in items)
    multi = [p for p in items if len(p["sources"]) > 1]  # type: ignore[arg-type]
    assert len(multi) == 1
    job = multi[0]
    labels = [s["label"] for s in job["sources"]]  # type: ignore[attr-defined]
    assert labels == ["Sito aziendale", "LinkedIn"]
    assert {"source", "url", "via", "label", "listed"} <= set(job["sources"][0])  # type: ignore[index]
    detail = client.get(f"/api/postings/{job['id']}").json()
    assert [s["label"] for s in detail["sources"]] == labels


def test_source_filter_matches_any_source_of_the_job(client: TestClient) -> None:
    linkedin = _all(client, source="linkedin")
    ids = {p["id"] for p in linkedin}
    multi = next(p for p in _all(client) if len(p["sources"]) > 1)  # type: ignore[arg-type]
    assert multi["id"] in ids  # board-first job also listed on LinkedIn
    assert all(
        any(s["source"] == "linkedin" for s in p["sources"])  # type: ignore[attr-defined]
        for p in linkedin
    )
