"""Compact prompts for scoring and application packs, plus the offline FakeProvider generator.

Prompt layout (stable, also parsed by ``fake_completion``):

    [radar-task: <task>]          first line of every system prompt
    CANDIDATE ... / POSTING ...   sections of the user prompt, ``Key: value`` lines
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from typing import Any

from radar.llm.style import AVAILABILITY_SENTENCE
from radar.models import Company, Contact, Posting
from radar.timeutil import today

TASK_SCORE = "score"
TASK_PACK = "pack"
TASK_CONTACT = "contact-draft"

SCORING_PROFILE_CHARS = 2000  # ~500 tokens
DRAFT_PROFILE_CHARS = 5000
SCORING_POSTING_CHARS = 6000
DRAFT_POSTING_CHARS = 10000
CONTACT_POSTING_CHARS = 4000

LANGUAGE_NAMES = {"it": "Italian", "en": "English"}

# --------------------------------------------------------------------------- profile


def _s(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _join(items: Iterable[str], sep: str = "; ") -> str:
    return sep.join(x for x in items if x)


def _languages(profile: dict[str, Any]) -> str:
    out = []
    for lang in profile.get("languages") or []:
        if isinstance(lang, dict):
            out.append(f"{_s(lang.get('language'))} {_s(lang.get('level'))}".strip())
        else:
            out.append(_s(lang))
    return _join(out, ", ")


def _skills(profile: dict[str, Any]) -> str:
    skills = profile.get("skills") or {}
    if isinstance(skills, dict):
        return _join((f"{k}: {_s(v)}" for k, v in skills.items()), "\n")
    return _s(skills)


def condense_profile(profile: dict[str, Any], max_chars: int = SCORING_PROFILE_CHARS) -> str:
    """Short profile for scoring (~500 tokens): no names, links or long facts."""
    exp = []
    for e in profile.get("experience") or []:
        if isinstance(e, dict):
            exp.append(f"{_s(e.get('role'))} at {_s(e.get('org'))} ({_s(e.get('period'))})")
    projects = []
    for p in profile.get("projects") or []:
        if isinstance(p, dict):
            projects.append(f"{_s(p.get('name'))}: {_s(p.get('what'))[:90]}")
    lines = [
        f"Headline: {_s(profile.get('headline'))}",
        f"Citizenship: {_s(profile.get('citizenship'))}",
        f"Based in: {_s(profile.get('based_in'))}",
        f"Availability: {_s(profile.get('availability'))}",
        f"Graduation: {_s(profile.get('graduation'))}",
        f"Languages: {_languages(profile)}",
        f"Target roles: {_join((_s(r) for r in profile.get('target_roles') or []), ', ')}",
        f"Experience: {_join(exp)}",
        f"Skills:\n{_skills(profile)}",
        "Projects:\n" + "\n".join(f"- {p}" for p in projects[:6]),
    ]
    return "\n".join(lines)[:max_chars]


def full_profile(profile: dict[str, Any], max_chars: int = DRAFT_PROFILE_CHARS) -> str:
    """Richer profile for drafting: experience facts and project descriptions."""
    lines = [
        f"Name: {_s(profile.get('name'))}",
        f"Headline: {_s(profile.get('headline'))}",
        f"Citizenship: {_s(profile.get('citizenship'))}",
        f"Based in: {_s(profile.get('based_in'))}",
        f"Availability: {_s(profile.get('availability'))}",
        f"Graduation: {_s(profile.get('graduation'))}",
        f"Languages: {_languages(profile)}",
        f"Target roles: {_join((_s(r) for r in profile.get('target_roles') or []), ', ')}",
        "Education:",
        *(f"- {_s(e)}" for e in profile.get("education") or []),
        "Experience:",
    ]
    for e in profile.get("experience") or []:
        if not isinstance(e, dict):
            continue
        lines.append(f"- {_s(e.get('role'))} at {_s(e.get('org'))} ({_s(e.get('period'))})")
        lines.extend(f"  - {_s(f)}" for f in e.get("facts") or [])
    lines.append("Projects:")
    for p in profile.get("projects") or []:
        if isinstance(p, dict):
            lines.append(f"- {_s(p.get('name'))}: {_s(p.get('what'))}")
    lines.append(f"Skills:\n{_skills(profile)}")
    if profile.get("other"):
        lines.append(f"Other: {_s(profile.get('other'))}")
    return "\n".join(lines)[:max_chars]


def default_cv_version(profile: dict[str, Any]) -> str:
    versions = [v for v in profile.get("cv_versions") or [] if isinstance(v, dict)]
    for v in versions:
        if v.get("default"):
            return _s(v.get("name")) or "CV v1"
    return _s(versions[0].get("name")) if versions else "CV v1"


# --------------------------------------------------------------------------- language

_STOPWORDS: dict[str, frozenset[str]] = {
    "it": frozenset(
        [
            "il",
            "lo",
            "la",
            "gli",
            "le",
            "di",
            "che",
            "per",
            "con",
            "della",
            "delle",
            "degli",
            "nel",
            "nella",
            "sono",
            "una",
            "è",
            "un",
            "del",
            "al",
            "dei",
            "ai",
            "e",
            "anche",
            "come",
            "più",
            "nostro",
            "nostra",
            "tuo",
            "tua",
        ]
    ),
    "en": frozenset(
        [
            "the",
            "and",
            "to",
            "of",
            "with",
            "you",
            "our",
            "for",
            "we",
            "will",
            "is",
            "are",
            "in",
            "on",
            "your",
            "this",
            "as",
            "be",
            "an",
            "at",
        ]
    ),
    "de": frozenset(
        [
            "und",
            "der",
            "die",
            "das",
            "mit",
            "für",
            "wir",
            "sie",
            "ist",
            "nicht",
            "ein",
            "eine",
            "zu",
            "bei",
            "auf",
        ]
    ),
    "fr": frozenset(
        [
            "et",
            "le",
            "les",
            "des",
            "vous",
            "nous",
            "pour",
            "avec",
            "une",
            "est",
            "dans",
            "sur",
            "du",
            "au",
        ]
    ),
    "es": frozenset(
        ["y", "el", "los", "las", "para", "con", "una", "es", "en", "del", "por", "su", "nuestro"]
    ),
    "nl": frozenset(
        ["en", "het", "van", "een", "voor", "wij", "je", "jij", "is", "op", "met", "bij", "ons"]
    ),
}
_IT_TITLE_WORDS = frozenset(
    [
        "stage",
        "tirocinio",
        "stagista",
        "tirocinante",
        "tirocini",
        "neolaureato",
        "neolaureati",
        "neolaureata",
        "laureando",
        "laureandi",
        "analista",
        "ingegnere",
        "sviluppatore",
        "consulente",
    ]
)


def detect_language(text: str) -> str | None:
    """'it' | 'en' | 'de' | ... by stopword counts; None when unsure."""
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    if len(words) < 20:
        return None
    counts = {lang: sum(1 for w in words if w in sw) for lang, sw in _STOPWORDS.items()}
    lang, best = max(counts.items(), key=lambda kv: kv[1])
    return lang if best >= 5 else None


def posting_language(posting: Posting) -> str:
    """Language for drafts: the posting language if Italian or English, else English.

    Seed postings carry the user's own notes as raw_text, so only the title counts there.
    """
    text = "" if posting.source == "seed" else (posting.raw_text or "")
    lang = detect_language(text) if text else None
    if lang in ("it", "en"):
        return lang
    if lang is not None:
        return "en"
    title_words = set(re.findall(r"[a-zà-ÿ]+", posting.title.lower()))
    return "it" if title_words & _IT_TITLE_WORDS else "en"


# --------------------------------------------------------------------------- posting block


def posting_block(
    posting: Posting,
    company: Company | None,
    max_chars: int,
    flags: Sequence[str] | None = None,
) -> str:
    text = (posting.raw_text or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + " [...]"
    source_note = (
        " (candidate's own notes, not the employer's text)" if posting.source == "seed" else ""
    )
    cc = f"({posting.country_code})" if posting.country_code else ""
    lines = [
        "POSTING",
        f"Title: {posting.title}",
        f"Company: {posting.company_name}",
        f"Location: {posting.location or ''} {cc}".rstrip(),
        f"Type: {posting.type or ''}",
        f"Area: {posting.area or ''}",
        f"Start: {posting.start_info or ''}",
        f"Deadline: {posting.deadline.isoformat() if posting.deadline else ''}",
    ]
    if company is not None:
        if company.sector:
            lines.append(f"Company sector: {company.sector}")
        if company.program:
            lines.append(f"Company program: {company.program}")
        if company.why:
            lines.append(f"Candidate's note on the company: {company.why}")
    if flags:
        lines.append("RADAR FLAGS: " + "; ".join(flags))
    lines.append(f"Description{source_note}:")
    lines.append(text or "(no description available)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- scoring

SCORING_SYSTEM = f"""[radar-task: {TASK_SCORE}]
You screen internship and graduate job postings for one candidate. Judge fit only from the
CANDIDATE and POSTING sections. Do not invent facts.
Return ONLY a JSON object with exactly these keys:
{{"priority": "Alta"|"Media"|"Bassa", "why_fits": string, "what_doesnt_fit": string,
"start_window": string|null, "language_requirements": [string], "confidence": number,
"warnings": [string]}}
Rules:
- priority: Alta = strong match with the target roles, skills and start window (apply now);
  Media = plausible with a clear gap; Bassa = weak match.
