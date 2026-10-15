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
