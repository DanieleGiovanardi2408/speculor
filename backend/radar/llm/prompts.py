"""Compact prompts for scoring and application packs, plus the offline FakeProvider generator.

Prompt layout (stable, also parsed by ``fake_completion``):

    [radar-task: <task>]          first line of every system prompt
    CANDIDATE / POSTING ...       sections of the user prompt, ``Key: value`` lines
    CANDIDATE FACTS               drafting prompts: ``[fact id] text`` lines (radar.facts), then
                                  PROHIBITED; the verifier adds TEXTS with ``### <text id>``

Scoring still uses the free-text profile (config/profile.yaml, ``condense_profile``); every draft
uses only the fact bank.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from typing import Any

from radar.facts import Fact, FactBank, empty_bank
from radar.facts import render as render_facts
from radar.llm.style import AVAILABILITY_SENTENCE
from radar.models import Company, Contact, Posting
from radar.timeutil import today

TASK_SCORE = "score"
TASK_PACK = "pack"
TASK_CONTACT = "contact-draft"
TASK_VERIFY = "verify"

SCORING_PROFILE_CHARS = 2000  # ~500 tokens
SCORING_POSTING_CHARS = 6000
DRAFT_POSTING_CHARS = 7000
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
  April 2027. Top areas: AI/ML/LLM engineering, data science and analytics, AI/data
  consulting, venture capital and investment, strategy and business analysis (including
  fintech strategy), forward deployed engineering. Product roles count only for AI/data
  products. The candidate speaks Italian (native) and English (C1) only.
- Priority rubric (apply strictly):
  Alta = the main work is in one of the top areas AND the start is compatible (Feb-Apr 2027,
  flexible, or not stated) AND no hard blocker AND the job can be done in Italian or English.
  Media = adjacent role (generic software engineering, fullstack, backend, frontend, QA or
  testing, DevOps, sales, BI/reporting only, quant/trading, operations analytics) with a
  compatible start, OR a top-area role with one real gap (missing key skill, enrollment
  agreement needed, unclear start, description written mostly in French, German, Spanish or
  Dutch so that language is probably needed). Adjacent roles are never Alta.
  Bassa = off-target area, OR start incompatible (summer internship, starts June-September
  2027, or before 2027), OR a hard blocker (a language other than Italian/English required,
  visa, seniority).
- RADAR FLAGS in the posting are facts from deterministic checks: take every one of them into
  account in priority, what_doesnt_fit and warnings. "lingua probabilmente richiesta: X" means
  the description is mostly in X: never Alta, name the language in what_doesnt_fit. "X
  required" means a hard language blocker. A French or Spanish internship needs a university
  agreement; "start not stated" means confirm the start date; a UK location needs a visa.
- Never claim the start fits unless the posting states a compatible start. "Summer
  internship" is never compatible.
- start_window: start date or period as stated in the posting, else null.
- language_requirements: languages the posting requires, e.g. ["English C1", "German"]; add
  "(plus)" to a language that is only nice to have, e.g. ["English", "Dutch (plus)"]; list the
  language of the description when it is not English or Italian and the role clearly needs it;
  [] if none.
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


# --------------------------------------------------------------------------- facts

FACTS_HEADER = "CANDIDATE FACTS"
PROHIBITED_HEADER = "PROHIBITED"
TEXTS_HEADER = "TEXTS"
_FACT_LINE = re.compile(r"^\[([A-Za-z0-9_.-]+)\] (.*)$")


def facts_block(bank: FactBank, facts: Sequence[Fact] | None = None) -> str:
    """The fact list as ``[id] text`` lines, then prohibitions and the user's own rules."""
    lines = [
        f"{FACTS_HEADER} (the only source of facts about the candidate)",
        render_facts(bank.facts if facts is None else facts) or "(none)",
    ]
    if bank.prohibitions:
        lines += ["", f"{PROHIBITED_HEADER} (never claim, never mention)"]
        lines += [f"[{p.id}] {p.text}" for p in bank.prohibitions]
    if bank.rules:
        lines += ["", "CANDIDATE'S OWN RULES"]
        lines += [f"- {r}" for r in bank.rules]
    return "\n".join(lines)


def _english_rule(bank: FactBank) -> str:
    level = bank.english_level or "C1"
    if "native" in level.lower():
        return ""
    return (
        f'- English level is {level}: write "fluent English" (or "{level}"), never "native" '
        '("madrelingua") or "bilingual".\n'
    )


