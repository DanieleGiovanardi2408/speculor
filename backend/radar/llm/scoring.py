"""LLM scoring of postings that survived the deterministic filters.

One posting per request (see the module report: tokens per minute/day are the binding Groq
free-tier limits, not requests, so batching saves little and couples failures).

Cache by content: a posting is scored only if no Score row exists with its content_hash (manual
seed scores count). Resume is therefore automatic: each Score is committed as soon as it exists,
and the next run picks up whatever is still unscored.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import exists, func
from sqlmodel import Session, col, select

from radar import config as radar_config
from radar.llm import guards, prompts, style
from radar.llm.provider import (
    LLMError,
    LLMProvider,
    LLMResult,
    MissingKeyError,
    RateLimitExhausted,
    get_provider,
    record_usage,
)
from radar.llm.relevance import Relevance
from radar.models import Company, FilterResult, Posting, Score

log = logging.getLogger(__name__)

SCOREABLE_FILTER_STATES = ("pass", "flagged", "overridden")
SCORING_MAX_TOKENS = 1200  # includes the short hidden reasoning of gpt-oss models
MAX_CONSECUTIVE_ERRORS = 3

_PRIORITY_ALIASES = {
    "alta": "Alta",
    "high": "Alta",
    "media": "Media",
    "medium": "Media",
    "bassa": "Bassa",
    "low": "Bassa",
}


def first_sentences(text: str, n: int = 2) -> str:
    text = " ".join(text.split())
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:n]).strip()


class ScoreOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    priority: Literal["Alta", "Media", "Bassa"]
    why_fits: str = Field(min_length=1)
    what_doesnt_fit: str = ""
    start_window: str | None = None
    language_requirements: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("priority", mode="before")
    @classmethod
    def _priority(cls, v: Any) -> Any:
        return _PRIORITY_ALIASES.get(str(v).strip().lower(), v) if isinstance(v, str) else v

    @field_validator("why_fits", "what_doesnt_fit", mode="before")
    @classmethod
    def _two_sentences(cls, v: Any) -> Any:
        if v is None:
            return ""
        if isinstance(v, str):
            return first_sentences(style.fix_dashes(v), 2)
        return v

    @field_validator("start_window", mode="before")
    @classmethod
    def _start(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().lower() in ("", "null", "none", "n/a", "unknown"):
            return None
        return style.fix_dashes(v) if isinstance(v, str) else v

    @field_validator("language_requirements", "warnings", mode="before")
    @classmethod
    def _list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        if isinstance(v, list):
            return [style.fix_dashes(str(x)) for x in v if str(x).strip()]
        return v

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip().rstrip("%")
            try:
                v = float(v)
            except ValueError:
                return v
        if isinstance(v, int | float) and 1 < v <= 100:
            return v / 100
        return v


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output (tolerates code fences and surrounding prose)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found") from None
        try:
            data = json.loads(t[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc.msg}") from None
    if not isinstance(data, dict):
        raise ValueError("JSON is not an object")
    return data


def parse_score(text: str) -> ScoreOut:
    try:
        return ScoreOut.model_validate(extract_json(text))
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
        raise ValueError(f"schema errors in: {fields}") from None


def pending_postings(session: Session, limit: int) -> list[Posting]:
    """Open, non-rejected, non-duplicate postings whose content was never scored."""
    already_scored = exists().where(col(Score.content_hash) == col(Posting.content_hash))
    stmt = (
        select(Posting)
        .where(
            Posting.status == "open",
            col(Posting.filter_state).in_(SCOREABLE_FILTER_STATES),
            col(Posting.duplicate_of).is_(None),
            Posting.content_hash != "",
            ~already_scored,
        )
        .order_by(
            func.date(col(Posting.first_seen)).desc(),
            col(Posting.deadline).is_(None),
            col(Posting.deadline).asc(),
            col(Posting.id).desc(),
        )
        .limit(limit)
    )
    return list(session.exec(stmt).all())


def _score_one(
    provider: LLMProvider, system: str, user: str, results: list[LLMResult]
) -> ScoreOut | None:
    """One request plus at most one repair request. Returns None if still invalid."""
    r = provider.complete(
        system,
        user,
        model=provider.model,
        max_tokens=SCORING_MAX_TOKENS,
        temperature=0.1,
        json_mode=True,
    )
    results.append(r)
    try:
        return parse_score(r.text)
    except ValueError as exc:
        error = str(exc)
    r2 = provider.complete(
        system,
        user + prompts.repair_suffix(r.text, error),
        model=provider.model,
        max_tokens=SCORING_MAX_TOKENS,
        temperature=0.0,
        json_mode=True,
    )
    results.append(r2)
    try:
        return parse_score(r2.text)
    except ValueError:
        return None


MAX_FLAG_FACTS = 10
_FIRST_FACTS = guards.LANGUAGE_REQUIRED_IDS | guards.LANGUAGE_PROBABLE_IDS


def _filter_rows(session: Session, posting: Posting) -> list[FilterResult]:
    """Filter hits that are facts for the scorer: every flag, plus rejects the user overrode.

    Language findings come first so the fact cap never drops them.
    """
    rows = session.exec(select(FilterResult).where(FilterResult.posting_id == posting.id)).all()
    kept = [r for r in rows if r.action == "flag" or r.overridden]
    return sorted(kept, key=lambda r: (r.rule_id not in _FIRST_FACTS, r.id or 0))


def _fact(row: FilterResult) -> str:
    text = row.reason
    if row.rule_id in guards.LANGUAGE_PROBABLE_IDS and row.evidence:
        text += f" ({row.evidence})"
    if row.action == "reject":
        text += " (reject overridden by the user: still true, judge it)"
    return text


def _flags(session: Session, posting: Posting) -> list[str]:
    """Reasons of the filter hits, given to the LLM as RADAR FLAGS facts."""
    return [_fact(r) for r in _filter_rows(session, posting)][:MAX_FLAG_FACTS]


def _rules_score(posting: Posting, keyword: str) -> Score:
    assert posting.id is not None
    return Score(
        posting_id=posting.id,
        content_hash=posting.content_hash,
        priority="Bassa",
        why_fits="Nessun punto di contatto forte con i ruoli target.",
        what_doesnt_fit=f"Ruolo fuori target (area: {keyword}), lontano da AI, dati, "
        "consulenza, prodotto o VC.",
        warnings=[],
        confidence=0.9,
        provider="rules",
        model="relevance-v1",
    )


def score_pending(session: Session, run_id: int | None, limit: int) -> dict[str, Any]:
    profile = radar_config.load_yaml_config("profile")
    relevance = Relevance(profile)
    # Off-target titles are scored "Bassa" by rule (no LLM call, not counted in `limit`);
    # the rest are scored by the LLM, target roles first.
    pool = pending_postings(session, max(limit * 6, limit))
    auto = 0
    llm_queue: list[Posting] = []
    for posting in pool:
        if relevance.rank(posting.title) == 0:
            session.add(_rules_score(posting, relevance.offtarget_keyword(posting.title) or "?"))
            auto += 1
        else:
            llm_queue.append(posting)
    if auto:
        session.commit()
    llm_queue.sort(key=lambda p: -relevance.rank(p.title))
    postings = llm_queue[:limit]
    stats: dict[str, Any] = {"candidates": len(postings), "rules_bassa": auto}
    return _score_with_llm(session, run_id, postings, profile, relevance, stats)


def _score_with_llm(
    session: Session,
    run_id: int | None,
    postings: list[Posting],
    profile: dict[str, Any],
    relevance: Relevance,
    stats: dict[str, Any],
) -> dict[str, Any]:
    """Score ``postings`` with the LLM (one request each), then apply the guards."""
    stats.update(
        {
            "scored": 0,
            "invalid": 0,
            "errors": 0,
            "capped": 0,
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    )
    if not postings:
        return stats
    try:
        provider = get_provider("scoring")
    except MissingKeyError:
        log.warning("scoring skipped: missing API key (%d pending)", len(postings))
        return {
            "skipped": "missing_key",
            "candidates": stats["candidates"],
            "rules_bassa": stats.get("rules_bassa", 0),
            "scored": 0,
        }
    stats["provider"], stats["model"] = provider.name, provider.model

    profile_text = prompts.condense_profile(profile)
    system = prompts.scoring_system()
    consecutive_errors = 0
    for posting in postings:
        assert posting.id is not None
        company = session.get(Company, posting.company_id) if posting.company_id else None
        rows = _filter_rows(session, posting)
        facts = [_fact(r) for r in rows][:MAX_FLAG_FACTS]
        user = prompts.scoring_user(profile_text, posting, company, facts)
        results: list[LLMResult] = []
        try:
            out = _score_one(provider, system, user, results)
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
                record_usage(session, run_id, "scoring", r)
                stats["requests"] += 1
                stats["input_tokens"] += r.input_tokens
                stats["output_tokens"] += r.output_tokens
            if results:
                session.commit()
        consecutive_errors = 0
        if out is None:
            stats["invalid"] += 1
            continue
        last = results[-1]
        guard = guards.apply_guards(
            out.priority,
            title=posting.title,
            language_requirements=out.language_requirements,
            flags=[guards.FlagFact(r.rule_id, r.reason, r.overridden) for r in rows],
            relevance=relevance,
            warnings=out.warnings,
            what_doesnt_fit=out.what_doesnt_fit,
        )
        if guard.applied:
            stats["capped"] += 1
        session.add(
            Score(
                posting_id=posting.id,
                content_hash=posting.content_hash,
                priority=guard.priority,
                why_fits=out.why_fits,
                what_doesnt_fit=guard.what_doesnt_fit,
                start_window=out.start_window,
                language_requirements=out.language_requirements,
                warnings=guard.warnings,
                confidence=out.confidence,
                provider=last.provider,
                model=last.model,
                input_tokens=sum(r.input_tokens for r in results),
                output_tokens=sum(r.output_tokens for r in results),
                guard_version=guards.GUARD_VERSION,
            )
        )
        session.commit()
        stats["scored"] += 1
    log.info(
        "scoring: %d candidates, %d scored, %d invalid, %d errors%s",
        stats["candidates"],
        stats["scored"],
        stats["invalid"],
        stats["errors"],
        f", stopped ({stats['stopped']})" if "stopped" in stats else "",
    )
    return stats


# --------------------------------------------------------------------------- re-scoring

LANGUAGE_RULE_IDS = guards.LANGUAGE_REQUIRED_IDS | guards.LANGUAGE_PROBABLE_IDS
_KEEP_PROVIDERS = ("manual", "rules")  # the user's own scores and the rule-based Bassa


def language_flagged_scored(session: Session) -> list[tuple[Posting, list[Score]]]:
    """Scoreable postings with a language finding and LLM scores from before the current guards.

    Postings that also carry a manual score are left alone. Highest current priority first.
    """
    flagged = select(FilterResult.posting_id).where(
        col(FilterResult.rule_id).in_(LANGUAGE_RULE_IDS)
    )
    postings = session.exec(
        select(Posting).where(
            col(Posting.id).in_(flagged),
            Posting.status == "open",
            col(Posting.filter_state).in_(SCOREABLE_FILTER_STATES),
            col(Posting.duplicate_of).is_(None),
        )
    ).all()
    out: list[tuple[Posting, list[Score]]] = []
    for p in postings:
        rows = session.exec(select(Score).where(Score.posting_id == p.id)).all()
        if any(r.provider == "manual" for r in rows):
            continue
        stale = [
            r
            for r in rows
            if r.provider not in _KEEP_PROVIDERS and (r.guard_version or 0) < guards.GUARD_VERSION
        ]
        if stale:
            out.append((p, stale))
    best = {"Alta": 0, "Media": 1, "Bassa": 2}
    out.sort(key=lambda ps: (min(best.get(s.priority, 3) for s in ps[1]), -(ps[0].id or 0)))
    return out


def rescore_flagged_language(
    session: Session, run_id: int | None = None, *, dry_run: bool = False, limit: int | None = None
) -> dict[str, Any]:
    """Delete the stale LLM scores of language-flagged postings and score them again.

    ``dry_run`` only counts. Postings left unscored (rate limit) have no Score any more, so the
    next ``radar score`` / daily run picks them up as pending.
    """
    targets = language_flagged_scored(session)
    by_priority: dict[str, int] = {}
    for _, stale in targets:
        latest = max(stale, key=lambda s: (s.created_at, s.id or 0))
        by_priority[latest.priority] = by_priority.get(latest.priority, 0) + 1
    stats: dict[str, Any] = {
        "postings": len(targets),
        "stale_scores": sum(len(s) for _, s in targets),
        "by_priority": by_priority,
    }
    if dry_run or not targets:
        return stats
    try:
        get_provider("scoring")
    except MissingKeyError:
        return {**stats, "skipped": "missing_key"}  # nothing deleted
    chosen = targets[:limit] if limit else targets
    for _, stale in chosen:
        for row in stale:
            session.delete(row)
    session.commit()
    profile = radar_config.load_yaml_config("profile")
    llm_stats = _score_with_llm(
        session,
        run_id,
        [p for p, _ in chosen],
        profile,
        Relevance(profile),
        {"candidates": len(chosen)},
    )
    return {**stats, "deleted": sum(len(s) for _, s in chosen), **llm_stats}
