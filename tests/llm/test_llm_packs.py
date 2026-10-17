from __future__ import annotations

import json

import pytest
from llm_testkit import ScriptedProvider, make_posting
from sqlmodel import Session, select

from radar.llm import packs, style
from radar.llm import verify as verify_module
from radar.llm.provider import RateLimitExhausted
from radar.models import ApplicationPack, Contact, Draft, LLMUsage, Score

EM = "—"
VERIFY_EMPTY = '{"claims": []}'


def pack_json(**over: object) -> str:
    base: dict[str, object] = {
        "cover_letter": (
            "Available full-time from February 2027, start flexible until April 2027. "
            "I am applying for the Data Science Intern role."
            "\n\nYour team builds weekly demand forecasts in Python. In tinybench I designed the "
            "evaluation of LLM agents on spreadsheet tasks.\n\nBest regards,\nMara"
        ),
        "form_answers": [
            {"question": "Why this company?", "answer": "[CHECK: reason]"},
            {"question": "Why this role?", "answer": "Forecasting work matches my internship."},
            {"question": "Relevant project?", "answer": "tinybench."},
            {"question": "Availability?", "answer": "February-April 2027, six months."},
            {"question": "Work authorization?", "answer": "EU citizen."},
            {"question": "Salary expectations?", "answer": "30k EUR gross per year."},
        ],
        "linkedin_connection_note": "Hi [NOME], I saw the forecasting internship. Which "
        "model family does the team use today?",
        "linkedin_message": "Hi [NOME], I applied for the forecasting internship. I built a "
        "forecasting pipeline last summer. How do interns split time between modelling and "
        "stakeholder work?",
        "followup": "Hi [NOME], two weeks ago I applied for the Data Science Intern role. Is "
        "there an update on the timeline?",
    }
    base.update(over)
    return json.dumps(base)


def drafts(session: Session) -> list[Draft]:
    return list(session.exec(select(Draft)).all())


def test_fake_end_to_end(session: Session, fake_env: None) -> None:
    p = make_posting(session, priority="Alta")
    make_posting(session, title="Media one", priority="Media")
    make_posting(session, title="Already sent", priority="Alta", user_status="sent")
    make_posting(session, title="Rejected", priority="Alta", filter_state="rejected")

    stats = packs.build_packs(session, run_id=None, limit=5)
    assert stats["candidates"] == 1 and stats["built"] == 1

    (pack,) = session.exec(select(ApplicationPack)).all()
    assert pack.posting_id == p.id
    assert pack.language == "en"
    assert pack.cv_version == "CV v2"
    assert pack.apply_link == p.url
    assert pack.content_hash == p.content_hash
    assert pack.provider == "fake"
    first_line = pack.cover_letter.splitlines()[0]
    assert "full-time from February 2027, start flexible until April 2027" in first_line
    assert style.word_count(pack.cover_letter) <= 250
    assert EM not in pack.cover_letter
    assert "tinybench" in pack.cover_letter  # user project
    assert "forecasting team in Milan" in pack.cover_letter  # posting detail
    assert style.check(pack.cover_letter, "cover_letter", "en") == []
    assert pack.form_answers is not None and 4 <= len(pack.form_answers) <= 6
    salary = [a for a in pack.form_answers if "alary" in a["question"]]
    assert salary and salary[0]["answer"].startswith("[CHECK")

    ds = drafts(session)
    assert sorted(d.kind for d in ds) == sorted(packs.OUTREACH_KINDS)
    for d in ds:
        assert d.status == "draft" and d.channel == "linkedin" and d.recipient is None
        assert d.pack_id == pack.id
        assert "[NOME]" in d.text
        assert style.check(d.text, d.kind, "en") == []
    usage = session.exec(select(LLMUsage)).all()
    assert sorted((u.purpose, u.requests) for u in usage) == [("pack", 1), ("verify", 1)]
    assert stats["verify_requests"] == 1

    v = pack.verification
    assert v is not None and v["status"] == "ok" and v["provider"] == "fake"
    assert v["facts_source"] == "facts.example.yaml"
    assert v["counts"]["total"] == len(v["claims"]) > 0
    assert any(c["claim"].startswith("Available full-time") for c in v["drafter_claims"])
    by_text = {c["text_id"] for c in v["claims"]}
    assert "cover_letter" in by_text
    for d in ds:
        assert d.verification is not None
        assert all(c["text_id"] == d.kind for c in d.verification["claims"])

    # second run: nothing to do
    assert packs.build_packs(session, run_id=None, limit=5)["built"] == 0


