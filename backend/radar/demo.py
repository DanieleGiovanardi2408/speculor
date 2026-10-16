"""Fictional demo dataset for local mode, screenshots and API tests.

Everything here is invented: company names, people, postings and URLs (example.com only).
Dates are relative to today so the Today screen always looks alive. Idempotent: if the demo
companies already exist, nothing is written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from radar.models import (
    Application,
    ApplicationPack,
    ChangeEvent,
    Company,
    Contact,
    Draft,
    FilterResult,
    LLMUsage,
    Posting,
    Run,
    Score,
)
from radar.pipeline.normalize import canonical_url, content_hash, dedupe_key, zone_for
from radar.timeutil import utcnow

AVAILABILITY_LINE = "Available full-time from February 2027, start flexible until April 2027."
SCORING_MODEL = "openai/gpt-oss-20b"
DRAFT_MODEL = "openai/gpt-oss-120b"


@dataclass(frozen=True)
class _Co:
    name: str
    slug: str
    sector: str
    city: str
    ats: str
    priority: str
    program: str | None
    why: str


COMPANIES: list[_Co] = [
    _Co(
        "Nebbia Labs",
        "nebbia-labs",
        "AI tooling",
        "Milano",
        "greenhouse",
        "Alta",
        "Summer and spring internships",
        "Small team building LLM evaluation tools.",
    ),
    _Co(
        "Corvo Analytics",
        "corvo-analytics",
        "Data analytics",
        "Amsterdam",
        "lever",
        "Alta",
        "Analytics internship (6 months)",
        "Product analytics for retail, strong data culture.",
    ),
    _Co(
        "Lumen Ventures",
        "lumen-ventures",
        "Venture capital",
        "Berlin",
        "ashby",
        "Media",
        "Investment analyst internship",
        "Early-stage fund with a data-driven sourcing team.",
    ),
    _Co(
        "Brina AI",
        "brina-ai",
        "Applied AI",
        "Torino",
        "ashby",
        "Alta",
        "Applied AI internship",
        "Computer vision for manufacturing quality control.",
    ),
    _Co(
        "Fiordo Consulting",
        "fiordo-consulting",
        "Strategy consulting",
        "Paris",
        "smartrecruiters",
        "Media",
        "Visiting associate internship",
        "Boutique strategy firm with a digital practice.",
    ),
    _Co(
        "Pianura Data",
        "pianura-data",
        "Data engineering",
        "Bologna",
        "greenhouse",
        "Media",
        "Graduate programme",
        "Modern data stack consultancy for mid-size companies.",
    ),
    _Co(
        "Ventaglio Pay",
        "ventaglio-pay",
        "Fintech",
        "Madrid",
        "smartrecruiters",
        "Bassa",
        None,
        "Payments scale-up, risk and fraud analytics team.",
    ),
    _Co(
        "Grecale Mobility",
        "grecale-mobility",
        "Mobility",
        "London",
        "lever",
        "Media",
        "Operations internship",
        "Shared mobility operator, strong operations analytics.",
    ),
    _Co(
        "Tramonto Energy",
        "tramonto-energy",
        "Energy transition",
        "Rotterdam",
        "greenhouse",
        "Media",
        "Business analyst traineeship",
        "Grid flexibility software, mission-driven.",
    ),
    _Co(
        "Ostro Robotics",
        "ostro-robotics",
        "Robotics",
        "Munich",
        "lever",
        "Bassa",
        "Working student and internships",
        "Warehouse robotics, AI product team.",
    ),
]


@dataclass(frozen=True)
class _P:
    key: str
    co: int  # index into COMPANIES
    title: str
    city: str
    cc: str
    type: str
    area: str
    source: str
    priority: str | None  # None = unscored
    filter_state: str = "pass"
    deadline: int | None = None  # days from today
    status: str = "open"
    user_status: str = "todo"
    age: int = 10  # days since first seen; 0 = new in the latest run, 1 = previous run


POSTINGS: list[_P] = [
    # new in the latest run (age 0)
    _P(
        "nebbia_ml",
        0,
        "Machine Learning Intern",
        "Milano",
        "IT",
        "Stage",
        "AI/ML",
        "greenhouse",
        "Alta",
        deadline=9,
        age=0,
    ),
    _P(
        "corvo_ds",
        1,
        "Data Science Intern",
        "Amsterdam",
        "NL",
        "Stage",
        "Data Science",
        "lever",
        "Alta",
        deadline=12,
        age=0,
    ),
    _P(
        "lumen_ops",
        2,
        "Platform and Data Intern",
        "Berlin",
        "DE",
        "Stage",
        "Data",
        "ashby",
        "Media",
        deadline=20,
        age=0,
    ),
    _P(
        "grecale_strat",
        7,
        "Strategy and Pricing Intern",
        "London",
        "GB",
        "Stage",
        "Strategy",
        "linkedin",
        None,
        age=0,
    ),
    _P(
        "tramonto_ml",
        8,
        "Forecasting Intern (Machine Learning)",
        "Rotterdam",
        "NL",
        "Stage",
        "AI/ML",
        "greenhouse",
        "Alta",
        filter_state="flagged",
        deadline=6,
        age=0,
    ),
    _P(
        "ostro_senior",
        9,
        "Senior Perception Engineer",
        "Munich",
        "DE",
        "Lavoro",
        "AI/ML",
        "lever",
        None,
        filter_state="rejected",
        age=0,
    ),
    # previous run (age 1)
    _P(
        "corvo_analyst",
        1,
        "Data Analyst Intern",
        "Amsterdam",
        "NL",
        "Stage",
        "Analytics",
        "lever",
        "Alta",
        deadline=5,
        age=1,
    ),
    _P(
        "brina_applied",
        3,
        "Applied AI Intern",
        "Torino",
        "IT",
        "Stage",
        "AI/ML",
        "ashby",
        "Alta",
        deadline=11,
        age=1,
    ),
    _P(
        "fiordo_digital",
        4,
        "Digital Strategy Intern",
        "Paris",
        "FR",
        "Stage",
        "Consulting",
        "smartrecruiters",
        "Media",
        deadline=3,
        age=1,
    ),
    _P(
        "pianura_ae",
        5,
        "Analytics Engineering Graduate",
        "Bologna",
        "IT",
        "Graduate",
        "Data",
        "greenhouse",
        "Media",
        deadline=30,
        age=1,
    ),
    # older, open
    _P(
        "nebbia_research",
        0,
        "Research Engineering Intern (Evaluation)",
        "Milano",
        "IT",
        "Stage",
        "AI/ML",
        "greenhouse",
        "Alta",
        deadline=18,
        age=6,
    ),
    _P(
        "nebbia_product",
        0,
        "Product Analyst Intern",
        "Milano",
        "IT",
        "Stage",
        "Product",
        "linkedin",
        "Media",
        age=8,
    ),
    _P(
        "corvo_bi",
        1,
        "Business Intelligence Intern",
        "Utrecht",
        "NL",
        "Stage",
        "Analytics",
        "lever",
        "Media",
        deadline=25,
        age=9,
    ),
    _P(
        "lumen_invest",
        2,
        "Investment Analyst Intern",
        "Berlin",
        "DE",
        "Stage",
        "Finance",
        "ashby",
        "Media",
        filter_state="flagged",
        deadline=8,
        age=4,
    ),
    _P(
        "lumen_platform",
        2,
        "Venture Platform Intern",
        "Berlin",
        "DE",
        "Stage",
        "Operations",
        "manual",
        "Bassa",
        age=12,
    ),
    _P(
        "brina_cv",
        3,
        "Computer Vision Intern",
        "Torino",
        "IT",
        "Stage",
        "AI/ML",
        "ashby",
        "Alta",
        deadline=40,
        age=7,
    ),
    _P(
        "brina_mlops",
        3,
        "MLOps Graduate Engineer",
        "Milano",
        "IT",
        "Graduate",
        "AI/ML",
        "linkedin",
        "Media",
        filter_state="overridden",
        age=5,
    ),
    _P(
        "fiordo_analyst",
        4,
        "Business Analyst Intern",
        "Lyon",
        "FR",
        "Stage",
        "Consulting",
        "smartrecruiters",
        "Media",
        filter_state="flagged",
        deadline=13,
        age=10,
    ),
    _P(
        "fiordo_fr",
        4,
        "Consultant Junior (stage)",
        "Paris",
        "FR",
        "Stage",
        "Consulting",
        "smartrecruiters",
        None,
        filter_state="rejected",
        age=11,
    ),
    _P(
        "pianura_de",
        5,
        "Data Engineering Intern",
        "Bologna",
        "IT",
        "Stage",
        "Data",
        "greenhouse",
        "Alta",
        deadline=16,
        age=14,
    ),
    _P(
        "pianura_remote",
        5,
        "Data Quality Intern",
        "Remote, Italy",
        "IT",
        "Stage",
        "Data",
        "greenhouse",
        "Bassa",
        age=15,
    ),
    _P(
        "ventaglio_fraud",
        6,
        "Fraud Analytics Intern",
        "Madrid",
        "ES",
        "Stage",
        "Risk",
        "smartrecruiters",
        "Media",
        deadline=10,
        age=6,
    ),
    _P(
        "ventaglio_bcn",
        6,
        "Growth Analyst Intern",
        "Barcelona",
        "ES",
        "Stage",
        "Marketing",
        "linkedin",
        "Bassa",
        age=9,
    ),
    _P(
        "ventaglio_es",
        6,
        "Analista de Datos en Prácticas",
        "Madrid",
        "ES",
        "Stage",
        "Data",
        "smartrecruiters",
        None,
        filter_state="rejected",
        age=13,
    ),
    _P(
        "grecale_data",
        7,
        "Data Science Intern (Demand)",
        "London",
        "GB",
        "Stage",
        "Data Science",
        "lever",
        "Alta",
        deadline=14,
        age=3,
    ),
    _P(
        "grecale_ops2",
        7,
        "Operations Analytics Graduate",
        "Manchester",
        "GB",
        "Graduate",
        "Operations",
        "lever",
        "Bassa",
        deadline=35,
        age=20,
    ),
    _P(
        "tramonto_policy",
        8,
        "Energy Markets Intern",
        "Amsterdam",
        "NL",
        "Stage",
        "Energy",
        "greenhouse",
        "Media",
        deadline=22,
        age=8,
    ),
    _P(
        "tramonto_de",
        8,
        "Werkstudent Data (m/w/d)",
        "Rotterdam",
        "NL",
        "Stage",
        "Data",
        "linkedin",
        None,
        filter_state="flagged",
        age=4,
    ),
    _P(
        "ostro_ai",
        9,
        "AI Product Intern",
        "Munich",
        "DE",
        "Stage",
        "Product",
        "lever",
        "Media",
        deadline=4,
        age=5,
    ),
    _P(
        "ostro_sim",
        9,
        "Simulation Intern",
        "Hamburg",
        "DE",
        "Stage",
        "Engineering",
        "lever",
        "Bassa",
        filter_state="flagged",
        age=17,
    ),
    _P(
        "ostro_phd",
        9,
        "Robotics Research Scientist (PhD)",
        "Munich",
        "DE",
        "Lavoro",
        "Research",
        "lever",
        None,
        filter_state="rejected",
        age=16,
    ),
    _P(
        "corvo_mkt",
        1,
        "Marketing Analytics Intern",
        "Amsterdam",
        "NL",
        "Stage",
        "Marketing",
        "lever",
        "Bassa",
        user_status="skipped",
        age=18,
    ),
    # closed (two in the latest run, one in the previous run)
    _P(
        "lumen_closed",
        2,
        "Portfolio Analyst Intern",
        "Berlin",
        "DE",
        "Stage",
        "Finance",
        "ashby",
        "Media",
        status="closed",
        age=21,
    ),
    _P(
        "fiordo_closed",
        4,
        "Operations Strategy Intern",
        "Paris",
        "FR",
        "Stage",
        "Consulting",
        "smartrecruiters",
        "Alta",
        status="closed",
        age=19,
    ),
    _P(
        "ventaglio_closed",
        6,
        "Risk Analytics Intern",
        "Madrid",
        "ES",
        "Stage",
        "Risk",
        "smartrecruiters",
        "Media",
        status="closed",
        user_status="sent",
        age=45,
    ),
    # already applied
    _P(
        "pianura_sent",
        5,
        "Data Engineering Intern (Spring)",
        "Bologna",
        "IT",
        "Stage",
        "Data",
        "greenhouse",
        "Alta",
        user_status="sent",
        age=25,
    ),
    _P(
        "tramonto_sent",
        8,
        "Business Analyst Intern",
        "Rotterdam",
        "NL",
        "Stage",
        "Business",
        "greenhouse",
        "Media",
        user_status="sent",
        age=22,
    ),
    _P(
        "corvo_sent",
        1,
        "Product Analytics Intern",
        "Amsterdam",
        "NL",
        "Stage",
        "Analytics",
        "lever",
        "Alta",
        user_status="sent",
        age=30,
    ),
    _P(
        "lumen_sent",
        2,
        "Investment Team Intern",
        "Berlin",
        "DE",
        "Stage",
        "Finance",
        "ashby",
        "Alta",
        user_status="sent",
        age=24,
    ),
    _P(
        "ostro_sent",
        9,
        "Robotics Product Intern",
        "Munich",
        "DE",
        "Stage",
        "Product",
        "lever",
        "Media",
        user_status="sent",
        age=3,
    ),
]

# rule_id, action, reason, evidence
_HITS: dict[str, list[tuple[str, str, str, str]]] = {
    "tramonto_ml": [
        (
            "start_date",
            "flag",
            "Start date may be before availability",
            "Expected start: January 2027",
        )
    ],
    "lumen_invest": [
        (
            "language",
            "flag",
            "German listed as a plus",
            "German is a plus, English is our working language",
        )
    ],
    "fiordo_analyst": [
        ("language", "flag", "French may be required", "Good command of French is appreciated")
    ],
    "tramonto_de": [
        ("language", "flag", "Dutch or German may be required", "Je spreekt Nederlands of Duits")
    ],
    "ostro_sim": [
        ("duration", "flag", "Duration longer than 6 months", "Duration: 9 to 12 months")
    ],
    "ostro_senior": [
        (
            "experience_years",
            "reject",
            "Asks for 3+ years of experience",
            "At least 5 years of experience in perception systems",
        )
    ],
    "fiordo_fr": [
        (
            "language",
            "reject",
            "Native-level French required",
            "Français courant (niveau natif) indispensable",
        )
    ],
    "ventaglio_es": [
        (
            "language",
            "reject",
            "Native-level Spanish required",
            "Nivel nativo de español imprescindible",
        )
    ],
    "ostro_phd": [
        (
            "degree",
            "reject",
            "Requires a completed PhD",
            "You hold a PhD in Robotics or a related field",
        )
    ],
    "brina_mlops": [
        (
            "experience_years",
            "reject",
            "Asks for 2+ years of experience",
            "2+ years of experience with production ML systems",
        )
    ],
}

_WHY = {
    "Alta": (
        "Strong match: {area} internship that uses Python, SQL and ML, in a city on the "
        "shortlist, start window compatible with February 2027 (flexible until April)."
    ),
    "Media": (
        "Reasonable match: {area} role with some overlap with the profile; the team "
        "and location are interesting."
    ),
    "Bassa": ("Weak match: {area} role only loosely related to data and AI work."),
}
_GAPS = {
    "Alta": "Nothing major; check the exact start date with the recruiter.",
    "Media": "Mostly business tasks, limited hands-on modelling.",
    "Bassa": "Different focus from the target roles and an unclear mentoring setup.",
}


# Skip reasons are stored as a "[Saltato: reason]" note prefix (see radar/api/routes/skip.py).
_NOTES: dict[str, str] = {
    "corvo_bi": "Ask about remote Fridays.",
    "corvo_mkt": "[Saltato: Fuori target] Marketing role, not analytics.",
}


def _cover_letters() -> dict[str, str]:
    nebbia = f"""{AVAILABILITY_LINE}