def fact_rules(bank: FactBank) -> str:
    return f"""Fact discipline (most important rule):
- CANDIDATE FACTS is the ONLY source of facts about the candidate. Every factual claim about the
  candidate (experience, projects, results, numbers, tools, skills, languages, education,
  availability, citizenship, plans) must come from one listed fact, with the same scope. Do not
  upgrade or generalise: scraping is not NLP, a listed skill is not a project, "helped run" is
  not "led", "two pilot gyms" stays two pilot gyms.
- Anything about the candidate that no listed fact states must be written as a visible marker
  [CHECK: what to verify], never as a plain sentence.
- Never claim or mention anything listed in PROHIBITED.
- Numbers: use only numbers that appear in CANDIDATE FACTS, exactly as written there (same
  rounding, same unit). Never compute, round, combine or invent a number.
{_english_rule(bank)}- Facts about the company come only from POSTING."""


_CLAIMS_RULE = """claims: one item per factual claim about the candidate in any text, with the ids
of the facts that support it, e.g. {"claim": "I built scrapers in Python", "fact_ids":
["experience.acme.did.0"]}. A [CHECK: ...] marker gets "fact_ids": []. Internal, never shown."""

# --------------------------------------------------------------------------- packs

_COMMON_RULES = """Hard rules for every text:
- Never invent facts about the candidate or the company. When a needed fact is missing, write a
  visible marker such as [CHECK: team name].
- Direct, specific, human. Short sentences. No cliches (never: "I am writing to express my
  interest", "passionate", "leverage", "dynamic environment", "fast-paced environment",
  "team player", "synergy", "I am excited to apply"; in Italian never: "con la presente",
  "appassionato", "ambiente dinamico", "sinergia").
- Never use the em dash or the en dash. Use commas, periods or a plain hyphen."""


def _availability_line(language: str, bank: FactBank | None = None) -> str:
    if bank is not None:
        return bank.availability_sentence(language)
    return AVAILABILITY_SENTENCE["it" if language == "it" else "en"]


def pack_system(language: str, bank: FactBank | None = None) -> str:
    bank = bank or empty_bank()
    lang_name = LANGUAGE_NAMES.get(language, "English")
    greet = "Ciao [NOME]," if language == "it" else "Hi [NOME],"
    return f"""[radar-task: {TASK_PACK}]
You write application material for one candidate and one posting. The candidate reviews and
sends everything by hand. Output language: {lang_name}.
Return ONLY a JSON object:
{{"cover_letter": string, "form_answers": [{{"question": string, "answer": string}}],
"linkedin_connection_note": string, "linkedin_message": string, "followup": string,
"claims": [{{"claim": string, "fact_ids": [string]}}]}}
{_COMMON_RULES}
{fact_rules(bank)}
cover_letter: at most 230 words, 3 or 4 short paragraphs separated by blank lines. Line 1 is
exactly "{_availability_line(language, bank)}" on its own line (no salutation before it). Then
cite at least one concrete detail from the posting and at least one concrete project or
experience from CANDIDATE FACTS that matches it. End with a short sign-off and the candidate's
name (fact identity.name).
form_answers: exactly 6 items, answers at most 70 words each:
1 Why this company? 2 Why this role? 3 Most relevant project? 4 Availability, start date,
duration and graduation date. 5 Work authorization (use the citizenship fact).
6 Salary expectations: "[CHECK: ...]" placeholder unless the posting states a range.
linkedin_connection_note: at most 260 characters, starts "{greet}", for a hiring manager or alum at
the company, mentions something real from the posting or company, asks ONE specific question,
never asks for a job or a referral.
linkedin_message: at most 550 characters, starts "{greet}", same rules as the note, a bit more
context about the candidate (one concrete project from the facts), ONE specific question.
followup: at most 550 characters, starts "{greet}", sent 14 days after applying: names the role,
recalls ONE concrete project or experience from CANDIDATE FACTS that fits the role (never
claim new work done "since" applying; if you want to add news, write "[CHECK: novita da
aggiungere]"), asks politely about the timeline.
{_CLAIMS_RULE}
Style: sound like a capable person writing quickly and precisely. Never use: "I am drawn to",
"I am eager to", "eager to contribute", "I look forward to", "thank you for considering",
"exactly the", "sono motivato", "motivato a contribuire", "mi piacerebbe molto". The cover
letter never mentions salary or stipend."""