def test_stale_pack_is_rebuilt_and_approved_drafts_kept(session: Session, fake_env: None) -> None:
    p = make_posting(session, priority="Alta")
    packs.build_packs(session, run_id=None, limit=5)
    note = next(d for d in drafts(session) if d.kind == "linkedin_connection_note")
    note.status = "approved"
    session.add(note)
    p.content_hash = "new-hash"
    session.add(p)
    session.add(
        Score(
            posting_id=p.id,
            content_hash="new-hash",
            priority="Alta",  # type: ignore[arg-type]
            why_fits="x",
            what_doesnt_fit="",
            provider="manual",
            model="manual",
        )
    )
    session.commit()

    stats = packs.build_packs(session, run_id=None, limit=5)
    assert stats["built"] == 1
    assert len(session.exec(select(ApplicationPack)).all()) == 1
    ds = drafts(session)
    assert len(ds) == 4  # 3 fresh + the approved one kept
    assert sum(d.status == "approved" for d in ds) == 1


def test_italian_posting(session: Session, fake_env: None) -> None:
    testo = (
        "Cerchiamo uno stagista per il team di data science della nostra sede di Milano. "
        "Il candidato lavorerà con il team su modelli di previsione della domanda e sarà "
        "coinvolto nella preparazione delle analisi per i clienti. È richiesta la laurea "
        "magistrale in corso, una buona conoscenza di Python e SQL e dell'inglese. "
        "Lo stage dura sei mesi con inizio a febbraio."
    )
    make_posting(session, title="Stage Data Science", raw_text=testo, priority="Alta")
    packs.build_packs(session, run_id=None, limit=5)
    (pack,) = session.exec(select(ApplicationPack)).all()
    assert pack.language == "it"
    assert pack.cover_letter.startswith(
        "Disponibile full-time da febbraio 2027, con inizio flessibile fino ad aprile 2027"
    )
    assert all(d.text.startswith("Ciao [NOME]") for d in drafts(session))


def test_one_regeneration_then_store_issues(
    session: Session, monkeypatch: pytest.MonkeyPatch, fake_env: None
) -> None:
    make_posting(session, priority="Alta")
    bad = pack_json(linkedin_message=f"Hi [NOME], I am passionate {EM} truly. What do you build?")
    fake = ScriptedProvider([bad, bad, VERIFY_EMPTY])
    monkeypatch.setattr(packs, "get_provider", lambda purpose: fake)
    monkeypatch.setattr(verify_module, "get_provider", lambda purpose: fake)
    stats = packs.build_packs(session, run_id=None, limit=5)
    assert stats["built"] == 1 and stats["requests"] == 2
    assert "passionate" in fake.prompts[1][1] and "PREVIOUS DRAFT" in fake.prompts[1][1]
    msg = next(d for d in drafts(session) if d.kind == "linkedin_message")
    assert EM not in msg.text
    assert msg.style_issues == ['cliche: "passionate"']


def test_regeneration_fixes(
    session: Session, monkeypatch: pytest.MonkeyPatch, fake_env: None
) -> None:
    make_posting(session, priority="Alta")
    bad = pack_json(cover_letter="To whom it may concern, I am a team player.")
    fake = ScriptedProvider([bad, pack_json(), VERIFY_EMPTY])
    monkeypatch.setattr(packs, "get_provider", lambda purpose: fake)
    monkeypatch.setattr(verify_module, "get_provider", lambda purpose: fake)
    packs.build_packs(session, run_id=None, limit=5)
    (pack,) = session.exec(select(ApplicationPack)).all()
    assert not [i for i in pack.style_issues or [] if i.startswith("cover_letter")]
    assert "team player" not in pack.cover_letter