- why_fits: ITALIAN, at most 2 short sentences. Connect one specific CANDIDATE item
  (experience, project, skill) to one specific POSTING requirement, e.g. "Il lavoro sugli
  scraper in Acme corrisponde alla pipeline dati richiesta.". Do not just list the posting's
  requirements. If nothing connects, write "Nessun punto di contatto forte.".
- what_doesnt_fit: ITALIAN, at most 2 short sentences, the main concrete gap or blocker (skill,
  language, start date, seniority, location). If nothing blocks, write "Nessun ostacolo evidente.".
- The candidate wants an INTERNSHIP (or junior role) starting February 2027, flexible until
  April 2027, in: AI/LLM engineering, data science/ML, forward deployed engineering, AI
  consulting, product/strategy in tech, venture capital.
- Priority rubric (apply strictly):
  Alta = the main work is in one of those target areas (AI, ML, LLM, data science, data
  engineering for analytics/AI, FDE, AI/data consulting, product or strategy, VC) AND the start
  is compatible (Feb-Apr 2027, flexible, or not stated) AND no hard blocker.
  Media = adjacent role (generic software engineering, fullstack, backend, frontend, QA,
  BI/reporting, quant/trading, operations analytics) with a compatible start, OR a target role
  with one real gap (missing key skill, enrollment agreement needed, unclear start).
  Bassa = off-target area, OR start incompatible (summer internship, starts June-September
  2027, or before 2027), OR a hard blocker (required language, visa, seniority).
