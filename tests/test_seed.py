from pathlib import Path

from sqlmodel import Session, select

from radar.models import Application, Company, Posting, Score
from radar.seed import import_seed

SEED = Path(__file__).parent / "fixtures" / "seed" / "mini_seed.json"


def test_import_seed_maps_fields_and_is_idempotent(session: Session) -> None:
    counts = import_seed(session, SEED)
    session.commit()
    assert counts == {"companies": 2, "postings": 2, "applications": 1, "scores": 2}
    assert import_seed(session, SEED) == {
        "companies": 0,
        "postings": 0,
        "applications": 0,
        "scores": 0,
    }

    sent = session.exec(select(Posting).where(Posting.external_id == "nebbia-ai-intern")).one()
    assert sent.user_status == "sent"
    assert sent.country_code == "IT"
    assert sent.canonical_url == "https://jobs.example.com/nebbia/1"
    assert str(sent.deadline) == "2026-11-30"

    closed = session.exec(select(Posting).where(Posting.external_id == "corvo-ds-grad")).one()
    assert closed.status == "closed" and closed.closed_at is not None
    assert closed.deadline is None

    score = session.exec(select(Score).where(Score.posting_id == sent.id)).one()
    assert score.priority == "Alta" and score.provider == "manual"

    app = session.exec(select(Application)).one()
    assert app.posting_id == sent.id

    corvo = session.exec(select(Company).where(Company.name == "Corvo Analytics")).one()
    assert corvo.careers_url is None and corvo.notes is not None


def test_sync_companies_config_stores_sector_and_ats(session: Session) -> None:
    from radar.seed import sync_companies_config

    session.add(Company(name="Nebula Analytics", sector="Data (edited in UI)"))
    session.commit()
    cfg = {
        "companies": [
            {"name": "Nebula Analytics", "ats": "greenhouse", "slug": "nebula", "sector": "ai"},
            {"name": "Fiordo Payments", "ats": "none", "slug": None, "sector": "fintech"},
            {"name": "Fiordo Payments", "ats": "personio", "slug": "fiordo", "region": "com"},
            {"name": "Orbis Consulting", "ats": "recruitee", "slug": "orbis", "active": False},
            {"ats": "lever", "slug": "nameless"},
            "not a dict",
        ]
    }
    assert sync_companies_config(session, cfg) == {"created": 2, "updated": 1}
    session.commit()
    rows = {c.name: c for c in session.exec(select(Company)).all()}
    nebula = rows["Nebula Analytics"]
    assert nebula.sector == "Data (edited in UI)"  # never overwritten
    assert (nebula.ats, nebula.ats_slug) == ("greenhouse", "nebula")
    fiordo = rows["Fiordo Payments"]
    assert fiordo.sector == "fintech"
    assert (fiordo.ats, fiordo.ats_slug, fiordo.ats_region) == ("personio", "fiordo", "com")
    assert rows["Orbis Consulting"].active is False
    assert sync_companies_config(session, cfg) == {"created": 0, "updated": 0}


def test_example_companies_config_sectors_load(session: Session) -> None:
    import yaml

    from radar.seed import sync_companies_config

    example = Path(__file__).parents[1] / "config" / "companies.example.yaml"
    cfg = yaml.safe_load(example.read_text(encoding="utf-8"))
    sync_companies_config(session, cfg)
    sectors = {c.name: c.sector for c in session.exec(select(Company)).all()}
    assert sectors["Fiordo Payments"] == "fintech"
