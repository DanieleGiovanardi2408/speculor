from __future__ import annotations

import json

import httpx
import pytest
import respx
from sqlmodel import Session, select

from radar import config as radar_config
from radar.llm.provider import (
    ANTHROPIC_URL,
    GROQ_URL,
    AnthropicProvider,
    FakeProvider,
    GroqProvider,
    LLMError,
    LLMResult,
    MissingKeyError,
    RateLimiter,
    RateLimitExhausted,
    get_provider,
    parse_duration,
    record_usage,
)
from radar.models import LLMUsage

GROQ_OK = {
    "model": "openai/gpt-oss-20b",
    "choices": [{"message": {"role": "assistant", "content": '{"a": 1}'}}],
    "usage": {"prompt_tokens": 120, "completion_tokens": 30},
}


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def groq(sleeps: Sleeps, model: str = "openai/gpt-oss-20b") -> GroqProvider:
    return GroqProvider(
        "gsk-test", model, sleep=sleeps, limiter=RateLimiter(1000, 10**9, sleep=sleeps)
    )


def complete(p: GroqProvider | AnthropicProvider, json_mode: bool = True) -> LLMResult:
    return p.complete(
        "sys", "user", model=p.model, max_tokens=500, temperature=0.2, json_mode=json_mode
    )


@respx.mock
def test_groq_request_shape_json_mode() -> None:
    route = respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json=GROQ_OK))
    result = complete(groq(Sleeps()))
    req = route.calls.last.request
    body = json.loads(req.content)
    assert req.headers["authorization"] == "Bearer gsk-test"
    assert body["model"] == "openai/gpt-oss-20b"
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_completion_tokens"] == 500
    assert body["reasoning_effort"] == "low"
    assert result == LLMResult('{"a": 1}', 120, 30, "openai/gpt-oss-20b", "groq")


@respx.mock
def test_groq_no_json_mode_no_reasoning_params_for_llama() -> None:
    route = respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json=GROQ_OK))
    complete(groq(Sleeps(), model="llama-3.1-8b-instant"), json_mode=False)
    body = json.loads(route.calls.last.request.content)
    assert "response_format" not in body
    assert "reasoning_effort" not in body


@respx.mock
def test_groq_json_validate_failed_returns_failed_generation() -> None:
    err = {
        "error": {
            "code": "json_validate_failed",
            "message": "bad",
            "failed_generation": "{not json",
        }
    }
    respx.post(GROQ_URL).mock(return_value=httpx.Response(400, json=err))
    assert complete(groq(Sleeps())).text == "{not json"


@respx.mock
def test_groq_other_400_raises() -> None:
    respx.post(GROQ_URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "model not found"}})
    )
    with pytest.raises(LLMError, match="model not found"):
        complete(groq(Sleeps()))


@respx.mock
def test_429_short_wait_retry_after_then_ok() -> None:
    sleeps = Sleeps()
    route = respx.post(GROQ_URL).mock(
        side_effect=[
            httpx.Response(
                429,
                headers={"retry-after": "2"},
                json={"error": {"message": "tokens per minute (TPM)"}},
            ),
            httpx.Response(200, json=GROQ_OK),
        ]
    )
    assert complete(groq(sleeps)).text == '{"a": 1}'
    assert route.call_count == 2
    assert sleeps.calls and 2.0 <= sleeps.calls[0] <= 3.0


@respx.mock
def test_429_short_wait_from_ratelimit_reset_header() -> None:
    sleeps = Sleeps()
    respx.post(GROQ_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"x-ratelimit-reset-tokens": "7.66s"}, json={}),
            httpx.Response(200, json=GROQ_OK),
        ]
    )
    complete(groq(sleeps))
    assert 7.6 <= sleeps.calls[0] <= 8.0


@respx.mock
def test_429_daily_limit_raises_exhausted_without_retry() -> None:
    msg = (
        "Rate limit reached for model `openai/gpt-oss-20b` on tokens per day (TPD): "
        "Limit 200000, Used 199500, Requested 900. Please try again in 7m12.5s."
    )
    route = respx.post(GROQ_URL).mock(
        return_value=httpx.Response(429, json={"error": {"message": msg}})
    )
    with pytest.raises(RateLimitExhausted):
        complete(groq(Sleeps()))
    assert route.call_count == 1


@respx.mock
def test_429_long_reset_raises_exhausted() -> None:
    route = respx.post(GROQ_URL).mock(
        return_value=httpx.Response(429, headers={"retry-after": "3600"}, json={})
    )
    with pytest.raises(RateLimitExhausted):
        complete(groq(Sleeps()))
    assert route.call_count == 1