- RADAR FLAGS in the posting are facts from deterministic checks: take them into account in
  what_doesnt_fit and warnings (e.g. a French or Spanish internship needs a university
  agreement; "start not stated" means confirm the start date).
- Never claim the start fits unless the posting states a compatible start. "Summer
  internship" is never compatible.
- start_window: start date or period as stated in the posting, else null.
- language_requirements: languages the posting requires, e.g. ["English C1", "German"]; [] if none.
- warnings: short Italian notes on blockers (deadline passed, must be enrolled as a student,
  start date incompatible, visa, unpaid, very short description); [] if none.
- confidence: 0 to 1, lower when the description is short or vague.
- Never use the em dash character."""


def scoring_system() -> str:
    return SCORING_SYSTEM


def scoring_user(
    profile_text: str,
    posting: Posting,
    company: Company | None,
    flags: Sequence[str] | None = None,
) -> str:
    return (
        f"CANDIDATE\n{profile_text}\n\n"
        f"{posting_block(posting, company, SCORING_POSTING_CHARS, flags)}\n\n"
        f"Today: {today().isoformat()}"
    )


def repair_suffix(previous: str, error: str) -> str:
    return (
        "\n\nYOUR PREVIOUS OUTPUT WAS INVALID: "
        f"{error[:300]}\nPrevious output:\n{previous[:2000]}\n"
        "Return only the corrected JSON object with all required keys."
    )


# --------------------------------------------------------------------------- packs

_COMMON_RULES = """Hard rules for every text:
- Never invent facts about the candidate or the company. Use only the CANDIDATE and POSTING
  sections. When a needed fact is missing, write a visible marker such as [CHECK: team name].