Dear Nebbia Labs team,

I am applying for the Machine Learning Intern role in Milan. I am finishing an MSc in Data \
Science, and my thesis compares evaluation methods for small language models on Italian text. \
Your work on evaluation tooling is the reason I am writing to you specifically.

For my thesis I built an evaluation harness in Python that runs 40 benchmark tasks across six \
open models and reports confidence intervals instead of single scores. I versioned the \
datasets, wrote the tests and documented each design choice so that my lab could reuse it.

In a group project with a logistics partner I trained a gradient boosting model to forecast \
late deliveries and presented the results to a non-technical audience. That taught me to \
explain trade-offs in plain words, which matters in a small team that ships tools to other \
engineers.

I would bring solid Python, PyTorch and SQL, careful experiment tracking and the habit of \
writing things down. I speak Italian and English fluently.

Thank you for your time. I would be glad to talk about how I could help the evaluation team.

Best regards"""
    corvo = f"""{AVAILABILITY_LINE}

Dear Corvo Analytics hiring team,

I would like to apply for the Data Analyst Intern position in Amsterdam, in the \
[CHECK: confirm the team name, the posting says Insights but the careers page says Retail \
Analytics] team.

During my MSc in Data Science I analysed two years of e-commerce orders for a course project: \
I cleaned the data in SQL, built a cohort retention model in Python and turned the results \
into a short dashboard that the course partner still uses. I enjoy the part where numbers \
become a decision someone can act on.