@respx.mock
def test_5xx_retries_with_backoff() -> None:
    sleeps = Sleeps()
    route = respx.post(GROQ_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, json=GROQ_OK)]
    )
    complete(groq(sleeps))
    assert route.call_count == 3
    assert sleeps.calls == [1.0, 2.0]


@respx.mock
def test_5xx_gives_up() -> None:
    respx.post(GROQ_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(LLMError):
        complete(groq(Sleeps()))


@respx.mock
def test_anthropic_request_shape() -> None:
    sleeps = Sleeps()
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "claude-sonnet-5-5",
                "content": [
                    {"type": "thinking", "thinking": ""},
                    {"type": "text", "text": '{"ok": true}'},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 50, "output_tokens": 9},
            },
        )
    )
    p = AnthropicProvider(
        "sk-ant-test",
        "claude-sonnet-5-5",
        sleep=sleeps,
        limiter=RateLimiter(1000, 10**9, sleep=sleeps),
    )
    result = complete(p)
    req = route.calls.last.request
    body = json.loads(req.content)
    assert req.headers["x-api-key"] == "sk-ant-test"
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert body["messages"] == [{"role": "user", "content": "user"}]
    assert body["system"].startswith("sys") and "JSON" in body["system"]
    assert "temperature" not in body
    assert body["max_tokens"] >= 4096
    assert body["output_config"] == {"effort": "low"}
    assert result == LLMResult('{"ok": true}', 50, 9, "claude-sonnet-5-5", "anthropic")


@respx.mock
def test_anthropic_429_retry_after() -> None:
    sleeps = Sleeps()
    respx.post(ANTHROPIC_URL).mock(
        side_effect=[
            httpx.Response(
                429,
                headers={"retry-after": "5"},
                json={"type": "error", "error": {"type": "rate_limit_error"}},
            ),
            httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "hi"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            ),
        ]
    )
    p = AnthropicProvider(
        "k", "claude-sonnet-5-5", sleep=sleeps, limiter=RateLimiter(1000, 10**9, sleep=sleeps)
    )
    assert complete(p, json_mode=False).text == "hi"
    assert 5.0 <= sleeps.calls[0] <= 6.0


def test_parse_duration() -> None:
    assert parse_duration("7.66s") == pytest.approx(7.66)
    assert parse_duration("2m59.56s") == pytest.approx(179.56)
    assert parse_duration("1h2m3s") == pytest.approx(3723)
    assert parse_duration("120ms") == pytest.approx(0.12)
    assert parse_duration("12") == 12
    assert parse_duration("") is None
    assert parse_duration("soon") is None


def test_rate_limiter_requests_and_tokens() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        now[0] += s

    lim = RateLimiter(2, 1000, clock=lambda: now[0], sleep=sleep)
    lim.acquire(10)
    lim.acquire(10)
    lim.acquire(10)  # third request in the same minute must wait for the window
    assert sleeps and sum(sleeps) >= 59.9
    sleeps.clear()
    lim2 = RateLimiter(100, 1000, clock=lambda: now[0], sleep=sleep)
    h = lim2.acquire(900)
    lim2.commit(h, 950)
    lim2.acquire(100)  # 950 + 100 > 1000: waits
    assert sum(sleeps) >= 59.9


def test_get_provider_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "")
    radar_config.get_settings.cache_clear()
    try:
        with pytest.raises(MissingKeyError):
            get_provider("scoring")
        monkeypatch.setenv("RADAR_DRAFT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        radar_config.get_settings.cache_clear()
        with pytest.raises(MissingKeyError, match="ANTHROPIC"):
            get_provider("draft")
    finally:
        radar_config.get_settings.cache_clear()


def test_get_provider_switch_draft_to_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_LLM_PROVIDER", "fake")
    monkeypatch.setenv("RADAR_DRAFT_PROVIDER", "anthropic")
    monkeypatch.setenv("RADAR_DRAFT_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    radar_config.get_settings.cache_clear()
    try:
        assert isinstance(get_provider("scoring"), FakeProvider)
        draft = get_provider("draft")
        assert isinstance(draft, AnthropicProvider)
        assert draft.model == "claude-sonnet-5-5"
    finally:
        radar_config.get_settings.cache_clear()


def test_record_usage_accumulates(session: Session) -> None:
    r = LLMResult("x", 100, 20, "m", "groq")
    record_usage(session, None, "scoring", r)
    record_usage(session, None, "scoring", r)
    record_usage(session, None, "pack", r)
    rows = session.exec(select(LLMUsage)).all()
    by_purpose = {row.purpose: row for row in rows}
    assert len(rows) == 2
    assert by_purpose["scoring"].requests == 2
    assert by_purpose["scoring"].input_tokens == 200
    assert by_purpose["scoring"].output_tokens == 40
    assert by_purpose["pack"].requests == 1