- Direct, specific, human. Short sentences. No cliches (never: "I am writing to express my
  interest", "passionate", "leverage", "dynamic environment", "fast-paced environment",
  "team player", "synergy", "I am excited to apply"; in Italian never: "con la presente",
  "appassionato", "ambiente dinamico", "sinergia").
- Never use the em dash or the en dash. Use commas, periods or a plain hyphen."""


def _availability_line(language: str) -> str:
    if language == "it":
        return AVAILABILITY_SENTENCE["it"]
    return AVAILABILITY_SENTENCE["en"]


def pack_system(language: str) -> str:
    lang_name = LANGUAGE_NAMES.get(language, "English")
    greet = "Ciao [NOME]," if language == "it" else "Hi [NOME],"
    return f"""[radar-task: {TASK_PACK}]
You write application material for one candidate and one posting. The candidate reviews and
sends everything by hand. Output language: {lang_name}.
Return ONLY a JSON object:
{{"cover_letter": string, "form_answers": [{{"question": string, "answer": string}}],
"linkedin_connection_note": string, "linkedin_message": string, "followup": string}}
{_COMMON_RULES}
cover_letter: at most 230 words, paragraphs separated by blank lines. Line 1 is one sentence that
starts with "{_availability_line(language)}" (no salutation before it). Then cite at least one
concrete detail from the posting and at least one concrete project or experience of the candidate
that matches it. End with a short sign-off and the candidate's name.
form_answers: exactly 6 items, answers at most 70 words each:
1 Why this company? 2 Why this role? 3 Most relevant project? 4 Availability, start date,
duration and graduation date. 5 Work authorization (use the candidate's citizenship).
6 Salary expectations: "[CHECK: ...]" placeholder unless the posting states a range.
linkedin_connection_note: at most 260 characters, starts "{greet}", for a hiring manager or alum at
the company, mentions something real from the posting or company, asks ONE specific question,
never asks for a job or a referral.
linkedin_message: at most 550 characters, starts "{greet}", same rules as the note, a bit more
context about the candidate (one concrete project), ONE specific question.
followup: at most 550 characters, starts "{greet}", sent 14 days after applying: names the role,
recalls ONE concrete project or experience that is already in CANDIDATE and fits the role (never
claim new work done "since" applying; if you want to add news, write "[CHECK: novita da
aggiungere]"), asks politely about the timeline.
Fact discipline (most important rule): every claim about the candidate must be traceable to a
line in CANDIDATE, with the same scope. Do not upgrade or generalise: scraping is not NLP, a
listed skill is not a project, "two pilot gyms" stays two pilot gyms. Never invent projects,
results, numbers or plans. When unsure, write a [CHECK: ...] marker instead.
Style: sound like a capable person writing quickly and precisely. Never use: "I am drawn to",
"I am eager to", "eager to contribute", "I look forward to", "thank you for considering",
"exactly the", "sono motivato", "motivato a contribuire", "mi piacerebbe molto". The cover
letter has 3 or 4 short paragraphs separated by blank lines and never mentions salary or
stipend."""


def pack_user(profile_text: str, posting: Posting, company: Company | None) -> str:
    return (
        f"CANDIDATE\n{profile_text}\n\n"
        f"{posting_block(posting, company, DRAFT_POSTING_CHARS)}\n\n"
        f"Apply link: {posting.url}\nToday: {today().isoformat()}"
    )


def regenerate_suffix(previous: str, issues: list[str]) -> str:
    listed = "\n".join(f"- {i}" for i in issues[:20])
    return (
        f"\n\nYOUR PREVIOUS DRAFT:\n{previous[:6000]}\n\n"
        f"It breaks these rules:\n{listed}\n"
        "Rewrite it fixing every issue and keep everything else. Return the full JSON object."
    )


# --------------------------------------------------------------------------- contact drafts

_KIND_RULES = {
    "linkedin_connection_note": "a LinkedIn connection note of at most 260 characters",
    "linkedin_message": "a LinkedIn message of at most 550 characters",
    "followup": "a follow-up message of at most 550 characters, sent 14 days after applying",
    "email": "a short email of at most 550 characters",
}


def contact_draft_system(kind: str, language: str) -> str:
    lang_name = LANGUAGE_NAMES.get(language, "English")
    return f"""[radar-task: {TASK_CONTACT}]
You write {_KIND_RULES.get(kind, _KIND_RULES["linkedin_message"])} from the candidate to the
CONTACT below, about the posting. Output language: {lang_name}.
Return ONLY a JSON object: {{"text": string}}
{_COMMON_RULES}
- Address the contact by first name. Use their role and the candidate's note about why this
  person, if given. Mention something real from the posting or company.
- Ask ONE specific question. Never ask for a job or a referral directly."""


def contact_draft_user(
    profile_text: str, posting: Posting, company: Company | None, contact: Contact
) -> str:
    return (
        f"CANDIDATE\n{profile_text}\n\n"
        f"{posting_block(posting, company, CONTACT_POSTING_CHARS)}\n\n"
        "CONTACT\n"
        f"Contact name: {contact.name}\n"
        f"Contact role: {contact.role or ''}\n"
        f"Why this person: {contact.why or ''}"
    )


# --------------------------------------------------------------------------- fake generator


def _field(text: str, key: str) -> str:
    m = re.search(rf"(?m)^{re.escape(key)}:[ \t]*(.*)$", text)
    return m.group(1).strip() if m else ""


def _first_project(user: str) -> tuple[str, str]:
    m = re.search(r"(?m)^Projects:\n- ([^:\n]+): ?(.*)$", user)
    if not m:
        return "[CHECK: project]", "[CHECK: what it does]"
    return m.group(1).strip(), m.group(2).strip().rstrip(".")


def _posting_detail(user: str) -> str:
    m = re.search(r"(?ms)^Description[^\n]*:\n(.*)", user)
    desc = (m.group(1) if m else "").split("\nApply link:")[0].split("\nToday:")[0].strip()
    if not desc or desc.startswith("(no description"):
        return ""
    first = re.split(r"(?<=[.!?])\s|\n", desc, maxsplit=1)[0].strip().rstrip(".")
    return first[:140]


def _fake_score(user: str) -> str:
    title = _field(user, "Title")
    company = _field(user, "Company")
    t = title.lower()
    if re.search(r"\b(data|ml|machine learning|ai|llm|scien\w*|analy\w*|nlp|genai)\b", t):
        priority = "Alta"
    elif re.search(r"\b(sales|marketing|legal|hr|finance|accounting)\b", t):
        priority = "Bassa"
    else:
        priority = "Media"
    langs = [
        name
        for name in ("English", "Italian", "German", "French", "Dutch", "Spanish")
        if re.search(rf"\b{name}\b", user.split("\nPOSTING\n")[-1], re.I)
    ]
    return json.dumps(
        {
            "priority": priority,
            "why_fits": (
                f"Il ruolo {title[:80]} presso {company[:60]} è coerente con i ruoli target."
            ),
            "what_doesnt_fit": "Nessun ostacolo evidente.",
            "start_window": _field(user, "Start") or None,
            "language_requirements": langs,
            "confidence": 0.5,
            "warnings": ["Valutazione offline (FakeProvider): da rivedere."],
        },
        ensure_ascii=False,
    )


def _fake_pack(system: str, user: str) -> str:
    it = "Output language: Italian" in system
    title = _field(user, "Title")[:70] or "[CHECK: role]"
    company = _field(user, "Company")[:60] or "[CHECK: company]"
    name = _field(user, "Name") or "[CHECK: name]"
    citizenship = _field(user, "Citizenship") or "[CHECK: citizenship]"
    graduation = _field(user, "Graduation") or "[CHECK: graduation date]"
    pname, pwhat = _first_project(user)
    detail = _posting_detail(user)
    if it:
        detail_s = (
            f'Nell\'annuncio leggo: "{detail}".'
            if detail
            else "[CHECK: aggiungi un dettaglio concreto dall'annuncio]."
        )
        cover = (
            f"{AVAILABILITY_SENTENCE['it']}\n\n"
            f"Mi candido per il ruolo di {title} presso {company}. "
            f"{detail_s} Nel progetto {pname} ({pwhat}) ho lavorato su un problema "
            "vicino a questo.\n\n"
            "[CHECK: aggiungi un secondo collegamento concreto tra la tua esperienza "
            "e il ruolo.]\n\n"
            f"Cordiali saluti,\n{name}"
        )
        answers = [
            ("Perché questa azienda?", f"[CHECK: motivo specifico per {company}]"),
            ("Perché questo ruolo?", f"Il ruolo {title} è in linea con il progetto {pname}."),
            ("Progetto più rilevante?", f"{pname}: {pwhat}."),
            (
                "Disponibilità e durata?",
                "Full-time da febbraio 2027, inizio flessibile fino ad aprile 2027. "
                f"Laurea: {graduation}. [CHECK: durata]",
            ),
            ("Permesso di lavoro?", f"{citizenship}."),
            (
                "Aspettative economiche?",
                "[CHECK: aspettativa economica, l'annuncio non indica una cifra]",
            ),
        ]
        note = (
            f"Ciao [NOME], ho visto la posizione {title} in {company}. "
            "Che tipo di progetto segue di solito chi entra nel team?"
        )
        msg = (
            f"Ciao [NOME], mi sono candidato per {title} in {company}. Ho lavorato a {pname}. "
            "Quale competenza conta di più nei primi mesi nel vostro team?"
        )
        follow = (
            f"Ciao [NOME], due settimane fa mi sono candidato per {title}. "
            f"Nel frattempo ho approfondito {pname}. "
            "Ci sono novità sulle tempistiche della selezione?"
        )
    else:
        detail_s = (
            f'Your posting mentions "{detail}".'
            if detail
            else "[CHECK: add one concrete detail from the posting]."
        )
        cover = (
            f"{AVAILABILITY_SENTENCE['en']}\n\n"
            f"I am applying for the {title} role at {company}. "
            f"{detail_s} In my project {pname} ({pwhat}) I worked on a closely related problem.\n\n"
            "[CHECK: add a second concrete link between your experience and the role.]\n\n"
            f"Best regards,\n{name}"
        )
        answers = [
            ("Why this company?", f"[CHECK: specific reason for {company}]"),
            ("Why this role?", f"The {title} role matches the work I did in {pname}."),
            ("Most relevant project?", f"{pname}: {pwhat}."),
            (
                "Availability, start date and duration?",
                "Full-time from February 2027, start flexible until April 2027. "
                f"Graduation: {graduation}. [CHECK: duration]",
            ),
            ("Work authorization?", f"{citizenship}."),
            ("Salary expectations?", "[CHECK: salary expectation, the posting states no range]"),
        ]
        note = (
            f"Hi [NOME], I saw the {title} opening at {company}. "
            "What kind of first project do new joiners on the team usually take on?"
        )
        msg = (
            f"Hi [NOME], I applied for {title} at {company}. I built {pname}. "
            "Which skill matters most in the first months on your team?"
        )
        follow = (
            f"Hi [NOME], two weeks ago I applied for {title}. "
            f"Since then I kept working on {pname}. "
            "Is there any update on the timeline for the selection?"
        )
    return json.dumps(
        {
            "cover_letter": cover,
            "form_answers": [{"question": q, "answer": a} for q, a in answers],
            "linkedin_connection_note": note[:280],
            "linkedin_message": msg[:600],
            "followup": follow[:600],
        },
        ensure_ascii=False,
    )


def _fake_contact(system: str, user: str) -> str:
    it = "Output language: Italian" in system
    first = (_field(user, "Contact name").split() or ["[NOME]"])[0]
    title = _field(user, "Title")[:70]
    company = _field(user, "Company")[:60]
    if it:
        text = (
            f"Ciao {first}, ho visto la posizione {title} in {company}. "
            "Quale competenza conta di più nei primi mesi nel team?"
        )
    else:
        text = (
            f"Hi {first}, I saw the {title} opening at {company}. "
            "Which skill matters most in the first months on the team?"
        )
    return json.dumps({"text": text}, ensure_ascii=False)


def fake_completion(system: str, user: str) -> str:
    """Deterministic output for the FakeProvider, chosen by the task marker in ``system``."""
    m = re.match(r"\[radar-task: ([a-z-]+)\]", system)
    task = m.group(1) if m else ""
    if task == TASK_SCORE:
        return _fake_score(user)
    if task == TASK_PACK:
        return _fake_pack(system, user)
    if task == TASK_CONTACT:
        return _fake_contact(system, user)
    return json.dumps({"text": "ok"})
