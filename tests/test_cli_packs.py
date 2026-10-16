"""`radar packs regenerate` / `radar packs verify` / `radar facts check` on a temp demo DB."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from radar import config as radar_config
from radar import db as radar_db
from radar.cli import app
from radar.demo import load_demo
from radar.models import ApplicationPack

runner = CliRunner()


@pytest.fixture()
def demo_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("RADAR_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("RADAR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RADAR_LLM_PROVIDER", "fake")
    monkeypatch.delenv("RADAR_DRAFT_PROVIDER", raising=False)
    radar_config.get_settings.cache_clear()
    radar_db.get_engine.cache_clear()
    engine = radar_db.init_db()
    with Session(engine) as s:
        load_demo(s)
        s.commit()
    yield tmp_path
    radar_db.get_engine.cache_clear()
    radar_config.get_settings.cache_clear()


def _packs(path: Path) -> list[ApplicationPack]:
    with Session(radar_db.get_engine()) as s:
        return list(s.exec(select(ApplicationPack)).all())


def test_packs_verify(demo_db: Path) -> None:
    result = runner.invoke(app, ["packs", "verify", "--all", "--no-llm"])
    assert result.exit_code == 0, result.output
    assert result.output.count("non supportate") == 3
    assert all(
        p.verification and p.verification["status"] == "precheck_only" for p in _packs(demo_db)
    )
    assert runner.invoke(app, ["packs", "verify"]).exit_code == 1


def test_packs_regenerate_writes_snapshots(demo_db: Path) -> None:
    pack = _packs(demo_db)[0]
    out = demo_db / "diffs"
    result = runner.invoke(
        app, ["packs", "regenerate", "--posting-id", str(pack.posting_id), "--out-dir", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "built=1" in result.output
    assert pack.cover_letter.splitlines()[2] not in result.output  # texts only in files
    (run_dir,) = list(out.iterdir())
    names = sorted(p.name for p in run_dir.iterdir())
    pid = pack.posting_id
    assert names == [f"posting-{pid}.after.md", f"posting-{pid}.before.md", f"posting-{pid}.diff"]
    diff = (run_dir / f"posting-{pid}.diff").read_text("utf-8")
    assert diff.startswith("---") and "## Fact check" in (
        run_dir / f"posting-{pid}.after.md"
    ).read_text("utf-8")
    after = next(p for p in _packs(demo_db) if p.posting_id == pid)
    assert after.provider == "fake" and after.verification is not None
    assert runner.invoke(app, ["packs", "regenerate"]).exit_code == 1


def test_facts_check(demo_db: Path) -> None:
    result = runner.invoke(app, ["facts", "check"])
    assert result.exit_code == 0
    assert "source=facts.example.yaml" in result.output and "matches style.py: yes" in result.output
