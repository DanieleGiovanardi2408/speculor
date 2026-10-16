"""LLM providers (Groq, Anthropic, offline Fake) over plain HTTP, with free-tier rate limiting.

* ``LLMProvider.complete(system, user, *, model, max_tokens, temperature, json_mode)`` returns an
  ``LLMResult``.
* ``get_provider("scoring" | "draft")`` builds the provider configured in ``radar.config.Settings``
  and raises ``MissingKeyError`` when the API key is absent.
* A client-side limiter (requests/min and tokens/min per provider+model) keeps us under the Groq
  free tier. On HTTP 429 we wait when the reset is short (<= 60 s); a daily limit (or a long
  reset) raises ``RateLimitExhausted`` so callers stop cleanly and resume on the next run.
* 5xx / 529 / network errors are retried with exponential backoff.
* ``record_usage`` accumulates one ``LLMUsage`` row per (run, provider, model, purpose).

Logging is counts-only: never prompts, outputs, company names or keys.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import lru_cache
from typing import Any, Literal, Protocol

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlmodel import Session, col, select

from radar.config import REPO_ROOT, get_settings
from radar.models import LLMUsage
from radar.timeutil import utcnow

log = logging.getLogger(__name__)

Purpose = Literal["scoring", "draft"]
UsagePurpose = Literal["scoring", "pack", "draft", "verify"]
SleepFn = Callable[[float], None]

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_SHORT_WAIT_S = 60.0


# --------------------------------------------------------------------------- errors / results


class LLMError(RuntimeError):
    """Any provider failure that is not a rate limit (bad request, auth, repeated 5xx...)."""


class MissingKeyError(LLMError):
    """The configured provider needs an API key that is not set."""


class RateLimitExhausted(LLMError):
    """A daily (or long) rate limit was hit: stop now and resume on the next run."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str


class LLMProvider(Protocol):
    name: str
    model: str  # default model for the purpose this provider was built for

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LLMResult: ...


# --------------------------------------------------------------------------- rate limiting


class LimiterSettings(BaseSettings):
    """Client-side limits. Defaults sit just under the Groq free tier for openai/gpt-oss-*
    (30 RPM, 8K TPM per model, see https://console.groq.com/docs/rate-limits)."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    groq_rpm: int = Field(default=25, validation_alias="RADAR_GROQ_RPM")
    groq_tpm: int = Field(default=7000, validation_alias="RADAR_GROQ_TPM")
    anthropic_rpm: int = Field(default=45, validation_alias="RADAR_ANTHROPIC_RPM")
    anthropic_tpm: int = Field(default=25000, validation_alias="RADAR_ANTHROPIC_TPM")


@lru_cache(maxsize=1)
def get_limiter_settings() -> LimiterSettings:
    return LimiterSettings()


class RateLimiter:
    """Sliding 60 s window over requests and tokens. Thread-safe; clock/sleep injectable."""

    def __init__(
        self,
        rpm: int,
        tpm: int,
        *,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: SleepFn = time.sleep,
    ) -> None:
        self.rpm = max(1, rpm)
        self.tpm = max(1, tpm)
        self.window = window
        self._clock = clock
        self._sleep = sleep
        self._events: deque[list[float]] = deque()  # [timestamp, tokens]
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= self.window:
            self._events.popleft()

    def _wait_needed(self, now: float, tokens: int) -> float:
        self._prune(now)
        if not self._events:
            return 0.0
        used = sum(e[1] for e in self._events)
        if len(self._events) < self.rpm and used + tokens <= self.tpm:
            return 0.0
        if len(self._events) >= self.rpm:
            return self._events[0][0] + self.window - now
        # Token budget: wait until enough old events leave the window.
        freed = 0.0
        for ts, tok in self._events:
            freed += tok
            if used - freed + tokens <= self.tpm:
                return ts + self.window - now
        return self._events[-1][0] + self.window - now

    def acquire(self, tokens: int) -> list[float]:
        """Block until a request of ~``tokens`` fits; return a handle for ``commit``."""
        while True:
            with self._lock:
                now = self._clock()
                wait = self._wait_needed(now, tokens)
                if wait <= 0:
                    handle = [now, float(tokens)]
                    self._events.append(handle)
                    return handle
            self._sleep(min(max(wait, 0.05), self.window))

    def commit(self, handle: list[float], actual_tokens: int) -> None:
        """Replace the estimate with the real token count once the response is known."""
        with self._lock:
            handle[1] = float(actual_tokens)


_LIMITERS: dict[tuple[str, str], RateLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def get_limiter(provider: str, model: str) -> RateLimiter:
    with _LIMITERS_LOCK:
        key = (provider, model)
        if key not in _LIMITERS:
            cfg = get_limiter_settings()
            if provider == "anthropic":
                _LIMITERS[key] = RateLimiter(cfg.anthropic_rpm, cfg.anthropic_tpm)
            else:
                _LIMITERS[key] = RateLimiter(cfg.groq_rpm, cfg.groq_tpm)
        return _LIMITERS[key]


def reset_limiters() -> None:
    with _LIMITERS_LOCK:
        _LIMITERS.clear()


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (about 4 characters per token)."""
    return len(text) // 4 + 1


