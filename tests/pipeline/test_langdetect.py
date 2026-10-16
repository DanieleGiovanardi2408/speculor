"""Local language detection and the `posting_language` flag (fictional postings only)."""

from __future__ import annotations

import pytest
from pipeline_fakes import TODAY

from radar.pipeline import langdetect
from radar.pipeline.filters import Hit, PostingText, Rules, compile_rules, evaluate

# Health-tech style French internship with the job board's English EEO paragraph appended.
FR_CONSULTANT = """<p>Santéo recrute un(e) stagiaire Consultant interopérabilité pour une durée
de 6 mois, à partir de février 2027.</p>
<h3>Vos missions</h3><ul>
<li>Accompagner les établissements de santé dans le déploiement de nos connecteurs</li>
<li>Analyser les flux de données et proposer des améliorations à l&#39;équipe produit</li>
<li>Rédiger la documentation technique pour nos clients</li></ul>
<h3>Votre profil</h3><p>Vous êtes en école d'ingénieur ou de commerce et vous avez un fort
intérêt pour la santé. Vous êtes rigoureux et vous aimez le contact client.</p>
<p>Santéo is an equal opportunity employer. We are committed to providing equal employment
opportunities to all employees and applicants and we do not discriminate.</p>"""

EN_FROM_FR_COMPANY = """About us: Exemplia SAS is a Paris-based fintech. Bienvenue chez nous!
We are looking for a Data Science Intern to join our team in Paris from February 2027. You will
work with our engineers on machine learning models for credit risk and present the results to
the product team. Requirements: you are enrolled in a master's degree in data science or a
related field and you have strong Python and SQL skills.
Our office: 12 rue de la Paix, 75002 Paris, France."""

IT_POSTING = """Cerchiamo uno stagista Data Science da inserire nel nostro team di Milano a
partire da febbraio 2027. Ti occuperai dello sviluppo di modelli di previsione della domanda e
della loro messa in produzione insieme al team di ingegneria. Requisiti: laurea magistrale in
corso, ottima conoscenza di Python e SQL, buona conoscenza dell'inglese. Offriamo un rimborso
spese e la possibilità di lavorare da remoto due giorni alla settimana."""

DE_POSTING = """Wir suchen ab Februar 2027 einen Praktikanten (m/w/d) im Bereich Data Science.
Deine Aufgaben: Du unterstützt unser Team bei der Entwicklung von Machine-Learning-Modellen
und bei der Auswertung von Kundendaten. Dein Profil: Du studierst Informatik oder Statistik und
hast erste Erfahrung mit Python. Wir bieten dir ein spannendes Umfeld und flexible Arbeitszeiten
in unserem Büro in Berlin."""

ES_POSTING = """Buscamos un becario para nuestro equipo de datos en Madrid a partir de febrero
de 2027. Trabajarás con el equipo de producto en proyectos de aprendizaje automático y
analítica para nuestros clientes. Requisitos: estudiante de máster, conocimientos de Python y
SQL, ganas de aprender y capacidad de trabajo en equipo. Ofrecemos un ambiente joven y la
posibilidad de incorporación."""

NL_POSTING = """Wij zoeken een stagiair voor ons data team in Amsterdam vanaf februari 2027. Je
werkt samen met onze engineers aan machine learning modellen voor onze klanten. Wat breng je
mee: je bent student in een master, je hebt ervaring met Python en je bent nieuwsgierig. Wat
bieden wij: een stagevergoeding, een eigen laptop en een team dat graag kennis deelt."""


def hits(rules: Rules, title: str, text: str, location: str = "Paris, France") -> list[Hit]:
    return evaluate(PostingText(title=title, raw_text=text, location=location), rules, TODAY)


def lang_hit(found: list[Hit]) -> Hit | None:
    return next((h for h in found if h.rule_id == "posting_language"), None)


# ---------------------------------------------------------------- detector


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        (FR_CONSULTANT, "fr"),
        (EN_FROM_FR_COMPANY, "en"),
        (IT_POSTING, "it"),
        (DE_POSTING, "de"),
        (ES_POSTING, "es"),
        (NL_POSTING, "nl"),
    ],
)
def test_detect_top_language(text: str, lang: str) -> None:
    det = langdetect.detect(text)
    assert det.lang == lang
    assert det.words >= 30


