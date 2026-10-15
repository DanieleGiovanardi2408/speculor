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
from radar.llm import prompts, style
from radar.llm.provider import (
    LLMError,
    LLMProvider,
    LLMResult,
    MissingKeyError,
    RateLimitExhausted,
    get_provider,
    record_usage,
)
from radar.models import Company, Posting, Score

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


def score_pending(session: Session, run_id: int | None, limit: int) -> dict[str, Any]:
    postings = pending_postings(session, limit)
    stats: dict[str, Any] = {
        "candidates": len(postings),
        "scored": 0,
        "invalid": 0,
        "errors": 0,
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    if not postings:
        return stats
    try:
        provider = get_provider("scoring")
    except MissingKeyError:
        log.warning("scoring skipped: missing API key (%d pending)", len(postings))
        return {"skipped": "missing_key", "candidates": len(postings), "scored": 0}
    stats["provider"], stats["model"] = provider.name, provider.model

    profile_text = prompts.condense_profile(radar_config.load_yaml_config("profile"))
    system = prompts.scoring_system()
    consecutive_errors = 0
    for posting in postings:
        assert posting.id is not None
        company = session.get(Company, posting.company_id) if posting.company_id else None
        user = prompts.scoring_user(profile_text, posting, company)
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
        session.add(
            Score(
                posting_id=posting.id,
                content_hash=posting.content_hash,
                priority=out.priority,
                why_fits=out.why_fits,
                what_doesnt_fit=out.what_doesnt_fit,
                start_window=out.start_window,
                language_requirements=out.language_requirements,
                warnings=out.warnings,
                confidence=out.confidence,
                provider=last.provider,
                model=last.model,
                input_tokens=sum(r.input_tokens for r in results),
                output_tokens=sum(r.output_tokens for r in results),
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