def pack_user(facts_text: str, posting: Posting, company: Company | None) -> str:
    return (
        f"{facts_text}\n\n"
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


def contact_draft_system(kind: str, language: str, bank: FactBank | None = None) -> str:
    bank = bank or empty_bank()
    lang_name = LANGUAGE_NAMES.get(language, "English")
    return f"""[radar-task: {TASK_CONTACT}]
You write {_KIND_RULES.get(kind, _KIND_RULES["linkedin_message"])} from the candidate to the
CONTACT below, about the posting. Output language: {lang_name}.
Return ONLY a JSON object: {{"text": string, "claims": [{{"claim": string, "fact_ids": [string]}}]}}
{_COMMON_RULES}
{fact_rules(bank)}
- Address the contact by first name. Use their role and the candidate's note about why this
  person, if given. Mention something real from the posting or company.
- Ask ONE specific question. Never ask for a job or a referral directly.
{_CLAIMS_RULE}"""


def contact_draft_user(
    facts_text: str, posting: Posting, company: Company | None, contact: Contact
) -> str:
    return (
        f"{facts_text}\n\n"
        f"{posting_block(posting, company, CONTACT_POSTING_CHARS)}\n\n"
        "CONTACT\n"
        f"Contact name: {contact.name}\n"
        f"Contact role: {contact.role or ''}\n"
        f"Why this person: {contact.why or ''}"
    )


# --------------------------------------------------------------------------- verification

VERIFY_SYSTEM = f"""[radar-task: {TASK_VERIFY}]
You check application texts written for one candidate against the candidate's fact bank. You
do not rewrite anything.
Return ONLY a JSON object:
{{"claims": [{{"text_id": string, "claim": string, "fact_id": string|null,
"verdict": "supported"|"unsupported"|"check"}}]}}
Rules:
- A claim is a sentence (or clause) that states something about the candidate: experience,
  projects, results, numbers, tools, skills, languages, education, availability, citizenship,
  plans. List EVERY claim in every text. Skip greetings, sign-offs, questions to the reader,
  statements of interest or motivation ("I am interested in ...", "I would welcome a
  conversation"), and sentences only about the company, the role or the posting: those are
  checked against the POSTING section, not the facts, and are not claims about the candidate.
- A sentence that mixes both is a claim only for its candidate part (e.g. "the rotations match
  my plan to work abroad" -> claim "plan to work abroad").
- text_id: the id after "###" of the text the claim comes from. claim: copied verbatim.
- supported: one listed fact in CANDIDATE FACTS backs the claim fully, with the same scope;
  fact_id is that fact's id (the text in brackets).
- unsupported: no fact backs it, or it goes beyond the fact (upgraded scope, a number that is not
  in the facts or is written differently, a skill turned into a project), or it breaks a
  PROHIBITED item. fact_id: the closest fact id, or the PROHIBITED id it breaks, or null.
- check: the claim is a [CHECK: ...] marker or depends on one. fact_id: null.
- Be strict: when unsure, the verdict is unsupported."""


def verify_system() -> str:
    return VERIFY_SYSTEM


VERIFY_POSTING_CHARS = 3000


def verify_user(facts_text: str, texts: Sequence[tuple[str, str]], posting_text: str = "") -> str:
    blocks = "\n\n".join(f"### {tid}\n{text.strip()}" for tid, text in texts)
    posting = ""
    if posting_text.strip():
        excerpt = posting_text.strip()[:VERIFY_POSTING_CHARS]
        posting = f"\n\nPOSTING (context only, not candidate facts):\n{excerpt}"
    return f"{facts_text}{posting}\n\n{TEXTS_HEADER}\n{blocks}"


# --------------------------------------------------------------------------- fake generator


def _field(text: str, key: str) -> str:
    m = re.search(rf"(?m)^{re.escape(key)}:[ \t]*(.*)$", text)
    return m.group(1).strip() if m else ""


def parse_fact_lines(user: str, header: str = FACTS_HEADER) -> dict[str, str]:
    """``{id: text}`` from the ``[id] text`` lines of a prompt section (used by the fake)."""
    out: dict[str, str] = {}
    m = re.search(rf"(?m)^{re.escape(header)}\b.*$", user)
    if not m:
        return out
    for line in user[m.end() :].lstrip("\n").splitlines():
        lm = _FACT_LINE.match(line.strip())
        if not lm:
            break
        out[lm.group(1)] = lm.group(2)
    return out


def _value(facts: dict[str, str], fid: str) -> str:
    """The value part of a fact text ("Label, key: value" -> "value")."""
    text = facts.get(fid, "")
    key = fid.rsplit(".", 1)[-1].replace("_", " ")
    marker = f"{key}: "
    idx = text.find(marker)
    return (text[idx + len(marker) :] if idx >= 0 else text).strip()


def _short(text: str, limit: int = 110) -> str:
    text = text.strip().rstrip(".")
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:")


def _first_project(facts: dict[str, str]) -> tuple[str, str, list[str]]:
    """(name, what, fact ids) of the first project in the facts."""
    for fid in facts:
        m = re.match(r"^projects\.([^.]+)\.name$", fid)
        if not m:
            continue
        base = f"projects.{m.group(1)}"
        for key in ("what", "problem", "solution"):
            if f"{base}.{key}" in facts:
                what = _value(facts, f"{base}.{key}")
                return _value(facts, fid), _short(what), [fid, f"{base}.{key}"]
        return _value(facts, fid), "[CHECK: what it does]", [fid]
    return "[CHECK: project]", "[CHECK: what it does]", []


def _first_value(facts: dict[str, str], pattern: str) -> tuple[str, str | None]:
    for fid in facts:
        if re.match(pattern, fid):
            return _value(facts, fid), fid
    return "", None


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
    posting = user.split("\nPOSTING\n")[-1]
    langs: list[str] = []
    for name in ("English", "Italian", "German", "French", "Dutch", "Spanish"):
        m = re.search(rf"[^.\n]*\b{name}\b[^.\n]*", posting, re.I)
        if not m:
            continue
        hard = name in ("English", "Italian") or re.search(
            r"\b(?:required|fluent|must|native|mandatory)\b", m.group(0), re.I
        )
        langs.append(name if hard else f"{name} (plus)")
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
    facts = parse_fact_lines(user)
    title = _field(user, "Title")[:70] or "[CHECK: role]"
    company = _field(user, "Company")[:60] or "[CHECK: company]"
    name = _value(facts, "identity.name") or "[CHECK: name]"
    citizenship = _value(facts, "identity.citizenship") or "[CHECK: citizenship]"
    graduation, grad_id = _first_value(facts, r"^education\.[^.]+\.dates$")
    graduation = graduation or "[CHECK: graduation date]"
    pname, pwhat, pids = _first_project(facts)
    avail_key = "meta.decisions.availability_it" if it else "meta.decisions.availability"
    availability = facts.get(avail_key) or AVAILABILITY_SENTENCE["it" if it else "en"]
    detail = _posting_detail(user)
    if it:
        detail_s = (
            f'Nell\'annuncio leggo: "{detail}".'
            if detail
            else "[CHECK: aggiungi un dettaglio concreto dall'annuncio]."
        )
        project_s = f"Nel progetto {pname} ho lavorato su questo: {pwhat}."
        cover = (
            f"{availability}\n\n"
            f"Mi candido per il ruolo di {title} presso {company}. {detail_s}\n\n"
            f"{project_s}\n\n"
            "[CHECK: aggiungi un secondo collegamento concreto tra la tua esperienza "
            "e il ruolo.]\n\n"
            f"Cordiali saluti,\n{name}"
        )
        answers = [
            ("Perché questa azienda?", f"[CHECK: motivo specifico per {company}]"),
            ("Perché questo ruolo?", f"Il ruolo {title} è vicino al progetto {pname}."),
            ("Progetto più rilevante?", f"{pname}: {pwhat}."),
            ("Disponibilità e durata?", f"{availability} Laurea: {graduation}. [CHECK: durata]"),
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
            f"Il mio progetto {pname} è vicino al ruolo. "
            "Ci sono novità sulle tempistiche della selezione?"
        )
    else:
        detail_s = (
            f'Your posting mentions "{detail}".'
            if detail
            else "[CHECK: add one concrete detail from the posting]."
        )
        project_s = f"In my project {pname} I worked on this: {pwhat}."
        cover = (
            f"{availability}\n\n"
            f"I am applying for the {title} role at {company}. {detail_s}\n\n"
            f"{project_s}\n\n"
            "[CHECK: add a second concrete link between your experience and the role.]\n\n"
            f"Best regards,\n{name}"
        )
        answers = [
            ("Why this company?", f"[CHECK: specific reason for {company}]"),
            ("Why this role?", f"The {title} role is close to my project {pname}."),
            ("Most relevant project?", f"{pname}: {pwhat}."),
            (
                "Availability, start date and duration?",
                f"{availability} Graduation: {graduation}. [CHECK: duration]",
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
            f"My project {pname} is close to the role. "
            "Is there any update on the timeline for the selection?"
        )
    claims = [
        {"claim": availability, "fact_ids": [avail_key]},
        {"claim": project_s, "fact_ids": pids},
        {"claim": f"{citizenship}.", "fact_ids": ["identity.citizenship"]},
    ]
    if grad_id:
        claims.append({"claim": f"{graduation}", "fact_ids": [grad_id]})
    return json.dumps(
        {
            "cover_letter": cover,
            "form_answers": [{"question": q, "answer": a} for q, a in answers],
            "linkedin_connection_note": note[:280],
            "linkedin_message": msg[:600],
            "followup": follow[:600],
            "claims": claims,
        },
        ensure_ascii=False,
    )


def _fake_contact(system: str, user: str) -> str:
    it = "Output language: Italian" in system
    facts = parse_fact_lines(user)
    first = (_field(user, "Contact name").split() or ["[NOME]"])[0]
    title = _field(user, "Title")[:70]
    company = _field(user, "Company")[:60]
    pname, _, pids = _first_project(facts)
    if it:
        text = (
            f"Ciao {first}, ho visto la posizione {title} in {company}. Ho lavorato a {pname}. "
            "Quale competenza conta di più nei primi mesi nel team?"
        )
        claim = f"Ho lavorato a {pname}."
    else:
        text = (
            f"Hi {first}, I saw the {title} opening at {company}. I built {pname}. "
            "Which skill matters most in the first months on the team?"
        )
        claim = f"I built {pname}."
    return json.dumps(
        {"text": text, "claims": [{"claim": claim, "fact_ids": pids}]}, ensure_ascii=False
    )


_FIRST_PERSON = re.compile(  # "I" stays case-sensitive: "i" is an Italian article
    r"\b(I|I'm|I've|I'd|[Mm]y|[Mm]e|[Mm]ine)\b"
    r"|\b([Hh]o|[Mm]io|[Mm]ia|[Mm]iei|[Mm]ie|[Ss]ono|[Mm]i|[Dd]isponibile|[Ll]aurea)\b"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_GREETING = re.compile(
    r"^(hi|hello|dear|ciao|gentile|best regards|kind regards|regards|cordiali saluti|"
    r"saluti|grazie|thank you)\b",
    re.I,
)
_TOKEN = re.compile(r"[a-zà-ÿ0-9]{4,}")
_FAKE_STOP = frozenset(
    [
        "with",
        "from",
        "that",
        "this",
        "have",
        "your",
        "role",
        "team",
        "work",
        "about",
        "when",
        "what",
        "which",
        "into",
        "they",
        "worked",
        "project",
        "progetto",
        "built",
        "questo",
        "questa",
        "sono",
        "della",
        "nella",
        "ruolo",
    ]
)


_NON_CLAIM = re.compile(
    r"\b(applying|applied|apply|saw the|candido|candidato|candidata|ho visto)\b", re.I
)


def claim_sentences(text: str) -> list[str]:
    """Sentences of ``text`` that state something about the candidate (fake verifier)."""
    out = []
    for raw in _SENTENCE_SPLIT.split(text):
        s = raw.strip()
        if not s or s.endswith("?") or (_GREETING.match(s) and len(s.split()) <= 4):
            continue
        about_me = _FIRST_PERSON.search(s) or re.search(r"\d", s)
        if "[CHECK" in s or (about_me and not _NON_CLAIM.search(s)):
            out.append(s)
    return out


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower())) - _FAKE_STOP


def _fake_verify(user: str) -> str:
    facts = parse_fact_lines(user)
    fact_tokens = {fid: _tokens(_value(facts, fid)) for fid in facts}
    m = re.search(rf"(?m)^{TEXTS_HEADER}$", user)
    body = user[m.end() :] if m else ""
    claims = []
    for block in re.split(r"(?m)^### ", body)[1:]:
        tid, _, text = block.partition("\n")
        for sentence in claim_sentences(text):
            if "[CHECK" in sentence:
                claims.append(
                    {"text_id": tid.strip(), "claim": sentence, "fact_id": None, "verdict": "check"}
                )
                continue
            words = _tokens(sentence)
            best, score = None, 0
            # A project name or a short fact value quoted verbatim counts as support.
            for fid in sorted(facts, key=lambda f: not f.endswith(".name")):
                value = _value(facts, fid)
                if 4 <= len(value) <= 40 and re.search(
                    rf"(?<!\w){re.escape(value.rstrip('.'))}(?!\w)", sentence, re.I
                ):
                    best, score = fid, 2
                    break
            for fid, toks in fact_tokens.items():
                shared = len(words & toks)
                if shared > score:
                    best, score = fid, shared
            supported = best is not None and score >= 2
            claims.append(
                {
                    "text_id": tid.strip(),
                    "claim": sentence,
                    "fact_id": best if score >= 1 else None,
                    "verdict": "supported" if supported else "unsupported",
                }
            )
    return json.dumps({"claims": claims}, ensure_ascii=False)


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
    if task == TASK_VERIFY:
        return _fake_verify(user)
    return json.dumps({"text": "ok"})
