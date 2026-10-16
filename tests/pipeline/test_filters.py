from __future__ import annotations

import pytest
from pipeline_fakes import TODAY

from radar.pipeline.filters import Hit, PostingText, Rules, evaluate, state_for

GOOD_START = "Start date: February 2027."


def hits_for(rules: Rules, **kw: str | None) -> list[Hit]:
    data: dict[str, str | None] = {"title": "Data Science Intern", "location": "Milan, Italy"}
    data.update(kw)
    title = data.pop("title") or ""
    return evaluate(PostingText(title=title, **data), rules, TODAY)  # type: ignore[arg-type]


def ids(hits: list[Hit]) -> set[str]:
    return {h.rule_id for h in hits}


def rejects(hits: list[Hit]) -> set[str]:
    return {h.rule_id for h in hits if h.action == "reject"}


# ---------------------------------------------------------------- start date


@pytest.mark.parametrize(
    "text",
    [
        "Starting in June 2027 for 3 months.",
        "Inizio: giugno 2027, durata 3 mesi.",
        "We are looking for interns starting summer 2027.",
        "Join our Summer Internship 2027 programme.",
    ],
)
def test_start_summer_2027_rejected(rules: Rules, text: str) -> None:
    hits = hits_for(rules, raw_text=text)
    assert "start_summer_only" in rejects(hits)
    hit = next(h for h in hits if h.rule_id == "start_summer_only")
    assert hit.evidence and len(hit.evidence) <= 200


def test_summer_in_title_rejected(rules: Rules) -> None:
    hits = hits_for(rules, title="Summer Analyst 2027 - Data", raw_text="Great team.")
    assert "start_summer_only" in rejects(hits)


@pytest.mark.parametrize(
    "text",
    [
        "Start date: September 2026.",
        "Start date: October 2026.",
        "Inizio novembre 2026, 6 mesi.",
        "Début : décembre 2026.",
    ],
)
def test_start_before_2027_rejected(rules: Rules, text: str) -> None:
    assert "start_before_window" in rejects(hits_for(rules, raw_text=text))


@pytest.mark.parametrize(
    "text",
    [
        "Start date: February 2027.",
        "Starting in January 2027.",
        "Inizio febbraio 2027.",
        "Inizio 1/3/2027, sei mesi.",
        "Start date: flexible.",
        "We hire on a rolling basis, ASAP start.",
        "Inizio flessibile.",
        "Summer 2027 or off-cycle internships starting February 2027 available.",
        "Students graduating in summer 2027. Start: March 2027.",
    ],
)
def test_start_ok(rules: Rules, text: str) -> None:
    hits = hits_for(rules, raw_text=text)
    assert not {i for i in ids(hits) if i.startswith("start_")}


def test_title_period_ok(rules: Rules) -> None:
    hits = hits_for(rules, title="Investment Intern (H1 2027)", raw_text="Great team.")
    assert not {i for i in ids(hits) if i.startswith("start_")}


def test_start_not_stated_flag(rules: Rules) -> None:
    hits = hits_for(rules, raw_text="Python, SQL, curiosity.")
    hit = next(h for h in hits if h.rule_id == "start_not_stated")
    assert hit.action == "flag"
    assert state_for(hits) == "flagged"


def test_start_late_flag(rules: Rules) -> None:
    hits = hits_for(rules, raw_text="Start date: October 2027.")
    assert any(h.rule_id == "start_after_window" and h.action == "flag" for h in hits)


def test_publication_date_not_a_start(rules: Rules) -> None:
    hits = hits_for(rules, raw_text="Annuncio aperto da giugno. Pubblicato 15/9/2026")
    assert "start_summer_only" not in ids(hits)
    assert "start_not_stated" in ids(hits)


# ---------------------------------------------------------------- graduation


@pytest.mark.parametrize(
    "text",
    [
        "Open to students graduating in 2028.",
        "Class of 2029 only.",
        "Expected graduation: 2028.",
        "Studenti con laurea prevista nel 2028.",
        "This programme is for penultimate year students.",
        "Open to pre-final year students.",
        "Studenti al penultimo anno di magistrale.",
        "You must return to university after the internship.",
        "You must have at least one semester remaining after the internship.",
    ],
)
def test_graduation_rejected(rules: Rules, text: str) -> None:
    got = rejects(hits_for(rules, raw_text=f"{GOOD_START} {text}"))
    assert got & {"graduation_year", "penultimate_year", "return_to_studies"}


