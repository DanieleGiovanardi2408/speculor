"""Contact search: parsing, URL validation, grounding, budget caps, no-login guarantee.

A fake search provider stands in for Groq; the Groq provider itself is exercised with an
``httpx.MockTransport`` returning a fictional response shaped like the real API. No network.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from llm_testkit import make_posting
from sqlmodel import Session, select

from radar import config as radar_config
from radar.llm import contacts as finder
from radar.llm.contacts import (
    BudgetExceeded,
    GroqSearchProvider,
    RawContact,
    SearchHit,
    SearchResult,
    ToolCall,
    estimate_cost_eur,
    normalize_profile_url,
    parse_contacts,
    review_contacts,
    slug_matches_name,
)
from radar.llm.provider import LLMError, RateLimiter
from radar.models import ApplicationPack, Contact, Draft, LLMUsage, Posting
from radar.timeutil import utcnow

LUISS_Q = '"Luiss" "Acme Analytics" site:linkedin.com/in'
ROLE_Q = '"Acme Analytics" "Data Science" site:linkedin.com/in'
HITS_LUISS = (
    SearchHit("https://it.linkedin.com/in/laura-esempio-fittizia", "Laura Esempio Fittizia"),
    SearchHit("https://www.linkedin.com/jobs/view/123", "Data Science Intern - Acme"),
)
HITS_ROLE = (
    SearchHit("https://www.linkedin.com/in/pfittizio", "Paolo Fittizio - Acme Analytics"),
    SearchHit("https://linkedin.com/in/sara-demo-esempio?trk=x", "Sara Demo Esempio | LinkedIn"),
)
TOOLS = (
    ToolCall("search", query=LUISS_Q, hits=HITS_LUISS),
    ToolCall("search", query=ROLE_Q, hits=HITS_ROLE),
)


def contact(name: str, url: str, category: str = "same_role", **kw: str) -> dict[str, str]:
    return {
        "name": name,
        "title": kw.get("title", "Data Scientist at Acme Analytics"),
        "url": url,
        "category": category,
        "reason": kw.get("reason", "Works on forecasting at the company."),
        "query": kw.get("query", ""),
    }


GOOD_OUTPUT = json.dumps(
    {
        "contacts": [
            contact(
                "Laura Esempio Fittizia",
                "https://it.linkedin.com/in/laura-esempio-fittizia",
                "alumni",
            ),
            # slug rewritten by the model: the search hit with the same name is used instead
            contact(
                "Paolo Fittizio", "https://www.linkedin.com/in/paolo-fittizio", "hiring_manager"
            ),
            contact("Sara Demo Esempio", "https://linkedin.com/in/sara-demo-esempio"),
        ]
    }
)


class FakeSearch:
    """Scripted stand-in for the Groq browser-search provider."""

    name = "fake-compound"
    model = "openai/gpt-oss-120b"

    def __init__(
        self,
        text: str = GOOD_OUTPUT,
        tools: tuple[ToolCall, ...] = TOOLS,
        error: Exception | None = None,
    ) -> None:
        self.text, self.tools, self.error = text, tools, error
        self.calls: list[tuple[str, str]] = []

    def search(self, system: str, user: str) -> SearchResult:
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        return SearchResult(
            self.text, 7000, 500, self.model, self.name, cached_tokens=700, tool_calls=self.tools
        )


@pytest.fixture()
def caps(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RADAR_LLM_PROVIDER", "fake")
    monkeypatch.setenv("RADAR_CONTACTS_MONTHLY_CAP_EUR", "5")
    monkeypatch.setenv("RADAR_CONTACTS_DAILY_CALL_CAP", "10")
    monkeypatch.setenv("RADAR_CONTACTS_DAILY_POSTINGS", "5")
    radar_config.get_settings.cache_clear()
    yield
    radar_config.get_settings.cache_clear()


def alta_with_pack(session: Session, **kw: Any) -> Posting:
    p = make_posting(session, priority="Alta", **kw)
    assert p.id is not None
    session.add(ApplicationPack(posting_id=p.id, content_hash=p.content_hash))
    session.commit()
    return p


# --------------------------------------------------------------------------- parsing


def test_parse_contacts_object_list_and_fences() -> None:
    assert parse_contacts(GOOD_OUTPUT)[0].name == "Laura Esempio Fittizia"
    fenced = "```json\n" + GOOD_OUTPUT + "\n```"
    assert len(parse_contacts(fenced)) == 3
    as_list = json.dumps([contact("Anna Prova", "https://example.org/team/anna-prova")])
    assert parse_contacts(as_list)[0].url == "https://example.org/team/anna-prova"
    prose = "Here you go: " + json.dumps({"contacts": [{"name": "X Y", "role": "Lead"}]})
    parsed = parse_contacts(prose)
    assert parsed[0].title == "Lead" and parsed[0].url == ""
    assert parse_contacts('{"contacts": []}') == []


def test_parse_contacts_rejects_non_json() -> None:
    with pytest.raises(ValueError):
        parse_contacts("I could not find anyone.")
    with pytest.raises(ValueError):
        parse_contacts('{"people": []}')


# --------------------------------------------------------------------------- URL validation


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://it.linkedin.com/in/laura-esempio/", "https://www.linkedin.com/in/laura-esempio"),
        (
            "https://www.linkedin.com/in/Laura-Esempio?trk=a#x",
            "https://www.linkedin.com/in/laura-esempio",
        ),
        ("https://example.org/team/anna-prova/", "https://example.org/team/anna-prova"),
        ("http://www.linkedin.com/in/laura-esempio", None),  # not https
        ("https://www.linkedin.com/jobs/view/123", None),  # not a person
        ("https://www.linkedin.com/company/acme", None),
        ("https://www.linkedin.com/posts/laura-esempio_activity-1", None),
        ("https://www.linkedin.com/in/", None),
        ("https://r.jina.ai/http://linkedin.com/in/laura-esempio", None),  # reader proxy
        ("https://www.google.com/search?q=laura", None),
        ("https://exa.ai/search?q=laura", None),
        ("https://example.org/", None),  # no path: not a profile
        ("https://user:pw@example.org/team/x", None),
        ("javascript:alert(1)", None),
        ("", None),
    ],
)
def test_normalize_profile_url(url: str, expected: str | None) -> None:
    assert normalize_profile_url(url) == expected


def test_slug_matches_name() -> None:
    assert slug_matches_name("mferri", "Marta Ferri")
    assert slug_matches_name("laura-esempio-0a1b2c", "Laura Esempio")
    assert slug_matches_name("ren%C3%A9-d%C3%BCrr", "René Dürr")
    assert not slug_matches_name("acme-user-123", "Laura Esempio")
    assert not slug_matches_name("", "Laura Esempio")
    assert not slug_matches_name("laura", "Al Bo")  # no 3+ letter token


# --------------------------------------------------------------------------- grounding


def raw(name: str, url: str, category: str = "same_role") -> RawContact:
    return RawContact(name, "Title", url, category, "reason", "")


def test_review_keeps_grounded_and_corrects_mangled_slug() -> None:
    review = review_contacts(parse_contacts(GOOD_OUTPUT), TOOLS)
    urls = [a.url for a in review.accepted]
    assert urls == [
        "https://www.linkedin.com/in/laura-esempio-fittizia",
        "https://www.linkedin.com/in/pfittizio",
        "https://www.linkedin.com/in/sara-demo-esempio",
    ]
    assert review.corrected == 1 and review.accepted[1].corrected
    assert review.accepted[0].category == "alumni" and review.accepted[0].query == LUISS_Q
    assert review.accepted[2].query == ROLE_Q


def test_review_drops_invented_and_malformed() -> None:
    items = [
        raw("Inventato Persona", "https://www.linkedin.com/in/inventato-persona"),  # not found
        raw("Laura Esempio Fittizia", ""),
        raw("Laura Esempio Fittizia", "https://www.linkedin.com/jobs/view/123"),
        raw("Mononimo", "https://www.linkedin.com/in/pfittizio"),
        raw("Laura Esempio Fittizia", "https://it.linkedin.com/in/laura-esempio-fittizia"),
        raw("Laura Esempio Fittizia", "https://www.linkedin.com/in/laura-esempio-fittizia/"),
    ]
    review = review_contacts(items, TOOLS)
    assert len(review.accepted) == 1
    assert review.dropped == {
        "not_in_search_results": 1,
        "no_url": 1,
        "not_a_profile": 1,
        "no_name": 1,
        "duplicate": 1,
    }


def test_review_slug_mismatch_and_alumni_claim_needs_luiss_query() -> None:
    tools = (
        ToolCall(
            "search",
            query=ROLE_Q,
            hits=(
                SearchHit("https://www.linkedin.com/in/xyz-123", "Laura Esempio"),
                SearchHit("https://www.linkedin.com/in/anna-prova", "Anna Prova"),
            ),
        ),
    )
    review = review_contacts(
        [
            raw("Laura Esempio", "https://www.linkedin.com/in/xyz-123"),
            raw("Anna Prova", "https://www.linkedin.com/in/anna-prova", "alumni"),
        ],
        tools,
    )
    assert review.dropped == {"slug_mismatch": 1}
    assert review.accepted[0].category == "other"  # alumni claim without a Luiss search


def test_review_without_any_search_keeps_nobody() -> None:
    review = review_contacts(parse_contacts(GOOD_OUTPUT), ())
    assert review.accepted == [] and review.dropped == {"not_in_search_results": 3}


# --------------------------------------------------------------------------- storage


def test_find_contacts_stores_reviewed_contacts_and_cost(session: Session, caps: None) -> None:
    p = alta_with_pack(session)
    assert p.id is not None
    fake = FakeSearch()
    stats = finder.find_contacts(session, p.id, provider=fake)
    assert stats["stored"] == 3 and stats["searches"] == 2 and stats["url_corrected"] == 1
    assert '"Luiss" "Acme Analytics"' in fake.calls[0][1]
    rows = session.exec(select(Contact).where(Contact.posting_id == p.id)).all()
    assert all(c.needs_review and c.source == "search" for c in rows)
    assert rows[0].why is not None and rows[0].why.startswith("Alumni Luiss: ")
    assert rows[0].source_query == LUISS_Q
    usage = session.exec(select(LLMUsage).where(LLMUsage.purpose == "contacts")).one()
    assert usage.requests == 1 and usage.tool_calls == 2
    expected = estimate_cost_eur(fake.model, 7000, 500, cached_tokens=700, searches=2)
    assert usage.est_cost_eur == pytest.approx(expected)


def test_find_contacts_dedupes_per_company(session: Session, caps: None) -> None:
    a = alta_with_pack(session)
    b = alta_with_pack(session, title="Analytics Intern")
    other = alta_with_pack(session, company="Beta Example")
    assert a.id and b.id and other.id
    finder.find_contacts(session, a.id, provider=FakeSearch())
    stats = finder.find_contacts(session, b.id, provider=FakeSearch())
    assert stats["stored"] == 0 and stats["duplicates"] == 3
    assert finder.find_contacts(session, other.id, provider=FakeSearch())["stored"] == 3


def test_find_contacts_invalid_output_still_records_usage(session: Session, caps: None) -> None:
    p = alta_with_pack(session)
    assert p.id is not None
    stats = finder.find_contacts(session, p.id, provider=FakeSearch(text="nothing found"))
    assert stats["invalid_output"] is True and stats["stored"] == 0
    assert session.exec(select(LLMUsage)).one().requests == 1


def test_find_contacts_unknown_posting(session: Session, caps: None) -> None:
    with pytest.raises(ValueError):
        finder.find_contacts(session, 999, provider=FakeSearch())


# --------------------------------------------------------------------------- budget


def test_monthly_cap_refuses_before_calling(session: Session, caps: None) -> None:
    p = alta_with_pack(session)
    assert p.id is not None
    session.add(LLMUsage(provider="groq", model="m", purpose="contacts", est_cost_eur=4.99))
    session.commit()
    fake = FakeSearch()
    with pytest.raises(BudgetExceeded) as exc:
        finder.find_contacts(session, p.id, provider=fake)
    assert exc.value.reason == "monthly_cap" and fake.calls == []


def test_monthly_cap_ignores_last_month_and_other_purposes(session: Session, caps: None) -> None:
    p = alta_with_pack(session)
    assert p.id is not None
    old = utcnow().replace(day=1) - timedelta(days=2)
    session.add(LLMUsage(provider="groq", model="m", purpose="contacts", est_cost_eur=50, at=old))
    session.add(LLMUsage(provider="groq", model="m", purpose="draft", est_cost_eur=50))
    session.commit()
    assert finder.find_contacts(session, p.id, provider=FakeSearch())["stored"] == 3


def test_monthly_cap_is_configurable(
    session: Session, caps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_CONTACTS_MONTHLY_CAP_EUR", "0.01")
    radar_config.get_settings.cache_clear()
    p = alta_with_pack(session)
    assert p.id is not None
    with pytest.raises(BudgetExceeded):  # the worst-case reserve alone exceeds 0.01
        finder.find_contacts(session, p.id, provider=FakeSearch())


def test_daily_call_cap(session: Session, caps: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_CONTACTS_DAILY_CALL_CAP", "2")
    radar_config.get_settings.cache_clear()
    postings = [alta_with_pack(session, company=f"Firm {i}") for i in range(3)]
    fake = FakeSearch()
    for p in postings[:2]:
        assert p.id is not None
        finder.find_contacts(session, p.id, provider=fake)
    assert postings[2].id is not None
    with pytest.raises(BudgetExceeded) as exc:
        finder.find_contacts(session, postings[2].id, provider=fake)
    assert exc.value.reason == "daily_cap" and len(fake.calls) == 2
    assert finder.budget_status(session).calls_today == 2


def test_failed_calls_count_toward_daily_cap(
    session: Session, caps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_CONTACTS_DAILY_CALL_CAP", "1")
    radar_config.get_settings.cache_clear()
    p = alta_with_pack(session)
    assert p.id is not None
    with pytest.raises(LLMError):
        finder.find_contacts(session, p.id, provider=FakeSearch(error=LLMError("boom")))
    with pytest.raises(BudgetExceeded):
        finder.find_contacts(session, p.id, provider=FakeSearch())


def test_cost_model() -> None:
    # 1M input + 1M output tokens on gpt-oss-120b, plus 1000 searches and 1000 opens.
    assert estimate_cost_eur("openai/gpt-oss-120b", 10**6, 10**6) == pytest.approx(0.75)
    assert estimate_cost_eur("openai/gpt-oss-120b", 0, 0, searches=1000, opens=1000) == (
        pytest.approx(6.0)
    )
    cached = estimate_cost_eur("openai/gpt-oss-120b", 10**6, 0, cached_tokens=10**6)
    assert cached == pytest.approx(0.075)
    assert estimate_cost_eur("unknown/model", 10**6, 0) == pytest.approx(0.15)  # priciest row
    # the reserve covers a heavier-than-observed call (real probe: about 0.015)
    assert 0.02 < finder.call_reserve_eur("openai/gpt-oss-120b") < 0.06


# --------------------------------------------------------------------------- daily batch


def test_alta_batch_limits_and_skips(session: Session, caps: None) -> None:
    for i in range(7):
        alta_with_pack(session, company=f"Firm {i}")
    make_posting(session, priority="Alta", company="No Pack Firm")  # no pack: not a candidate
    media = make_posting(session, priority="Media", company="Media Firm")
    assert media.id is not None
    session.add(ApplicationPack(posting_id=media.id, content_hash=media.content_hash))
    session.commit()
    fake = FakeSearch()
    stats = finder.find_contacts_for_alta(session, provider=fake)
    assert stats["candidates"] == 5 and stats["searched"] == 5  # RADAR_CONTACTS_DAILY_POSTINGS
    assert stats["budget"]["calls_today"] == 5
    # same day: the daily room is used up
    assert finder.find_contacts_for_alta(session, provider=fake)["candidates"] == 0
    prompts = " ".join(u for _s, u in fake.calls)
    assert "Media Firm" not in prompts and "No Pack Firm" not in prompts


def test_alta_batch_does_not_research_recent_empty_searches(session: Session, caps: None) -> None:
    p = alta_with_pack(session)
    empty = FakeSearch(text='{"contacts": []}')
    assert finder.find_contacts_for_alta(session, provider=empty, limit=5)["searched"] == 1
    assert finder.find_contacts_for_alta(session, provider=empty, limit=5)["candidates"] == 0
    assert p.id is not None


def test_alta_batch_stops_on_budget(session: Session, caps: None) -> None:
    for i in range(3):
        alta_with_pack(session, company=f"Firm {i}")
    session.add(LLMUsage(provider="groq", model="m", purpose="contacts", est_cost_eur=5))
    session.commit()
    fake = FakeSearch()
    stats = finder.find_contacts_for_alta(session, provider=fake)
    assert stats["stopped"] == "monthly_cap" and fake.calls == []


def test_alta_batch_without_key_skips(
    session: Session, caps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "")
    radar_config.get_settings.cache_clear()
    alta_with_pack(session)
    assert finder.find_contacts_for_alta(session)["skipped"] == "missing_key"


def test_fake_provider_end_to_end(session: Session, caps: None) -> None:
    p = alta_with_pack(session)
    assert p.id is not None
    stats = finder.find_contacts(session, p.id)  # demo mode: offline FakeSearchProvider
    assert stats["stored"] == 2
    urls = [c.profile_url for c in session.exec(select(Contact)).all()]
    assert all(u and u.startswith("https://example.com/") for u in urls)


# --------------------------------------------------------------------------- Groq provider


def groq_response() -> dict[str, Any]:
    """Fictional response with the shape returned by the real API (2026-10)."""
    return {
        "id": "x",
        "model": "openai/gpt-oss-120b",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": GOOD_OUTPUT,
                    "executed_tools": [
                        {
                            "index": 0,
                            "type": "browser_search",
                            "name": "browser.search",
                            "arguments": json.dumps({"query": LUISS_Q, "topn": 10}),
                            "output": "L0: ...",
                            "search_results": {
                                "results": [
                                    {"title": h.title, "url": h.url, "content": "", "score": 0}
                                    for h in HITS_LUISS
                                ]
                            },
                        },
                        {
                            "index": 1,
                            "type": "browser_search",
                            "name": "browser.search",
                            "arguments": json.dumps({"query": ROLE_Q}),
                            "output": "",
                            "search_results": {
                                "results": [{"title": h.title, "url": h.url} for h in HITS_ROLE]
                            },
                        },
                        {
                            "index": 2,
                            "type": "function",
                            "name": "browser.open",
                            "arguments": json.dumps({"cursor": 0, "id": 0}),
                            "output": "no content returned for https://linkedin.com/in/pfittizio",
                            "search_results": {"results": []},
                        },
                    ],
                }
            }
        ],
        "usage": {
            "prompt_tokens": 7134,
            "completion_tokens": 502,
            "prompt_tokens_details": {"cached_tokens": 768},
        },
        "usage_breakdown": None,
    }


def mock_groq(seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=groq_response())

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_groq_provider_request_and_parse(session: Session, caps: None) -> None:
    seen: list[httpx.Request] = []
    provider = GroqSearchProvider(
        "test-key", "openai/gpt-oss-120b", client=mock_groq(seen), limiter=RateLimiter(100, 10**6)
    )
    p = alta_with_pack(session)
    assert p.id is not None
    stats = finder.find_contacts(session, p.id, provider=provider)
    body = json.loads(seen[0].content)
    assert body["tools"] == [{"type": "browser_search"}] and body["tool_choice"] == "required"
    assert "response_format" not in body  # not supported together with browser_search
    assert stats["stored"] == 3 and stats["searches"] == 2 and stats["page_opens"] == 1
    assert stats["login_page_opens"] == 1  # counted, and its content is never used
    usage = session.exec(select(LLMUsage)).one()
    assert usage.input_tokens == 7134 and usage.est_cost_eur > 0


# --------------------------------------------------------------------------- no login


def test_no_subprocess_no_linkedin_cli(
    session: Session, caps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The search never spawns a process (no linkedin-cli) and only talks to the Groq API."""
    spawned: list[Any] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        spawned.append(args)
        raise AssertionError("subprocess used during contact search")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr("os.system", forbidden)
    seen: list[httpx.Request] = []
    provider = GroqSearchProvider(
        "k", "openai/gpt-oss-120b", client=mock_groq(seen), limiter=RateLimiter(100, 10**6)
    )
    for i in range(2):
        p = alta_with_pack(session, company=f"Firm {i}")
        assert p.id is not None
        finder.find_contacts(session, p.id, provider=provider)
    finder.find_contacts_for_alta(session, provider=provider, limit=5)
    assert spawned == []
    assert {r.url.host for r in seen} == {"api.groq.com"}
    assert all("linkedin" not in str(r.url) for r in seen)


def test_module_has_no_login_or_process_code() -> None:
    source = Path(finder.__file__).read_text(encoding="utf-8")
    for needle in ("import subprocess", "linkedin_bin", "os.system", "cookie", "li_at"):
        assert needle not in source, needle
    assert "PoliteClient" not in source and "httpx.get" not in source


# --------------------------------------------------------------------------- drafts


def test_draft_outreach_for_contact(session: Session, fake_env: None) -> None:
    from radar.llm.packs import draft_outreach_for_contact

    p = make_posting(session, priority="Alta")
    assert p.id is not None
    c = Contact(posting_id=p.id, name="Laura Esempio", role="Data Lead", why="Ruolo simile: x")
    session.add(c)
    session.commit()
    assert c.id is not None
    drafts = draft_outreach_for_contact(session, p.id, c.id)
    session.commit()
    kinds = [d.kind for d in drafts]
    assert kinds == ["linkedin_connection_note", "linkedin_message"]
    assert all(d.status == "draft" and d.contact_id == c.id for d in drafts)
    assert len(drafts[0].text) <= 300
    assert len(session.exec(select(Draft)).all()) == 2