# --------------------------------------------------------------------------- 429 parsing

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_DAILY_MARKERS = (
    "per day",
    "(tpd)",
    "(rpd)",
    "tokens per day",
    "requests per day",
    "daily",
)


def parse_duration(value: str | None) -> float | None:
    """Parse '7.66s', '2m59.56s', '1h2m3s', '120ms' or plain seconds ('12', '1.5')."""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    total = 0.0
    found = False
    for num, unit in _DURATION_PART.findall(value):
        found = True
        n = float(num)
        total += {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}[unit] * n
    return total if found else None


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    secs = parse_duration(value)
    if secs is not None:
        return secs
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def parse_rate_limit(response: httpx.Response) -> tuple[float | None, bool]:
    """Return (seconds to wait or None if unknown, is_daily_limit) for a 429 response."""
    body = response.text.lower()
    daily = any(marker in body for marker in _DAILY_MARKERS)
    headers = response.headers
    wait = _parse_retry_after(headers.get("retry-after"))
    if wait is None:
        m = re.search(r"try again in\s+([0-9hms.]+)", body)
        if m:
            wait = parse_duration(m.group(1))
    if wait is None:
        candidates: list[float] = []
        tok = parse_duration(headers.get("x-ratelimit-reset-tokens"))
        if tok is not None:
            candidates.append(tok)
        if headers.get("x-ratelimit-remaining-requests") == "0":
            req = parse_duration(headers.get("x-ratelimit-reset-requests"))
            if req is not None:
                candidates.append(req)
        if candidates:
            wait = max(candidates)
    return wait, daily


# --------------------------------------------------------------------------- HTTP providers


class _HTTPProvider:
    name = "http"
    url = ""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        client: httpx.Client | None = None,
        sleep: SleepFn = time.sleep,
        limiter: RateLimiter | None = None,
        max_retries: int = 3,
        max_short_wait: float = MAX_SHORT_WAIT_S,
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._client = client or httpx.Client(timeout=timeout)
        self._sleep = sleep
        self._limiter = limiter
        self._max_retries = max_retries
        self._max_short_wait = max_short_wait

    # subclass hooks -------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _body(
        self,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _parse(self, data: dict[str, Any], model: str) -> LLMResult:
        raise NotImplementedError

    def _client_error(self, response: httpx.Response, model: str) -> LLMResult | None:
        return None

    # ----------------------------------------------------------------------
    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LLMResult:
        body = self._body(system, user, model, max_tokens, temperature, json_mode)
        limiter = self._limiter or get_limiter(self.name, model)
        handle = limiter.acquire(estimate_tokens(system + user) + max_tokens // 2)
        server_errors = 0
        rate_limited = 0
        while True:
            try:
                response = self._client.post(self.url, headers=self._headers(), json=body)
            except httpx.TransportError as exc:
                server_errors += 1
                if server_errors > self._max_retries:
                    raise LLMError(f"{self.name}: network error ({type(exc).__name__})") from exc
                self._sleep(2.0 ** (server_errors - 1))
                continue

            status = response.status_code
            if status == 429:
                wait, daily = parse_rate_limit(response)
                if daily:
                    log.warning("%s: daily rate limit reached", self.name)
                    raise RateLimitExhausted(f"{self.name}: daily rate limit reached")
                if wait is not None and wait > self._max_short_wait:
                    log.warning("%s: rate limit resets in %.0fs, stopping", self.name, wait)
                    raise RateLimitExhausted(f"{self.name}: rate limit resets in {wait:.0f}s")
                rate_limited += 1
                if rate_limited > self._max_retries:
                    raise RateLimitExhausted(f"{self.name}: still rate limited after retries")
                delay = wait if wait is not None else 2.0**rate_limited
                log.info("%s: 429, waiting %.1fs", self.name, delay)
                self._sleep(delay + 0.1)
                continue
            if status >= 500:  # includes Anthropic 529 overloaded
                server_errors += 1
                if server_errors > self._max_retries:
                    raise LLMError(f"{self.name}: HTTP {status} after {self._max_retries} retries")
                self._sleep(2.0 ** (server_errors - 1))
                continue
            if status >= 400:
                handled = self._client_error(response, model)
                if handled is not None:
                    limiter.commit(handle, handled.input_tokens + handled.output_tokens)
                    return handled
                raise LLMError(f"{self.name}: HTTP {status}: {_error_message(response)}")

            try:
                data = response.json()
            except ValueError as exc:
                raise LLMError(f"{self.name}: response is not JSON") from exc
            result = self._parse(data, model)
            limiter.commit(handle, result.input_tokens + result.output_tokens)
            return result


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or "")[:200]
    return str(err or "")[:200]