I also worked part-time as a teaching assistant for an introductory statistics course, where \
I explained regression and A/B testing to more than 80 students. It made me better at \
checking my own assumptions before presenting results.

Your focus on product analytics for retail is close to what I want to learn next, and the \
six-month format fits my plans well. I am comfortable in English and happy to relocate to the \
Netherlands.

Thank you for considering my application.

Kind regards"""
    brina = f"""{AVAILABILITY_LINE}

Dear Brina AI team,

I would like to be considered for the Applied AI Intern role in Turin. Computer vision for quality \
control is a field where a model only matters if it works on the factory floor, and that \
practical angle is what draws me to your team.

In my MSc I trained a defect detection model on a public dataset of metal surfaces, reaching \
a clear improvement over the baseline by fixing label noise rather than adding layers. I \
packaged the model with a small FastAPI service and wrote a short guide on how to retrain it \
with new images.

Before my master's I spent a summer in a family-run manufacturing company, where I helped \
digitise inspection logs. I saw how operators use (and sometimes ignore) software, and I \
would bring that respect for the end user to your projects.

I work well with Python, PyTorch, OpenCV and Docker, and I am used to writing clear reports \
in Italian and English.

Thank you for reading. I would welcome the chance to discuss the role.

Best regards"""
    return {"nebbia_ml": nebbia, "corvo_analyst": corvo, "brina_applied": brina}


_FORM_ANSWERS = [
    {
        "question": "Why do you want to join us?",
        "answer": "The team works on problems I studied in my thesis, with real users.",
    },
    {"question": "When can you start?", "answer": AVAILABILITY_LINE},
    {"question": "Do you need a visa to work in the EU?", "answer": "No, I am an EU citizen."},
]


def _raw_text(p: _P, co: _Co) -> str:
    hits = _HITS.get(p.key, [])
    lines = [
        f"{co.name} is looking for a {p.title} to join the team in {p.city}.",
        f"You will work on {p.area.lower()} projects with experienced colleagues.",
        "Requirements: studies in a quantitative field, Python and SQL, curiosity.",
    ]
    lines += [h[3] + "." for h in hits]
    lines.append("This is a fictional demo posting.")
    return "\n".join(lines)


def _first_seen(p: _P, now: datetime, run1_at: datetime, run2_at: datetime) -> datetime:
    if p.age == 0:
        return run2_at + timedelta(minutes=2)
    if p.age == 1:
        return run1_at + timedelta(minutes=2)
    return now - timedelta(days=p.age, hours=3)


def _run_stats(
    sources: dict[str, tuple[int, int, int]],
    *,
    new: int,
    changed: int,
    closed: int,
    rejected: int,
    flagged: int,
    scored: int,
    packs: int,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    """Same shape as radar.pipeline.run writes (counts only)."""
    failed = {e["source"] for e in errors}
    return {
        "sources": {
            name: {"boards": b, "fetched": f, "new": n, "errors": int(name in failed)}
            for name, (b, f, n) in sources.items()
        },
        "new": new,
        "changed": changed,
        "closed": closed,
        "reopened": 0,
        "rejected": rejected,
        "flagged": flagged,
        "llm": {
            "scoring": {
                "scored": scored,
                "input_tokens": 2300 * scored,
                "output_tokens": 180 * scored,
            },
            "packs": {"built": packs, "input_tokens": 4500 * packs, "output_tokens": 1300 * packs},
        },
        "errors": errors,
    }


def load_demo(session: Session) -> dict[str, int]:
    """Insert the fictional dataset. Returns counts; ``{"skipped": 1}`` if already loaded."""
    if session.exec(select(Company).where(Company.name == COMPANIES[0].name)).first():
        return {"skipped": 1}

    now = utcnow()
    today: date = now.date()
    counts: dict[str, int] = {}

    def bump(key: str, n: int = 1) -> None:
        counts[key] = counts.get(key, 0) + n

    # --- runs ------------------------------------------------------------------------
    run1_at = now - timedelta(days=1, hours=2)
    run2_at = now - timedelta(hours=2)
    run1 = Run(
        started_at=run1_at,
        finished_at=run1_at + timedelta(minutes=6),
        status="ok",
        stats=_run_stats(
            {"greenhouse": (12, 142, 2), "lever": (6, 98, 1), "ashby": (8, 61, 1)},
            new=4,
            changed=1,
            closed=1,
            rejected=0,
            flagged=0,
            scored=4,
            packs=2,
            errors=[],
        ),
        report_md=(
            "# Radar run (demo)\n\n- Collected 386 postings from 5 sources\n"
            "- 4 new, 1 changed, 1 closed\n- 4 scored, 2 application packs ready\n"
        ),
    )
    run2 = Run(
        started_at=run2_at,
        finished_at=run2_at + timedelta(minutes=7),
        status="partial",
        stats=_run_stats(
            {
                "greenhouse": (12, 150, 3),
                "lever": (6, 101, 1),
                "ashby": (8, 63, 2),
                "linkedin": (1, 0, 0),
            },
            new=6,
            changed=1,
            closed=2,
            rejected=1,
            flagged=1,
            scored=4,
            packs=1,
            errors=[{"source": "linkedin", "error": "SourceError (HTTP 429)"}],
        ),
        report_md=(
            "# Radar run (demo)\n\n- Collected 401 postings from 5 sources (1 source error)\n"
            "- 6 new (1 rejected by filters, 1 flagged), 1 changed, 2 closed\n"
            "- 4 scored: 3 Alta, 1 Media\n- 1 application pack ready for review\n"
            "- 4 outreach drafts waiting for approval\n"
        ),
    )
    session.add(run1)
    session.add(run2)
    session.flush()
    assert run1.id is not None and run2.id is not None
    bump("runs", 2)

    # --- companies -------------------------------------------------------------------
    companies: list[Company] = []
    for co in COMPANIES:
        c = Company(
            name=co.name,
            sector=co.sector,
            city=co.city,
            careers_url=f"https://example.com/careers/{co.slug}",
            program=co.program,
            why=co.why,
            notes="Fictional demo company.",
            priority=co.priority,
            ats=co.ats,
            ats_slug=co.slug.replace("-", ""),
            active=co.slug != "ventaglio-pay",
            created_at=now - timedelta(days=60),
        )
        session.add(c)
        companies.append(c)
    session.flush()
    bump("companies", len(companies))

    # --- postings, filter hits, scores, "new" events ---------------------------------
    postings: dict[str, Posting] = {}
    for n, p in enumerate(POSTINGS, start=1):
        co = COMPANIES[p.co]
        company = companies[p.co]
        raw = _raw_text(p, co)
        url = f"https://example.com/jobs/{co.slug}/{n:03d}?utm_source=demo"
        seen = _first_seen(p, now, run1_at, run2_at)
        closed_at = None
        if p.status == "closed":
            closed_at = run1_at if p.key == "ventaglio_closed" else run2_at
        posting = Posting(
            uid=f"{p.source}:demo-{n:03d}",
            source=p.source,
            external_id=f"demo-{n:03d}",
            url=url,
            canonical_url=canonical_url(url),
            title=p.title,
            company_name=co.name,
            company_id=company.id,
            location=p.city,
            country=p.cc,
            country_code=p.cc,
            zone=zone_for(p.cc),
            type=p.type,
            area=p.area,
            start_info="February-April 2027" if p.type == "Stage" else None,
            deadline=today + timedelta(days=p.deadline) if p.deadline is not None else None,
            via="LinkedIn" if p.source == "linkedin" else "Sito aziendale",
            raw_text=raw,
            content_hash=content_hash(p.title, co.name, p.city, raw),
            dedupe_key=dedupe_key(co.name, p.title, p.cc),
            first_seen=seen,
            last_seen=closed_at or run2_at,
            status=p.status,
            closed_at=closed_at,
            filter_state=p.filter_state,
            user_status=p.user_status,
            note=_NOTES.get(p.key),
        )
        session.add(posting)
        session.flush()
        assert posting.id is not None
        postings[p.key] = posting
        bump("postings")

        for rule_id, action, reason, evidence in _HITS.get(p.key, []):
            session.add(
                FilterResult(
                    posting_id=posting.id,
                    rule_id=rule_id,
                    action=action,
                    reason=reason,
                    evidence=evidence,
                    overridden=p.filter_state == "overridden",
                    created_at=seen,
                )
            )
            bump("filter_results")

        if p.priority is not None:
            if p.key == "brina_cv":  # re-scored: the latest score wins
                session.add(_score(posting, "Media", seen, p.area, confidence=0.55))
                bump("scores")
            session.add(_score(posting, p.priority, seen + timedelta(minutes=5), p.area))
            bump("scores")

        run_id = run2.id if p.age == 0 else run1.id if p.age == 1 else None
        session.add(ChangeEvent(posting_id=posting.id, run_id=run_id, kind="new", at=seen))
        bump("change_events")

    # duplicate: the same Nebbia posting seen on LinkedIn (hidden from lists)
    original = postings["nebbia_ml"]
    dup_url = "https://example.com/jobs/linkedin/nebbia-labs-ml-intern"
    dup = Posting(
        uid="linkedin:demo-dup-001",
        source="linkedin",
        external_id="demo-dup-001",
        url=dup_url,
        canonical_url=canonical_url(dup_url),
        title=original.title,
        company_name=original.company_name,
        company_id=original.company_id,
        location=original.location,
        country_code=original.country_code,
        zone=original.zone,
        type=original.type,
        area=original.area,
        deadline=original.deadline,
        raw_text=original.raw_text,
        content_hash=original.content_hash,
        dedupe_key=original.dedupe_key,
        duplicate_of=original.id,
        first_seen=original.first_seen,
        last_seen=original.last_seen,
    )
    session.add(dup)
    bump("postings")

    # --- other change events -----------------------------------------------------------
    for key, run, at in (
        ("lumen_closed", run2, run2_at),
        ("fiordo_closed", run2, run2_at),
        ("ventaglio_closed", run1, run1_at),
    ):
        session.add(
            ChangeEvent(
                posting_id=_pid(postings[key]),
                run_id=run.id,
                kind="closed",
                changes={"status": ["open", "closed"]},
                at=at + timedelta(minutes=3),
            )
        )
        bump("change_events")
    deadline = postings["pianura_ae"].deadline
    session.add(
        ChangeEvent(
            posting_id=_pid(postings["pianura_ae"]),
            run_id=run2.id,
            kind="changed",
            changes={"deadline": [str((deadline or today) - timedelta(days=10)), str(deadline)]},
            at=run2_at + timedelta(minutes=4),
        )
    )
    session.add(
        ChangeEvent(
            posting_id=_pid(postings["grecale_ops2"]),
            run_id=run1.id,
            kind="reopened",
            changes={"status": ["closed", "open"]},
            at=run1_at + timedelta(minutes=4),
        )
    )
    bump("change_events", 2)

    # --- packs, contacts, drafts ---------------------------------------------------------
    letters = _cover_letters()
    packs: dict[str, ApplicationPack] = {}
    for key, letter in letters.items():
        posting = postings[key]
        created = posting.first_seen + timedelta(minutes=6)
        pack = ApplicationPack(
            posting_id=_pid(posting),
            content_hash=posting.content_hash,
            cover_letter=letter,
            form_answers=_FORM_ANSWERS,
            cv_version="CV v2" if key == "corvo_analyst" else "CV v1",
            apply_link=posting.url,
            language="en",
            style_issues=["Unresolved [CHECK] marker"] if "[CHECK:" in letter else [],
            provider="groq",
            model=DRAFT_MODEL,
            created_at=created,
            updated_at=created,
        )
        session.add(pack)
        packs[key] = pack
        bump("packs")
    session.flush()

    contact_specs = [
        ("nebbia_ml", "Elena Fittizia", "ML Engineering Lead", "Leads the evaluation team."),
        ("nebbia_ml", "Marco Esempio", "Talent Partner", "Recruiter for internships."),
        ("corvo_analyst", "Sofie Voorbeeld", "Analytics Manager", "Hiring manager in posting."),
        ("brina_applied", "Luca Immaginario", "Head of Applied AI", "Team lead for the role."),
        ("lumen_invest", "Lukas Beispiel", "Investment Associate", "Alumnus of my university."),
        ("pianura_sent", "Giulia Prova", "People Partner", "Recruiter for the graduate track."),
    ]
    contacts: list[Contact] = []
    for key, name, role, why in contact_specs:
        posting = postings[key]
        contact = Contact(
            posting_id=_pid(posting),
            company_id=posting.company_id,
            name=name,
            role=role,
            profile_url=f"https://example.com/in/{name.lower().replace(' ', '-')}",
            why=why + " (fictional demo contact)",
        )
        session.add(contact)
        contacts.append(contact)
    session.flush()
    bump("contacts", len(contacts))

    # (posting key, contact index, kind, channel, status, text, style_issues)
    draft_specs: list[tuple[str, int | None, str, str, str, str, list[str]]] = [
        (
            "nebbia_ml",
            0,
            "linkedin_connection_note",
            "linkedin",
            "draft",
            "Hi Elena, I am an MSc Data Science student working on LLM evaluation for my thesis. "
            "I just applied for the ML Intern role at Nebbia Labs and would love to follow your "
            "team's work. Thank you!",
            [],
        ),
        (
            "corvo_analyst",
            2,
            "linkedin_message",
            "linkedin",
            "draft",
            "Hi Sofie, I saw the Data Analyst Intern opening in your team. I recently built a "
            "cohort retention analysis on retail data and would be glad to hear what the team "
            "is focusing on this year. Would you have 10 minutes for a short call?",
            [],
        ),
        (
            "brina_applied",
            3,
            "email",
            "email",
            "draft",
            "Subject: Applied AI Intern application\n\nDear Luca,\n\nI have just applied for the "
            "Applied AI Intern role. My MSc project on surface defect detection is close to your "
            "quality control work, and I would be happy to share the short report.\n\n"
            "Best regards",
            ["Subject line longer than 40 characters"],
        ),
        (
            "pianura_sent",
            5,
            "followup",
            "email",
            "draft",
            "Dear Giulia,\n\nI applied for the Data Engineering Intern (Spring) role three weeks "
            "ago and wanted to confirm that my application arrived. I remain very interested.\n\n"
            "Best regards",
            [],
        ),
        (
            "nebbia_ml",
            1,
            "linkedin_connection_note",
            "linkedin",
            "approved",
            "Hi Marco, I applied for the ML Intern role at Nebbia Labs today. Happy to connect "
            "and to answer any question about my profile.",
            [],
        ),
        (
            "lumen_invest",
            4,
            "linkedin_connection_note",
            "linkedin",
            "sent",
            "Hi Lukas, we studied at the same university. I am interested in the Investment "
            "Analyst Intern role at Lumen Ventures and would be glad to connect.",
            [],
        ),
        (
            "corvo_analyst",
            None,
            "email",
            "email",
            "skipped",
            "Subject: Question about the internship start date\n\nHello, could you confirm "
            "whether a start in March 2027 is possible?\n\nKind regards",
            [],
        ),
    ]
    for i, (key, ci, kind, channel, status, text, issues) in enumerate(draft_specs):
        posting = postings[key]
        d_contact = contacts[ci] if ci is not None else None
        created = run2_at + timedelta(minutes=10 + i)
        d_pack = packs.get(key)
        session.add(
            Draft(
                posting_id=_pid(posting),
                pack_id=d_pack.id if d_pack else None,
                contact_id=d_contact.id if d_contact else None,
                kind=kind,
                channel=channel,
                recipient=d_contact.profile_url if d_contact else "jobs@example.com",
                text=text,
                status=status,
                style_issues=issues,
                send_result="manual" if status == "sent" else None,
                created_at=created,
                updated_at=created,
                approved_at=created if status in ("approved", "sent") else None,
                sent_at=created + timedelta(minutes=30) if status == "sent" else None,
            )
        )
        bump("drafts")

    # --- applications ------------------------------------------------------------------
    # (posting key, company idx, role, city, days ago, channel, cv, stage, responded, next)
    app_specs: list[tuple[str | None, int, str, str, int, str, str, str, bool, str | None]] = [
        (
            "pianura_sent",
            5,
            "Data Engineering Intern (Spring)",
            "Bologna",
            20,
            "Sito aziendale",
            "CV v1",
            "Inviata",
            False,
            None,
        ),
        (
            "tramonto_sent",
            8,
            "Business Analyst Intern",
            "Rotterdam",
            16,
            "LinkedIn",
            "CV v2",
            "Inviata",
            False,
            None,
        ),
        (
            "corvo_sent",
            1,
            "Product Analytics Intern",
            "Amsterdam",
            25,
            "Sito aziendale",
            "CV v2",
            "Screening HR",
            True,
            "Phone screen",
        ),
        (
            None,
            4,
            "Strategy Intern",
            "Paris",
            30,
            "Referral",
            "CV v1",
            "Test/case",
            True,
            "Submit case study",
        ),
        (
            "lumen_sent",
            2,
            "Investment Team Intern",
            "Berlin",
            18,
            "Email",
            "CV v2",
            "Colloquio 1",
            True,
            "Second interview with a partner",
        ),
        (
            "ventaglio_closed",
            6,
            "Risk Analytics Intern",
            "Madrid",
            40,
            "Sito aziendale",
            "CV v1",
            "Rifiutata",
            True,
            None,
        ),
        (
            None,
            7,
            "Operations Analyst Intern",
            "London",
            45,
            "LinkedIn",
            "CV v1",
            "Nessuna risposta",
            False,
            None,
        ),
        (
            "ostro_sent",
            9,
            "Robotics Product Intern",
            "Munich",
            0,
            "Sito aziendale",
            "CV v2",
            "Inviata",
            False,
            None,
        ),
    ]
    for a_key, ci, role, city, ago, channel, cv, stage, responded, next_step in app_specs:
        sent_on = today - timedelta(days=ago)
        session.add(
            Application(
                posting_id=_pid(postings[a_key]) if a_key else None,
                company=COMPANIES[ci].name,
                role=role,
                city=city,
                date_sent=sent_on,
                channel=channel,
                cv_version=cv,
                stage=stage,
                next_step=next_step,
                next_step_date=today + timedelta(days=4) if next_step else None,
                notes="Fictional demo application.",
                responded=responded,
                updated_at=datetime.combine(sent_on, datetime.min.time()).replace(
                    tzinfo=now.tzinfo
                ),
            )
        )
        bump("applications")

    # --- LLM usage ---------------------------------------------------------------------
    usage: list[tuple[Run, str, str, int, int, int]] = [
        (run1, SCORING_MODEL, "scoring", 4, 9_800, 1_450),
        (run1, DRAFT_MODEL, "pack", 2, 7_200, 1_900),
        (run2, SCORING_MODEL, "scoring", 4, 10_300, 1_520),
        (run2, DRAFT_MODEL, "pack", 1, 3_700, 980),
        (run2, DRAFT_MODEL, "draft", 4, 4_100, 720),
    ]
    for run, model, purpose, requests, tin, tout in usage:
        session.add(
            LLMUsage(
                run_id=run.id,
                provider="groq",
                model=model,
                purpose=purpose,
                requests=requests,
                input_tokens=tin,
                output_tokens=tout,
                at=(run.finished_at or now),
            )
        )
        bump("llm_usage")

    session.flush()
    return counts


def _pid(posting: Posting) -> int:
    assert posting.id is not None
    return posting.id


def _score(
    posting: Posting, priority: str, at: datetime, area: str, confidence: float = 0.8
) -> Score:
    langs: list[str] = ["English"]
    if posting.country_code == "IT":
        langs.append("Italian")
    warnings: list[Any] = []
    if posting.deadline is not None:
        warnings.append(f"Deadline on {posting.deadline.isoformat()}")
    return Score(
        posting_id=_pid(posting),
        content_hash=posting.content_hash,
        priority=priority,
        why_fits=_WHY[priority].format(area=area),
        what_doesnt_fit=_GAPS[priority],
        start_window="February-April 2027" if posting.type == "Stage" else None,
        language_requirements=langs,
        warnings=warnings,
        confidence=confidence,
        provider="groq",
        model=SCORING_MODEL,
        input_tokens=2_400,
        output_tokens=360,
        created_at=at,
    )
