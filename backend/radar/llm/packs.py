"""Application packs (cover letter, form answers, outreach drafts) for Alta postings.

One LLM request per pack (all texts in one JSON object, which keeps a pack under the Groq free
tier's per-minute token budget), one repair request if the JSON is invalid, and at most ONE
regeneration if the texts still break the writing rules after ``style.fix``. Remaining issues are
stored in ``style_issues``. Nothing is ever sent: drafts are created with status "draft".

Facts: the only source of candidate facts is the fact bank (``radar.facts``), rendered as
``[id] text`` lines; on Groq only the core facts plus the ones most related to the posting are
sent (``PACK_FACTS_CHARS``). ``config/profile.yaml`` is used here only for the CV versions.
After drafting, a second request (``radar.llm.verify``) checks every claim against the facts;
the result is stored on the pack and on its outreach drafts (``verification``). Texts are never
rewritten by the verifier.

Resume: each pack is committed when built; the next run picks Alta postings that still have no
pack (or whose pack's content_hash is stale).
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlmodel import Session, col, select

from radar import config as radar_config
from radar.facts import FactBank, load_facts, select_facts
from radar.llm import prompts, style, verify
from radar.llm.provider import (
    LLMError,
    LLMProvider,
    LLMResult,
    MissingKeyError,
    RateLimitExhausted,
    get_provider,
    record_usage,
)
from radar.llm.scoring import extract_json
from radar.models import ApplicationPack, Company, Contact, Draft, Posting, Score
from radar.timeutil import today, utcnow

log = logging.getLogger(__name__)

PACK_MAX_TOKENS = 3000
PACK_FACTS_CHARS = 8000  # rendered facts per drafting prompt on Groq/fake (Anthropic: all)
CONTACT_MAX_TOKENS = 900
MAX_CONSECUTIVE_ERRORS = 3
OUTREACH_KINDS = ("linkedin_connection_note", "linkedin_message", "followup")
CONTACT_KINDS = ("linkedin_connection_note", "linkedin_message", "followup", "email")
PACKABLE_FILTER_STATES = ("pass", "flagged", "overridden")

_SALARY_Q = re.compile(r"salar|stipend|retribu|compens|\bral\b|\bpay\b|economic", re.I)
_CURRENCY = r"(?:€|£|\$|\b(?:eur|euro|chf|gbp|usd|sek|dkk|nok|pln|czk)\b)"
_SALARY_IN_POSTING = re.compile(rf"{_CURRENCY}\s?\d|\d[\d.,]*\s?(?:k\s?)?{_CURRENCY}", re.I)
_SALARY_PLACEHOLDER = {
    "en": "[CHECK: salary expectation. The posting states no range: check typical intern pay "
    "for this city before answering]",
    "it": "[CHECK: aspettativa economica. L'annuncio non indica una cifra: verifica i valori "
    "tipici per questa città prima di rispondere]",
}


class FormAnswerOut(BaseModel):
    question: str = Field(min_length=1)
    answer: str = ""


class PackOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cover_letter: str = Field(min_length=40)
    form_answers: list[FormAnswerOut] = Field(min_length=3)
    linkedin_connection_note: str = Field(min_length=10)
    linkedin_message: str = Field(min_length=10)
    followup: str = Field(min_length=10)
    claims: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("claims", mode="before")
    @classmethod
    def _claims(cls, v: Any) -> Any:
        return verify.normalize_drafter_claims(v)

    @field_validator("form_answers", mode="before")
    @classmethod
    def _answers(cls, v: Any) -> Any:
        if isinstance(v, dict):  # {"question": "answer"} form
            return [{"question": k, "answer": str(a)} for k, a in v.items()]
        return v


class StyledPack(BaseModel):
    cover_letter: str
    form_answers: list[dict[str, str]]
    drafts: dict[str, str]
    pack_issues: list[str]
    draft_issues: dict[str, list[str]]
    violations: list[str]  # what would justify a regeneration
    claims: list[dict[str, Any]] = []  # the drafter's own claim -> fact ids mapping


def parse_pack(text: str) -> PackOut:
    try:
        return PackOut.model_validate(extract_json(text))
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
        raise ValueError(f"schema errors in: {fields}") from None


def posting_states_salary(text: str | None) -> bool:
    return bool(text and _SALARY_IN_POSTING.search(text))


def apply_style(pack: PackOut, language: str, posting_text: str | None) -> StyledPack:
    """Run style.fix on every text, guard salary answers, collect issues."""
    pack_issues: list[str] = []
    violations: list[str] = []

    cover, issues = style.fix(pack.cover_letter, "cover_letter", language)
    pack_issues += [f"cover_letter: {i}" for i in issues]
    violations += [f"cover_letter: {i}" for i in style.check(cover, "cover_letter", language)]

    has_salary = posting_states_salary(posting_text)
    answers: list[dict[str, str]] = []
    for n, fa in enumerate(pack.form_answers[:7], start=1):
        question = style.fix_dashes(fa.question).strip()
        answer, issues = style.fix(fa.answer, "form_answer", language)
        if _SALARY_Q.search(question) and not has_salary and "[CHECK" not in answer:
            answer = _SALARY_PLACEHOLDER["it" if language == "it" else "en"]
            pack_issues.append(f"form_answers[{n}]: salary replaced by a [CHECK] placeholder")
        pack_issues += [f"form_answers[{n}]: {i}" for i in issues]
        violations += [
            f"form_answers[{n}]: {i}" for i in style.check(answer, "form_answer", language)
        ]
        answers.append({"question": question, "answer": answer})

    drafts: dict[str, str] = {}
    draft_issues: dict[str, list[str]] = {}
    for kind in OUTREACH_KINDS:
        text, issues = style.fix(getattr(pack, kind), kind, language)
        drafts[kind] = text
        draft_issues[kind] = issues
        violations += [f"{kind}: {i}" for i in style.check(text, kind, language)]
    return StyledPack(
        cover_letter=cover,
        form_answers=answers,
        drafts=drafts,
        pack_issues=pack_issues,
        draft_issues=draft_issues,
        violations=violations,
        claims=pack.claims,
    )


# --------------------------------------------------------------------------- selection


def _latest_scores(session: Session, posting_ids: list[int]) -> dict[int, Score]:
    latest: dict[int, Score] = {}
    if not posting_ids:
        return latest
    rows = session.exec(
        select(Score)
        .where(col(Score.posting_id).in_(posting_ids))
        .order_by(col(Score.created_at), col(Score.id))
    ).all()
    for s in rows:
        latest[s.posting_id] = s
    return latest


def pack_candidates(session: Session, limit: int, posting_id: int | None = None) -> list[Posting]:
    if posting_id is not None:  # explicit request (API "generate now"): no priority filter
        posting = session.get(Posting, posting_id)
        return [posting] if posting is not None else []
    postings = list(
        session.exec(
            select(Posting).where(
                Posting.status == "open",
                col(Posting.filter_state).in_(PACKABLE_FILTER_STATES),
                col(Posting.duplicate_of).is_(None),
                Posting.user_status == "todo",
            )
        ).all()
    )
    ids = [p.id for p in postings if p.id is not None]
    scores = _latest_scores(session, ids)
    packs = (
        {
            pk.posting_id: pk
            for pk in session.exec(
                select(ApplicationPack).where(col(ApplicationPack.posting_id).in_(ids))
            ).all()
        }
        if ids
        else {}
    )
    now = today()
    out: list[Posting] = []
    for p in postings:
        assert p.id is not None
        score = scores.get(p.id)
        if score is None or score.priority != "Alta":
            continue
        if p.deadline is not None and p.deadline < now:
            continue
        pack = packs.get(p.id)
        if pack is not None and pack.content_hash == p.content_hash:
            continue
        out.append(p)
    out.sort(
        key=lambda p: (
            p.deadline is None,
            p.deadline or date.max,
            -(p.similarity or 0.0),
            -p.first_seen.timestamp(),
        )
    )
    return out[:limit]


# --------------------------------------------------------------------------- generation


def _generate(
    provider: LLMProvider,
    system: str,
    user: str,
    results: list[LLMResult],
    language: str,
    posting_text: str | None,
) -> StyledPack | None:
    def call(prompt: str, temperature: float) -> LLMResult:
        r = provider.complete(
            system,
            prompt,
            model=provider.model,
            max_tokens=PACK_MAX_TOKENS,
            temperature=temperature,
            json_mode=True,
        )
        results.append(r)
        return r

    r = call(user, 0.4)
    try:
        parsed = parse_pack(r.text)
    except ValueError as exc:
        r = call(user + prompts.repair_suffix(r.text, str(exc)), 0.2)
        try:
            parsed = parse_pack(r.text)
        except ValueError:
            return None
    styled = apply_style(parsed, language, posting_text)
    if not styled.violations:
        return styled
    previous = json.dumps(parsed.model_dump(), ensure_ascii=False)
    r = call(user + prompts.regenerate_suffix(previous, styled.violations), 0.3)
    try:
        regenerated = apply_style(parse_pack(r.text), language, posting_text)
    except ValueError:
        return styled  # keep the first version, issues recorded
    return regenerated


def facts_for(bank: FactBank, provider: LLMProvider, query: str) -> str:
    """The fact block for a drafting prompt (budgeted unless the provider is Anthropic)."""
    budget = None if provider.name == "anthropic" else PACK_FACTS_CHARS
    facts = select_facts(bank, query, budget or 10**9)
    return prompts.facts_block(bank, facts)


def _store_pack(
    session: Session,
    posting: Posting,
    styled: StyledPack,
    language: str,
    cv_version: str,
    result: LLMResult,
    verification: dict[str, Any] | None = None,
) -> ApplicationPack:
    assert posting.id is not None
    pack = session.exec(
        select(ApplicationPack).where(ApplicationPack.posting_id == posting.id)
    ).first()
    now = utcnow()
    if pack is None:
        pack = ApplicationPack(posting_id=posting.id)
    else:  # stale pack: replace texts and the untouched generic drafts
        old = session.exec(
            select(Draft).where(
                Draft.pack_id == pack.id,
                Draft.status == "draft",
                col(Draft.contact_id).is_(None),
            )
        ).all()
        for d in old:
            session.delete(d)
    pack.content_hash = posting.content_hash
    pack.cover_letter = styled.cover_letter
    pack.form_answers = styled.form_answers
    pack.cv_version = cv_version
    pack.apply_link = posting.url
    pack.language = language
    pack.style_issues = styled.pack_issues
    pack.provider = result.provider
    pack.model = result.model
    pack.updated_at = now
    session.add(pack)
    session.flush()
    for kind in OUTREACH_KINDS:
        session.add(
            Draft(
                posting_id=posting.id,
                pack_id=pack.id,
                kind=kind,
                channel="linkedin",
                recipient=None,
                text=styled.drafts[kind],
                status="draft",
                style_issues=styled.draft_issues[kind],
            )
        )
    session.flush()
    if verification is not None:
        verify.store_pack_verification(session, pack, verification)
    return pack


def build_packs(
    session: Session, run_id: int | None, limit: int, posting_id: int | None = None
) -> dict[str, Any]:
    postings = pack_candidates(session, limit, posting_id)
    stats: dict[str, Any] = {
        "candidates": len(postings),
        "built": 0,
        "invalid": 0,
        "errors": 0,
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "verify_requests": 0,
        "unsupported_claims": 0,
    }
    if posting_id is not None and not postings:
        stats["error"] = "posting_not_found"
        return stats
    if not postings:
        return stats
    try:
        provider = get_provider("draft")
    except MissingKeyError:
        log.warning("packs skipped: missing API key (%d pending)", len(postings))
        return {"skipped": "missing_key", "candidates": len(postings), "built": 0}
    stats["provider"], stats["model"] = provider.name, provider.model

    profile = radar_config.load_yaml_config("profile")  # CV versions only: facts come from the bank
    cv_version = prompts.default_cv_version(profile)
    bank = load_facts()
    verifier = provider if radar_config.get_settings().verify_drafts else None
    pack_ids: list[int] = []
    consecutive_errors = 0
    for posting in postings:
        language = prompts.posting_language(posting)
        company = session.get(Company, posting.company_id) if posting.company_id else None
        system = prompts.pack_system(language, bank)
        context = verify.posting_text(posting)
        user = prompts.pack_user(facts_for(bank, provider, context), posting, company)
        results: list[LLMResult] = []
        try:
            styled = _generate(provider, system, user, results, language, posting.raw_text)
        except RateLimitExhausted:
            stats["stopped"] = "rate_limit"
            break
        except LLMError:
            stats["errors"] += 1
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                stats["stopped"] = "errors"
                break
            continue
        finally:
            for r in results:
                record_usage(session, run_id, "pack", r)
                stats["requests"] += 1
                stats["input_tokens"] += r.input_tokens
                stats["output_tokens"] += r.output_tokens
            if results:
                session.commit()
        consecutive_errors = 0
        if styled is None:
            stats["invalid"] += 1
            continue
        verify_results: list[LLMResult] = []
        verification = verify.verify_texts(
            bank,
            verify.pack_texts(styled.cover_letter, styled.form_answers, styled.drafts),
            provider=verifier,
            posting_text=context,
            results=verify_results,
            drafter_claims=styled.claims,
        )
        for r in verify_results:
            record_usage(session, run_id, "verify", r)
            stats["verify_requests"] += 1
            stats["input_tokens"] += r.input_tokens
            stats["output_tokens"] += r.output_tokens
        stats["unsupported_claims"] += verification["counts"]["unsupported"]
        pack = _store_pack(
            session, posting, styled, language, cv_version, results[-1], verification
        )
        session.commit()
        if pack.id is not None:
            pack_ids.append(pack.id)
        stats["built"] += 1
        if verification.get("error") == "rate_limit":
            stats["stopped"] = "rate_limit"
            break
    if posting_id is not None and pack_ids:
        stats["pack_id"] = pack_ids[0]
    log.info(
        "packs: %d candidates, %d built, %d invalid, %d errors%s",
        stats["candidates"],
        stats["built"],
        stats["invalid"],
        stats["errors"],
        f", stopped ({stats['stopped']})" if "stopped" in stats else "",
    )
    return stats


# --------------------------------------------------------------------------- contact drafts


def _parse_contact(raw: str) -> tuple[str, list[dict[str, Any]]]:
    """(text, drafter claims) from a contact-draft answer."""
    try:
        data = extract_json(raw)
    except ValueError:
        if raw.strip() and "{" not in raw:
            return raw.strip(), []
        raise
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("missing 'text'")
    return text, verify.normalize_drafter_claims(data.get("claims"))


def draft_for_contact(session: Session, posting_id: int, contact_id: int, kind: str) -> Draft:
    """Personalised outreach draft for one Contact. Raises ValueError for unknown ids/kind,
    MissingKeyError / RateLimitExhausted / LLMError from the provider. Flushes, does not commit."""
    if kind not in CONTACT_KINDS:
        raise ValueError(f"unsupported draft kind {kind!r}")
    posting = session.get(Posting, posting_id)
    if posting is None:
        raise ValueError(f"posting {posting_id} not found")
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise ValueError(f"contact {contact_id} not found")
    provider = get_provider("draft")

    bank = load_facts()
    language = prompts.posting_language(posting)
    company = session.get(Company, posting.company_id) if posting.company_id else None
    system = prompts.contact_draft_system(kind, language, bank)
    facts_text = facts_for(bank, provider, verify.posting_text(posting))
    user = prompts.contact_draft_user(facts_text, posting, company, contact)

    results: list[LLMResult] = []

    def call(prompt: str, temperature: float) -> LLMResult:
        r = provider.complete(
            system,
            prompt,
            model=provider.model,
            max_tokens=CONTACT_MAX_TOKENS,
            temperature=temperature,
            json_mode=True,
        )
        results.append(r)
        return r

    try:
        r = call(user, 0.4)
        try:
            raw_text, claims = _parse_contact(r.text)
        except ValueError as exc:
            r = call(user + prompts.repair_suffix(r.text, str(exc)), 0.2)
            try:
                raw_text, claims = _parse_contact(r.text)
            except ValueError as exc2:
                raise LLMError("draft: model output is not valid") from exc2
        text, issues = style.fix(raw_text, kind, language)
        remaining = style.check(text, kind, language)
        if remaining:
            r = call(user + prompts.regenerate_suffix(json.dumps({"text": text}), remaining), 0.3)
            with contextlib.suppress(ValueError):  # else keep the first version and its issues
                regenerated, new_claims = _parse_contact(r.text)
                text, issues = style.fix(regenerated, kind, language)
                claims = new_claims or claims
    finally:
        for res in results:
            record_usage(session, None, "draft", res)

    pack = session.exec(
        select(ApplicationPack).where(ApplicationPack.posting_id == posting_id)
    ).first()
    channel = "email" if kind == "email" else "linkedin"
    draft = Draft(
        posting_id=posting_id,
        pack_id=pack.id if pack else None,
        contact_id=contact_id,
        kind=kind,
        channel=channel,
        recipient=contact.profile_url if channel == "linkedin" else None,
        text=text,
        status="draft",
        style_issues=issues,
    )
    verifier = provider if radar_config.get_settings().verify_drafts else None
    verify.verify_draft(
        session,
        draft,
        provider=verifier,
        use_llm=verifier is not None,
        bank=bank,
        drafter_claims=claims,
    )
    session.add(draft)
    session.flush()
    return draft


def draft_outreach_for_contact(
    session: Session,
    posting_id: int,
    contact_id: int,
    kinds: tuple[str, ...] = ("linkedin_connection_note", "linkedin_message"),
) -> list[Draft]:
    """Connection note (<= 300 chars, checked by style) and message for one contact, via
    ``draft_for_contact``. Drafts only (status "draft"): sending stays with ``radar send``."""
    return [draft_for_contact(session, posting_id, contact_id, kind) for kind in kinds]
