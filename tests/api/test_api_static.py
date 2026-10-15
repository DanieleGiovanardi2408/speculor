from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from radar.api.main import _mount_frontend


def test_spa_fallback_and_assets(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>radar</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("nope")
    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    _mount_frontend(app, tmp_path)
    c = TestClient(app)
    assert c.get("/api/health").json() == {"status": "ok"}
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert "radar" in c.get("/postings/12").text  # SPA route -> index.html
    assert c.get("/api/unknown").status_code == 404
    assert "nope" not in c.get("/../secret.txt").text
