"""Deterministic caps after the LLM (language, adjacent roles) and language re-scoring.

Fictional postings only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from llm_testkit import ScriptedProvider, make_posting
from sqlmodel import Session, select

from radar.llm import guards, scoring
from radar.llm.guards import FlagFact, apply_guards, required_languages
from radar.llm.relevance import Relevance
from radar.models import FilterResult, Posting, Score
from radar.pipeline.filters import apply_filters, compile_rules

REPO = Path(__file__).resolve().parents[2]

FR_TEXT = (
    "Santéo recrute un(e) stagiaire Consultant interopérabilité pour une durée de 6 mois, "
    "à partir de février 2027.\nVos missions : accompagner les établissements de santé dans le "
    "déploiement de nos connecteurs et analyser les flux de données avec l'équipe produit.\n"
    "Votre profil : vous êtes en école d'ingénieur ou de commerce et vous avez un fort intérêt "
    "pour la santé. Vous êtes rigoureux et vous aimez le contact client.\n"
    "Santéo is an equal opportunity employer and we do not discriminate."
)


def llm_out(priority: str = "Alta", langs: list[str] | None = None, **kw: Any) -> str:
    data: dict[str, Any] = {
        "priority": priority,
        "why_fits": "Il lavoro sui dati corrisponde al ruolo.",
        "what_doesnt_fit": "Nessun ostacolo evidente.",
        "start_window": "February 2027",
        "language_requirements": langs or [],
        "confidence": 0.8,
        "warnings": [],
    }
    data.update(kw)
    return json.dumps(data)


def guard(priority: str, title: str, langs: list[str] = (), flags: list[FlagFact] = ()):  # type: ignore[assignment]
    return apply_guards(
        priority, title=title, language_requirements=langs, flags=flags, relevance=Relevance()
    )


LANG_FLAG = FlagFact("posting_language", "lingua probabilmente richiesta: francese")


# ---------------------------------------------------------------- pure guards


def test_language_flag_caps_alta_to_media() -> None:
    g = guard("Alta", "Stage - Consultant interopérabilité", flags=[LANG_FLAG])
    assert g.priority == "Media" and g.applied == ["language_probable"]
    assert any("francese" in w and "Media" in w for w in g.warnings)
    assert "francese" in g.what_doesnt_fit


def test_required_language_caps_to_bassa() -> None:
    g = guard("Alta", "Data Science Intern", langs=["English", "French"], flags=[LANG_FLAG])
    assert g.priority == "Bassa" and g.applied == ["language_required"]
    assert any("Bassa" in w and "francese" in w for w in g.warnings)
    req = FlagFact("language_required", "German required", overridden=True)
    assert guard("Media", "Data Science Intern", flags=[req]).priority == "Bassa"


@pytest.mark.parametrize(
    "langs",
    [
        ["English C1"],
        ["Italian", "English"],
        ["German (plus)"],
        ["English or French"],
        ["Dutch is nice to have"],
        ["Spagnolo gradito"],
    ],
)
def test_allowed_or_optional_languages_not_capped(langs: list[str]) -> None:
    assert guard("Alta", "AI Engineer Intern", langs=langs).priority == "Alta"


def test_required_languages_parsing() -> None:
    assert required_languages(["Français courant", "Deutsch B2", "English"]) == [
        "francese",
        "tedesco",
    ]


def test_overridden_language_flag_not_capped() -> None:
    flag = FlagFact("posting_language", "lingua probabilmente richiesta: tedesco", True)
    assert guard("Alta", "AI Engineer Intern", flags=[flag]).priority == "Alta"


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer Intern",
        "Fullstack Developer (stage)",
        "QA Engineer Intern",
        "Sales Intern",
        "BI Reporting Intern",
        "Frontend Engineer",
    ],
)
def test_adjacent_roles_capped_at_media(title: str) -> None:
    g = guard("Alta", title)
    assert g.priority == "Media" and g.applied == ["role_cap"]
    assert guard("Bassa", title).priority == "Bassa"  # caps never raise


@pytest.mark.parametrize(
    "title",
    [
        "AI Engineer Intern",
        "Machine Learning Software Engineer",
        "Software Engineer, AI Platform",
        "Data Scientist Intern",
        "Forward Deployed Engineer",
        "Fintech Strategy Intern",
        "Venture Capital Analyst",
        "Business Analyst Intern",
        "Data & AI Consulting Intern",
    ],
)
def test_top_roles_stay_eligible_for_alta(title: str) -> None:
    assert guard("Alta", title).priority == "Alta"
    assert Relevance().cap(title) is None


def test_media_cap_patterns_overridable() -> None:
    r = Relevance({"scoring": {"media_cap_patterns": [r"\bproduct\b"]}})
    assert r.cap("Product Manager Intern") == "Media"
    assert r.cap("Software Engineer Intern") is None


# ---------------------------------------------------------------- scoring pipeline


@pytest.fixture()
def no_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scoring.radar_config, "load_yaml_config", lambda n: {})


def use(monkeypatch: pytest.MonkeyPatch, outputs: list[str | Exception]) -> ScriptedProvider:
    fake = ScriptedProvider(outputs)
    monkeypatch.setattr(scoring, "get_provider", lambda purpose: fake)
    return fake


def refilter(session: Session, posting: Posting) -> None:
    cfg = yaml.safe_load((REPO / "config" / "rules.example.yaml").read_text("utf-8"))
    apply_filters(session, posting, compile_rules(cfg))
    session.commit()


def only_score(session: Session, posting: Posting) -> Score:
    return session.exec(select(Score).where(Score.posting_id == posting.id)).one()


def test_french_posting_flagged_and_never_alta(
    session: Session, monkeypatch: pytest.MonkeyPatch, no_profile: None
) -> None:
    p = make_posting(session, title="Stage - Consultant interopérabilité", raw_text=FR_TEXT)
    p.location, p.country_code = "Paris", "FR"
    refilter(session, p)
    assert p.filter_state == "flagged"
    fake = use(monkeypatch, [llm_out("Alta")])
    stats = scoring.score_pending(session, run_id=None, limit=5)
    assert stats["scored"] == 1 and stats["capped"] == 1
    user = fake.prompts[0][1]
    flags_line = next(line for line in user.splitlines() if line.startswith("RADAR FLAGS: "))
    # language first, with the detected share; the other flags are passed too
    assert flags_line.startswith("RADAR FLAGS: lingua probabilmente richiesta: francese (testo:")
    assert "convention de stage" in flags_line
    s = only_score(session, p)
    assert s.priority == "Media"
    assert any("francese" in w for w in s.warnings or [])


def test_french_posting_with_llm_language_is_bassa(
    session: Session, monkeypatch: pytest.MonkeyPatch, no_profile: None
) -> None:
    p = make_posting(session, title="Stage - Consultant", raw_text=FR_TEXT)
    refilter(session, p)
    use(monkeypatch, [llm_out("Alta", ["French"])])
    scoring.score_pending(session, run_id=None, limit=5)
    assert only_score(session, p).priority == "Bassa"


def test_software_engineer_capped_ai_engineer_not(
    session: Session, monkeypatch: pytest.MonkeyPatch, no_profile: None
) -> None:
    swe = make_posting(session, title="Software Engineer Intern")
    ai = make_posting(session, title="AI Engineer Intern")
    use(monkeypatch, [llm_out("Alta"), llm_out("Alta")])
    scoring.score_pending(session, run_id=None, limit=5)
    assert only_score(session, swe).priority == "Media"
    assert any("software engineer" in w for w in only_score(session, swe).warnings or [])
    s = only_score(session, ai)
    assert s.priority == "Alta" and s.warnings == []


def test_english_and_italian_postings_not_flagged(session: Session) -> None:
    it = make_posting(
        session,
        title="Stage Data Science",
        raw_text=(
            "Cerchiamo uno stagista da inserire nel nostro team di Milano a partire da febbraio "
            "2027. Ti occuperai dello sviluppo di modelli di previsione della domanda insieme al "
            "team di ingegneria. Requisiti: laurea magistrale in corso, ottima conoscenza di "
            "Python e SQL, buona conoscenza dell'inglese."
        ),
    )
    en = make_posting(session, title="Data Science Intern")  # default English text
    for p in (it, en):
        refilter(session, p)
        rows = session.exec(select(FilterResult).where(FilterResult.posting_id == p.id)).all()
        assert not [r for r in rows if r.rule_id in scoring.LANGUAGE_RULE_IDS]


def test_overridden_reject_is_passed_as_fact(session: Session) -> None:
    p = make_posting(session, filter_state="overridden")
    session.add(FilterResult(posting_id=p.id, rule_id="location_uk_visa", action="reject",
                             reason="UK visa", overridden=True))  # fmt: skip
    for i in range(12):
        session.add(FilterResult(posting_id=p.id, rule_id=f"r{i}", action="flag", reason=f"f{i}"))
    session.add(FilterResult(posting_id=p.id, rule_id="posting_language", action="flag",
                             reason="lingua probabilmente richiesta: tedesco"))  # fmt: skip
    session.commit()
    facts = scoring._flags(session, p)
    assert facts[0].startswith("lingua probabilmente richiesta: tedesco")
    assert any(f.startswith("UK visa (reject overridden") for f in facts)
    assert len(facts) == scoring.MAX_FLAG_FACTS


# ---------------------------------------------------------------- re-scoring


def add_score(session: Session, p: Posting, priority: str, provider: str = "groq") -> None:
    assert p.id is not None
    session.add(Score(posting_id=p.id, content_hash=p.content_hash, priority=priority,
                      why_fits="x", what_doesnt_fit="", provider=provider, model="m"))  # fmt: skip
    session.commit()


def test_rescore_flagged_language(
    session: Session, monkeypatch: pytest.MonkeyPatch, no_profile: None
) -> None:
    fr = make_posting(session, title="Stage - Consultant", raw_text=FR_TEXT)
    refilter(session, fr)
    add_score(session, fr, "Alta")
    manual = make_posting(session, title="Stage - Analyste", raw_text=FR_TEXT, priority="Alta")
    refilter(session, manual)
    clean = make_posting(session, title="AI Engineer Intern")
    refilter(session, clean)
    add_score(session, clean, "Alta")

    dry = scoring.rescore_flagged_language(session, dry_run=True)
    assert dry == {"postings": 1, "stale_scores": 1, "by_priority": {"Alta": 1}}
    assert len(session.exec(select(Score)).all()) == 3  # nothing changed

    use(monkeypatch, [llm_out("Alta")])
    stats = scoring.rescore_flagged_language(session)
    assert stats["deleted"] == 1 and stats["scored"] == 1 and stats["capped"] == 1
    new = only_score(session, fr)
    assert new.priority == "Media" and new.guard_version == guards.GUARD_VERSION
    assert only_score(session, manual).provider == "manual"
    assert only_score(session, clean).priority == "Alta"
    assert scoring.rescore_flagged_language(session, dry_run=True)["postings"] == 0


def test_rescore_missing_key_deletes_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch, no_profile: None
) -> None:
    fr = make_posting(session, title="Stage - Consultant", raw_text=FR_TEXT)
    refilter(session, fr)
    add_score(session, fr, "Alta")

    def boom(purpose: str) -> None:
        raise scoring.MissingKeyError("missing")

    monkeypatch.setattr(scoring, "get_provider", boom)
    assert scoring.rescore_flagged_language(session)["skipped"] == "missing_key"
    assert only_score(session, fr).priority == "Alta"


def test_guard_ids_cover_filter_rule_ids() -> None:
    cfg = yaml.safe_load((REPO / "config" / "rules.example.yaml").read_text("utf-8"))
    assert cfg["posting_language"]["id"] in guards.LANGUAGE_PROBABLE_IDS
    assert cfg["languages"]["ids"]["required"] in guards.LANGUAGE_REQUIRED_IDS
