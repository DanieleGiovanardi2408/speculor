from pathlib import Path

from sqlmodel import Session, select

from radar.models import Application, Company, Posting, Score
from radar.seed import import_seed

SEED = Path(__file__).parent / "fixtures" / "seed" / "mini_seed.json"


def test_import_seed_maps_fields_and_is_idempotent(session: Session) -> None:
    counts = import_seed(session, SEED)
    session.commit()
    assert counts == {"companies": 2, "postings": 2, "applications": 1}
    assert import_seed(session, SEED) == {"companies": 0, "postings": 0, "applications": 0}

    sent = session.exec(select(Posting).where(Posting.external_id == "nebbia-ai-intern")).one()
    assert sent.user_status == "sent"
    assert sent.country_code == "IT"
    assert sent.canonical_url == "https://jobs.example.com/nebbia/1"
    assert str(sent.deadline) == "2026-11-30"

    closed = session.exec(select(Posting).where(Posting.external_id == "corvo-ds-grad")).one()
    assert closed.status == "closed" and closed.closed_at is not None
    assert closed.deadline is None

    # the tracker's priority is only a note: the posting waits for the normal LLM scoring
    assert session.exec(select(Score).where(Score.posting_id == sent.id)).first() is None
    assert sent.note is not None and "[Tracker: Alta" in sent.note

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


def test_demote_manual_scores_keeps_a_note_and_is_idempotent(session: Session) -> None:
    from radar.seed import demote_manual_scores

    p = Posting(
        uid="seed:x",
        source="seed",
        external_id="x",
        url="https://a.example/1",
        canonical_url="https://a.example/1",
        title="AI Intern",
        company_name="Acme",
        content_hash="h",
        note="mine",
    )
    session.add(p)
    session.flush()
    session.add(
        Score(
            posting_id=p.id or 0,
            content_hash="h",
            priority="Alta",
            why_fits="fit",
            what_doesnt_fit="",
            provider="manual",
            model="manual",
        )
    )
    session.add(
        Score(
            posting_id=p.id or 0,
            content_hash="h",
            priority="Media",
            why_fits="llm",
            what_doesnt_fit="",
            provider="groq",
            model="m",
        )
    )
    session.flush()
    assert demote_manual_scores(session) == {"scores_removed": 1, "notes_added": 1}
    assert demote_manual_scores(session) == {"scores_removed": 0, "notes_added": 0}
    session.refresh(p)
    assert p.note == "mine\n[Tracker: Alta - fit]"
    assert [s.provider for s in session.exec(select(Score)).all()] == ["groq"]