def test_overlong_drafts_trimmed(
    session: Session, monkeypatch: pytest.MonkeyPatch, fake_env: None
) -> None:
    make_posting(session, priority="Alta")
    sentence = "I enjoyed reading about your forecasting work."
    long = "Hi [NOME], " + " ".join(sentence for _ in range(30))
    fake = ScriptedProvider([pack_json(linkedin_connection_note=long, followup=long), VERIFY_EMPTY])
    monkeypatch.setattr(packs, "get_provider", lambda purpose: fake)
    monkeypatch.setattr(verify_module, "get_provider", lambda purpose: fake)
    packs.build_packs(session, run_id=None, limit=5)
    by_kind = {d.kind: d for d in drafts(session)}
    assert len(by_kind["linkedin_connection_note"].text) <= 280
    assert len(by_kind["followup"].text) <= 600
    assert any("trimmed" in i for i in by_kind["followup"].style_issues or [])
    assert fake.outputs == []  # trimming fixed it: no regeneration needed


def test_salary_kept_when_posting_states_range(
    session: Session, monkeypatch: pytest.MonkeyPatch, fake_env: None
) -> None:
    make_posting(
        session,
        priority="Alta",
        raw_text="Internship in Milan. Stipend: EUR 1,200 per month. Python required.",
    )
    fake = ScriptedProvider([pack_json(), VERIFY_EMPTY])
    monkeypatch.setattr(packs, "get_provider", lambda purpose: fake)
    monkeypatch.setattr(verify_module, "get_provider", lambda purpose: fake)
    packs.build_packs(session, run_id=None, limit=5)
    (pack,) = session.exec(select(ApplicationPack)).all()
    assert pack.form_answers is not None
    assert pack.form_answers[-1]["answer"] == "30k EUR gross per year."
    # 30 is neither in the facts nor in the posting: the pre-check flags it.
    assert pack.verification is not None
    flagged = [c for c in pack.verification["claims"] if c["text_id"] == "form_answers.6"]
    assert flagged and flagged[0]["verdict"] == "unsupported" and "30" in flagged[0]["reason"]


def test_invalid_json_repair_then_skip(
    session: Session, monkeypatch: pytest.MonkeyPatch, fake_env: None
) -> None:
    make_posting(session, priority="Alta")
    fake = ScriptedProvider(["not json", '{"cover_letter": "short"}'])
    monkeypatch.setattr(packs, "get_provider", lambda purpose: fake)
    monkeypatch.setattr(verify_module, "get_provider", lambda purpose: fake)
    stats = packs.build_packs(session, run_id=None, limit=5)
    assert stats["invalid"] == 1 and stats["built"] == 0
    assert session.exec(select(ApplicationPack)).all() == []


def test_rate_limit_stops(
    session: Session, monkeypatch: pytest.MonkeyPatch, fake_env: None
) -> None:
    make_posting(session, priority="Alta")
    make_posting(session, title="Second", priority="Alta")
    fake = ScriptedProvider([pack_json(), RateLimitExhausted("daily")])
    monkeypatch.setattr(packs, "get_provider", lambda purpose: fake)
    monkeypatch.setattr(verify_module, "get_provider", lambda purpose: fake)
    stats = packs.build_packs(session, run_id=None, limit=5)
    assert stats["stopped"] == "rate_limit" and stats["built"] == 1
    assert len(packs.pack_candidates(session, 5)) == 1  # resumes next run


def test_explicit_posting_id(session: Session, fake_env: None) -> None:
    p = make_posting(session, priority="Bassa")
    stats = packs.build_packs(session, run_id=None, limit=1, posting_id=p.id)
    assert stats["built"] == 1 and stats["pack_id"]
    assert packs.build_packs(session, run_id=None, limit=1, posting_id=9999)["error"]


def test_draft_for_contact(session: Session, fake_env: None) -> None:
    p = make_posting(session, priority="Alta")
    c = Contact(
        posting_id=p.id,
        name="Giulia Esempio",
        role="Data Lead",
        profile_url="https://www.linkedin.com/in/giulia-esempio",
        why="Alum",
    )
    session.add(c)
    session.commit()
    assert p.id is not None and c.id is not None
    d = packs.draft_for_contact(session, p.id, c.id, "linkedin_connection_note")
    assert d.id is not None
    assert d.contact_id == c.id and d.kind == "linkedin_connection_note"
    assert d.recipient == c.profile_url and d.status == "draft"
    assert d.text.startswith("Hi Giulia")
    assert len(d.text) <= 280 and EM not in d.text
    with pytest.raises(ValueError):
        packs.draft_for_contact(session, p.id, c.id, "carrier_pigeon")