@pytest.mark.parametrize(
    "text",
    [
        "Open to students graduating in 2027.",
        "Graduating in 2027 or 2028.",
        "Open to penultimate or final year students.",
        "Class of 2027.",
    ],
)
def test_graduation_ok(rules: Rules, text: str) -> None:
    got = rejects(hits_for(rules, raw_text=f"{GOOD_START} {text}"))
    assert not got & {"graduation_year", "penultimate_year", "return_to_studies"}


# ---------------------------------------------------------------- PhD


@pytest.mark.parametrize(
    ("title", "text"),
    [
        ("Research Intern", "You are currently enrolled in a PhD program in CS."),
        ("Research Intern", "PhD students only."),
        ("PhD Intern, Machine Learning", "Work on LLMs."),
        ("Research Intern", "A PhD is required."),
    ],
)
def test_phd_only_rejected(rules: Rules, title: str, text: str) -> None:
    assert "phd_only" in rejects(hits_for(rules, title=title, raw_text=f"{GOOD_START} {text}"))


@pytest.mark.parametrize(
    "text",
    [
        "Currently pursuing an MSc or PhD in Computer Science.",
        "Currently enrolled in a PhD or Master's program.",
        "MSc/PhD students in a quantitative field.",
    ],
)
def test_msc_or_phd_ok(rules: Rules, text: str) -> None:
    assert "phd_only" not in ids(hits_for(rules, raw_text=f"{GOOD_START} {text}"))


# ---------------------------------------------------------------- experience


@pytest.mark.parametrize(
    "text",
    [
        "3+ years of experience in data science.",
        "At least 3 years of professional experience.",
        "A minimum of 5 years in a similar role.",
        "Almeno 3 anni di esperienza nel ruolo.",
        "Hai 4 anni di esperienza in Python.",
    ],
)
def test_experience_rejected(rules: Rules, text: str) -> None:
    assert "experience_too_senior" in rejects(hits_for(rules, raw_text=f"{GOOD_START} {text}"))


@pytest.mark.parametrize(
    "text",
    ["1-2 years of experience with Python.", "Chiede 2+ anni in ML.", "3+ years preferred."],
)
def test_experience_stretch_flag(rules: Rules, text: str) -> None:
    hits = hits_for(rules, raw_text=f"{GOOD_START} {text}")
    assert any(h.rule_id == "experience_stretch" and h.action == "flag" for h in hits)
    assert "experience_too_senior" not in ids(hits)


@pytest.mark.parametrize(
    "text",
    [
        "Up to 3 years of experience.",
        "Fino a 3 anni di esperienza, quindi entry level ok.",
        "No experience required.",
        "Programma di 3 anni con rotazioni.",
        "Internship of 3-6 months.",
    ],
)
def test_experience_ok(rules: Rules, text: str) -> None:
    got = ids(hits_for(rules, raw_text=f"{GOOD_START} {text}"))
    assert not got & {"experience_too_senior", "experience_stretch"}


# ---------------------------------------------------------------- location


@pytest.mark.parametrize(
    ("location", "cc"),
    [
        ("Milan, Italy", "IT"),
        ("Berlin", "DE"),
        ("Zurich", "CH"),
        ("Remote", None),
        ("London, Paris", "GB"),
        (None, "NL"),
    ],
)
def test_location_ok(rules: Rules, location: str | None, cc: str | None) -> None:
    got = ids(hits_for(rules, location=location, country_code=cc, raw_text=GOOD_START))
    assert not {i for i in got if i.startswith("location_")}


def test_uk_rejected_without_sponsorship(rules: Rules) -> None:
    hits = hits_for(rules, location="London, UK", raw_text=GOOD_START)
    hit = next(h for h in hits if h.rule_id == "location_uk_visa")
    assert hit.action == "reject" and "UK visa" in hit.reason


def test_uk_negated_sponsorship_rejected(rules: Rules) -> None:
    text = f"{GOOD_START} Unfortunately we do not offer visa sponsorship."
    assert "location_uk_visa" in rejects(hits_for(rules, location="London", raw_text=text))


def test_uk_ok_with_sponsorship(rules: Rules) -> None:
    text = f"{GOOD_START} Visa sponsorship is available for this role."
    assert "location_uk_visa" not in ids(hits_for(rules, location="London", raw_text=text))


@pytest.mark.parametrize("location", ["New York, United States", "Remote - US", "Singapore"])
def test_outside_europe_rejected(rules: Rules, location: str) -> None:
    hits = hits_for(rules, location=location, raw_text=GOOD_START)
    assert "location_outside_europe" in rejects(hits)


def test_location_unknown_flag(rules: Rules) -> None:
    hits = hits_for(rules, location=None, country_code=None, raw_text=GOOD_START)
    assert any(h.rule_id == "location_unknown" and h.action == "flag" for h in hits)