class GroqProvider(_HTTPProvider):
    """Groq OpenAI-compatible chat completions."""

    name = "groq"
    url = GROQ_URL

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def _body(
        self,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if model.startswith("openai/gpt-oss"):
            # Reasoning models: keep the hidden reasoning short (it counts toward TPM/TPD).
            body["reasoning_effort"] = "low"
            body["include_reasoning"] = False
        return body

    def _parse(self, data: dict[str, Any], model: str) -> LLMResult:
        try:
            text = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("groq: unexpected response shape") from exc
        usage = data.get("usage") or {}
        return LLMResult(
            text=str(text),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            model=str(data.get("model") or model),
            provider=self.name,
        )

    def _client_error(self, response: httpx.Response, model: str) -> LLMResult | None:
        # JSON mode: Groq rejects output that is not valid JSON with 400 json_validate_failed.
        # Hand the failed generation back so the caller's repair retry can deal with it.
        if response.status_code != 400:
            return None
        try:
            err = response.json().get("error") or {}
        except ValueError:
            return None
        if isinstance(err, dict) and err.get("code") == "json_validate_failed":
            return LLMResult(
                text=str(err.get("failed_generation") or ""),
                input_tokens=0,
                output_tokens=0,
                model=model,
                provider=self.name,
            )
        return None


_ANTHROPIC_EFFORT_MODELS = re.compile(r"^claude-(opus|sonnet|haiku|fable)-5")


class AnthropicProvider(_HTTPProvider):
    """Anthropic Messages API (raw HTTP).

    Current models reject non-default sampling parameters and assistant prefill, so
    ``temperature`` is not sent and JSON mode is a system-prompt instruction (callers parse
    leniently and repair once).
    """

    name = "anthropic"
    url = ANTHROPIC_URL

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _body(
        self,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> dict[str, Any]:
        if json_mode:
            system = (
                f"{system}\n\nRespond with a single valid JSON object only: no prose, "
                "no code fences."
            )
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max(max_tokens, 4096),  # room for adaptive thinking
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if _ANTHROPIC_EFFORT_MODELS.match(model):
            body["output_config"] = {"effort": "low"}
        return body

    def _parse(self, data: dict[str, Any], model: str) -> LLMResult:
        if data.get("stop_reason") == "refusal":
            raise LLMError("anthropic: request declined (refusal)")
        blocks = data.get("content") or []
        text = "".join(
            str(b.get("text") or "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            model=str(data.get("model") or model),
            provider=self.name,
        )


class FakeProvider:
    """Deterministic offline provider: valid scoring JSON and rule-compliant drafts built from
    the prompt itself. No key, no network. Used by ``make dev`` and tests."""

    name = "fake"

    def __init__(self, model: str = "fake-deterministic") -> None:
        self.model = model
        self.calls = 0

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LLMResult:
        from radar.llm.prompts import fake_completion  # local import: prompts imports nothing here

        self.calls += 1
        text = fake_completion(system, user)
        return LLMResult(
            text=text,
            input_tokens=estimate_tokens(system + user),
            output_tokens=estimate_tokens(text),
            model=model or self.model,
            provider=self.name,
        )


# --------------------------------------------------------------------------- factory / usage


def get_provider(purpose: Purpose) -> LLMProvider:
    """Provider + default model for ``purpose`` from settings. Raises MissingKeyError."""
    settings = get_settings()
    if purpose == "scoring":
        name, model = settings.llm_provider, settings.scoring_model
    else:
        name, model = settings.effective_draft_provider, settings.draft_model
    if name == "fake":
        return FakeProvider()
    if name == "groq":
        if not settings.groq_api_key:
            raise MissingKeyError("GROQ_API_KEY is not set")
        return GroqProvider(settings.groq_api_key, model)
    if name == "anthropic":
        if not settings.anthropic_api_key:
            raise MissingKeyError("ANTHROPIC_API_KEY is not set")
        return AnthropicProvider(settings.anthropic_api_key, model)
    raise LLMError(f"unknown LLM provider {name!r}")


def record_usage(
    session: Session, run_id: int | None, purpose: UsagePurpose, result: LLMResult
) -> LLMUsage:
    """Accumulate into the LLMUsage row for (run, provider, model, purpose).

    Without a run (CLI/API calls) rows are accumulated per UTC day.
    """
    stmt = select(LLMUsage).where(
        LLMUsage.provider == result.provider,
        LLMUsage.model == result.model,
        LLMUsage.purpose == purpose,
    )
    if run_id is None:
        start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(col(LLMUsage.run_id).is_(None), col(LLMUsage.at) >= start)
    else:
        stmt = stmt.where(LLMUsage.run_id == run_id)
    row = session.exec(stmt.order_by(col(LLMUsage.id).desc())).first()
    if row is None:
        row = LLMUsage(run_id=run_id, provider=result.provider, model=result.model, purpose=purpose)
    row.requests += 1
    row.input_tokens += result.input_tokens
    row.output_tokens += result.output_tokens
    if run_id is not None:
        row.at = utcnow()
    session.add(row)
    session.flush()
    return row