def test_mixed_text_reports_shares() -> None:
    det = langdetect.detect(FR_CONSULTANT)
    assert 0.5 < det.shares["fr"] < 1.0
    assert det.shares.get("en", 0) > 0  # the boilerplate is seen, not ignored


def test_html_and_urls_are_stripped() -> None:
    assert "<li>" not in langdetect.clean("<li>a</li> https://example.com/x b@example.com")
    assert langdetect.clean("l&#39;équipe") == "l'équipe"


def test_empty_or_short_text() -> None:
    assert langdetect.detect("").lang is None
    assert langdetect.detect("Python, SQL").lang is None


# ---------------------------------------------------------------- posting_language flag


def test_french_consultant_flagged(rules: Rules) -> None:
    found = hits(rules, "Stage - Consultant interopérabilité", FR_CONSULTANT)
    hit = lang_hit(found)
    assert hit is not None and hit.action == "flag"
    assert hit.reason == "lingua probabilmente richiesta: francese"
    assert hit.evidence and hit.evidence.startswith("testo: francese ")
    assert "inglese" in hit.evidence


def test_english_title_french_body_flagged(rules: Rules) -> None:
    assert lang_hit(hits(rules, "Data Science Intern", FR_CONSULTANT)) is not None


@pytest.mark.parametrize(
    ("text", "label"),
    [(DE_POSTING, "tedesco"), (ES_POSTING, "spagnolo"), (NL_POSTING, "olandese")],
)
def test_other_languages_flagged(rules: Rules, text: str, label: str) -> None:
    hit = lang_hit(hits(rules, "Data Science Intern", text))
    assert hit is not None and hit.reason.endswith(label)


def test_english_posting_from_french_company_not_flagged(rules: Rules) -> None:
    assert lang_hit(hits(rules, "Data Science Intern", EN_FROM_FR_COMPANY)) is None


def test_italian_posting_not_flagged(rules: Rules) -> None:
    found = hits(rules, "Stage Data Science", IT_POSTING, location="Milano")
    assert lang_hit(found) is None
    assert not any(h.rule_id.startswith("language") for h in found)


def test_short_text_not_flagged(rules: Rules) -> None:
    assert lang_hit(hits(rules, "Stage - Consultant", "Stage de 6 mois à Paris.")) is None


def test_explicit_requirement_is_one_finding(rules: Rules) -> None:
    found = hits(rules, "Stage", FR_CONSULTANT + " Maîtrise du français indispensable.")
    assert any(h.rule_id == "language_required" and "French" in h.reason for h in found)
    assert lang_hit(found) is None


def test_old_config_keys_still_work() -> None:
    cfg = {"posting_language": {"flag_languages": ["French"], "min_tokens": 30}}
    hit = lang_hit(
        evaluate(PostingText(title="Stage", raw_text=FR_CONSULTANT), compile_rules(cfg), TODAY)
    )
    assert hit is not None and "francese" in hit.reason


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("French required.", "French"),
        ("Maîtrise du français indispensable.", "French"),
        ("Vous êtes francophone.", "French"),
        ("Fließend Deutsch.", "German"),
        ("Sehr gute Deutschkenntnisse.", "German"),
        ("Nivel nativo de español.", "Spanish"),
        ("Nederlands vereist.", "Dutch"),
        ("Vloeiend Nederlands en Engels.", "Dutch"),
    ],
)
def test_explicit_requirements_any_language(rules: Rules, text: str, lang: str) -> None:
    found = hits(rules, "Data Intern", f"Start date: February 2027. {text}", "Milan, Italy")
    assert any(h.rule_id == "language_required" and lang in h.reason for h in found)


@pytest.mark.parametrize(
    "text", ["Perfect English.", "Fluent English, Italian is a plus.", "Deutsch ist ein Plus."]
)
def test_allowed_or_soft_not_required(rules: Rules, text: str) -> None:
    found = hits(rules, "Data Intern", f"Start date: February 2027. {text}", "Milan, Italy")
    assert not any(h.rule_id == "language_required" for h in found)


def test_fully_bilingual_posting_not_flagged(rules: Rules) -> None:
    bilingual = FR_CONSULTANT.split("<p>Santéo is")[0] + "\n" + EN_FROM_FR_COMPANY
    det = langdetect.detect(bilingual)
    assert det.lang == "fr" and det.share < 0.6
    assert lang_hit(hits(rules, "Data Science Intern", bilingual)) is None