# ---------------------------------------------------------------- languages


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("Fluent French is required.", "French"),
        ("Business fluent German.", "German"),
        ("Français courant exigé.", "French"),
        ("Verhandlungssichere Deutschkenntnisse.", "German"),
        ("Spanish (C1) required for client work.", "Spanish"),
        ("Excellent command of English and Dutch.", "Dutch"),
        ("Tedesco richiesto.", "German"),
        ("Ottima conoscenza dello spagnolo.", "Spanish"),
        ("You must speak Portuguese with clients.", "Portuguese"),
    ],
)
def test_language_required_rejected(rules: Rules, text: str, lang: str) -> None:
    hits = hits_for(rules, raw_text=f"{GOOD_START} {text}")
    hit = next(h for h in hits if h.rule_id == "language_required")
    assert hit.action == "reject" and lang in hit.reason


@pytest.mark.parametrize(
    "text",
    [
        "German is a plus.",
        "Fluent English required; Dutch is nice to have.",
        "French would be an advantage.",
        "Il tedesco è gradito.",
        "Probabilmente richiesto il tedesco.",
    ],
)
def test_language_plus_not_rejected(rules: Rules, text: str) -> None:
    hits = hits_for(rules, raw_text=f"{GOOD_START} {text}")
    assert "language_required" not in ids(hits)


def test_language_plus_flagged(rules: Rules) -> None:
    hits = hits_for(rules, raw_text=f"{GOOD_START} Fluent German is a plus.")
    assert any(h.rule_id == "language_nice_to_have" and h.action == "flag" for h in hits)


@pytest.mark.parametrize(
    "text",
    [
        "Fluent English required.",
        "Italiano e inglese C1.",
        "Fluent in English or French.",
        "Experience with German clients.",
    ],
)
def test_allowed_languages_ok(rules: Rules, text: str) -> None:
    assert "language_required" not in ids(hits_for(rules, raw_text=f"{GOOD_START} {text}"))


FRENCH = (
    "Nous recherchons un stagiaire pour rejoindre notre équipe data. Vous travaillerez avec "
    "les équipes produit et les ingénieurs sur des projets de machine learning. Le stage "
    "est basé à Paris et dure six mois. Vous êtes curieux, autonome et vous aimez les "
    "données. Nous offrons une ambiance de travail stimulante avec des projets concrets "
    "pour nos clients et une équipe qui partage ses connaissances. Début : février 2027."
)


def test_posting_in_french_flagged(rules: Rules) -> None:
    hits = hits_for(rules, raw_text=FRENCH, location="Paris")
    hit = next(h for h in hits if h.rule_id == "posting_language")
    assert hit.action == "flag" and hit.reason == "lingua probabilmente richiesta: francese"
    assert hit.evidence and "francese 100%" in hit.evidence
    assert "language_required" not in ids(hits)


def test_posting_in_english_not_flagged(rules: Rules) -> None:
    text = (
        "We are looking for an intern to join our data team. You will work with the product "
        "team and engineers on machine learning projects that matter for our customers. "
        "The internship is based in Milan and lasts six months, and you will learn a lot "
        "from a team that shares knowledge. Start date: February 2027."
    )
    assert "posting_language" not in ids(hits_for(rules, raw_text=text))


# ---------------------------------------------------------------- enrollment agreement


@pytest.mark.parametrize("location", ["Paris, France", "Madrid"])
def test_fr_es_internship_flagged(rules: Rules, location: str) -> None:
    hits = hits_for(rules, location=location, raw_text=GOOD_START)
    hit = next(h for h in hits if h.rule_id == "enrollment_agreement")
    assert hit.action == "flag" and "convention de stage" in hit.reason


def test_fr_job_not_internship_not_flagged(rules: Rules) -> None:
    hits = hits_for(rules, title="Data Scientist", location="Paris", raw_text=GOOD_START)
    assert "enrollment_agreement" not in ids(hits)


def test_italian_internship_passes(rules: Rules) -> None:
    hits = hits_for(rules, raw_text="Inizio febbraio 2027, Python e SQL, 6 mesi a Milano.")
    assert hits == []
    assert state_for(hits) == "pass"


def test_evaluate_accepts_mapping() -> None:
    cfg = {
        "regex_rules": [
            {"id": "x", "action": "reject", "reason": "x", "patterns": ["foo"], "unless": ["bar"]}
        ]
    }
    assert [h.rule_id for h in evaluate(PostingText(title="foo"), cfg)] == ["x"]
    assert evaluate(PostingText(title="foo bar"), cfg) == []
