"""Fact-bank prompts, deterministic pre-check, verifier parsing and storage. Fictional data."""

from __future__ import annotations

import json

import pytest
from llm_testkit import ScriptedProvider, make_posting
from sqlmodel import Session, select

from radar.facts import build_bank, load_facts
from radar.llm import packs, prompts, verify
from radar.llm.provider import LLMError, RateLimitExhausted
from radar.llm.style import AVAILABILITY_SENTENCE
from radar.models import ApplicationPack, Contact, Draft

BANK = build_bank(
    {
        "meta": {
            "decisions": {
                "availability": AVAILABILITY_SENTENCE["en"],
                "availability_it": AVAILABILITY_SENTENCE["it"],
            }
        },
        "identity": {
            "name": "Mara Fittizia",
            "citizenship": "EU citizen",
            "languages": {"italian": "native", "english": "C1"},
        },
        "experience": [
            {
                "id": "acme",
                "org": "Acme",
                "did": ["Built two scrapers in Python."],
                "scale": "about 11,700 members and 2.7 million rows",
            },
            {"id": "secret", "org": "Hushco", "use_in_drafts": False},
        ],
        "education": [{"id": "uni", "school": "Example University", "grades": "not_stated"}],
        "projects": [{"id": "bench", "name": "tinybench", "what": "Agent benchmark, 40 tasks."}],
    }
)


def test_pack_prompt_uses_fact_list_only(session: Session, fake_env: None) -> None:
    posting = make_posting(session)
    system = prompts.pack_system("en", BANK)
    user = prompts.pack_user(prompts.facts_block(BANK), posting, None)
    assert "[experience.acme.did.0] Acme, did: Built two scrapers in Python." in user
    assert "[education.uni.grades]" in user.split("PROHIBITED")[1]
    assert "Hushco" in user.split("PROHIBITED")[1]  # named only as a prohibition
    assert "Hushco" not in user.split("PROHIBITED")[0]
    for rule in ("[CHECK: what to verify]", "ONLY source", 'never "native"', "claims"):
        assert rule in system
    assert AVAILABILITY_SENTENCE["en"] in system
    assert "em dash" in system and "3 or 4 short paragraphs" in system
    assert "salary or" in system  # cover letter never mentions salary
    assert "Headline:" not in user  # no free-text profile in drafting prompts


def test_contact_prompt_uses_fact_list(session: Session, fake_env: None) -> None:
    posting = make_posting(session)
    contact = Contact(name="Giulia Esempio", role="Lead")
    user = prompts.contact_draft_user(prompts.facts_block(BANK), posting, None, contact)
    assert "[projects.bench.name] name: tinybench" in user
    assert "claims" in prompts.contact_draft_system("linkedin_message", "en", BANK)


def test_numeric_precheck() -> None:
    texts = [
        ("cover_letter", "I built two scrapers. My data had 11.700 members and 2.7 million rows."),
        ("form_answers.1", "My benchmark has 40 tasks and reached 93% accuracy."),
        ("form_answers.2", "I can start in 2027 [CHECK: 15 March]."),
        ("linkedin_message", "Your team of 12 engineers works on 3 products."),
    ]
    found = verify.precheck(BANK, texts, posting_text="A team of 12 engineers, 3 products.")
    assert [(c["text_id"], c["verdict"]) for c in found] == [("form_answers.1", "unsupported")]
    assert "93" in found[0]["reason"] and "40" not in found[0]["reason"]
    assert found[0]["source"] == "precheck" and found[0]["fact_id"] is None


def test_number_words_in_facts_allow_digits() -> None:
    assert "2" in verify.allowed_numbers(BANK)  # "two scrapers"
    assert verify.precheck(BANK, [("cover_letter", "I built 2 scrapers in Python.")]) == []


def test_prohibition_precheck() -> None:
    texts = [
        ("cover_letter", "I am a native English speaker."),
        ("form_answers.1", "I speak fluent German."),
        ("form_answers.2", "My GPA is high."),
        ("form_answers.3", "At Hushco I learned a lot."),
        ("form_answers.4", "I speak fluent English (C1)."),
    ]
    found = {c["text_id"]: c for c in verify.precheck(BANK, texts)}
    assert set(found) == {"cover_letter", "form_answers.1", "form_answers.2", "form_answers.3"}
    assert found["cover_letter"]["fact_id"] == "identity.languages.english.level"
    assert found["form_answers.1"]["fact_id"] == "identity.languages.unlisted"
    assert found["form_answers.2"]["fact_id"] == "education.uni.grades"
    assert found["form_answers.3"]["fact_id"] == "experience.secret.excluded"
    assert all(c["verdict"] == "unsupported" for c in found.values())


def test_language_named_in_posting_is_check() -> None:
    found = verify.precheck(
        BANK, [("cover_letter", "The role asks for German.")], "German is required."
    )
    assert found[0]["verdict"] == "check"


def _vjson(*claims: dict[str, object]) -> str:
    return json.dumps({"claims": list(claims)})


def test_parse_verifier() -> None:
    raw = _vjson(
        {"text_id": "cover_letter", "claim": "I built two scrapers.", "fact_id":
         "[experience.acme.did.0]", "verdict": "Supported"},
        {"text_id": "cover_letter", "claim": "I led the team.", "fact_id": None,
         "verdict": "unsupported"},
        {"text_id": "cover_letter", "claim": "Made up id", "fact_id": "experience.zzz",
         "verdict": "supported"},
        {"text_id": "nope", "claim": "Graded 110", "fact_id": "education.uni.grades",
         "verdict": "supported"},
        {"text_id": "cover_letter", "claim": "No id", "verdict": "supported"},
        {"text_id": "cover_letter", "claim": "[CHECK: x]", "verdict": "weird"},
        {"claim": ""},
        "junk",
    )  # fmt: skip
    out = verify.parse_verifier(raw, BANK, ["cover_letter", "linkedin_message"])
    assert [c["verdict"] for c in out] == [
        "supported",
        "unsupported",
        "check",
        "unsupported",
        "check",
        "check",
    ]
    assert out[0]["fact_id"] == "experience.acme.did.0"
    assert out[2]["fact_id"] is None and "sconosciuto" in out[2]["reason"]
    assert out[3]["fact_id"] == "education.uni.grades" and out[3]["text_id"] == "nope"
    with pytest.raises(ValueError):
        verify.parse_verifier('{"result": 1}', BANK, ["cover_letter"])
    with pytest.raises(ValueError):
        verify.parse_verifier("not json", BANK, ["cover_letter"])


def test_merge_precheck_overrides_llm() -> None:
    llm = [
        verify._claim("cover_letter", "I had 93 users", "experience.acme.scale", "supported",
                      "llm"),
        verify._claim("cover_letter", "I built two scrapers.", "experience.acme.did.0",
                      "supported", "llm"),
    ]  # fmt: skip
    pre = [
        verify._claim("cover_letter", "I had 93 users.", None, "unsupported", "precheck", "n"),
        verify._claim("form_answers.1", "GPA 30", "education.uni.grades", "unsupported",
                      "precheck", "g"),
    ]  # fmt: skip
    merged = verify.merge(llm, pre)
    assert [c["verdict"] for c in merged] == ["unsupported", "unsupported", "supported"]
    assert merged[0]["source"] == "llm+precheck" and merged[0]["reason"] == "n"
    assert merged[1]["text_id"] == "form_answers.1"


def test_verify_texts_with_provider() -> None:
    fake = ScriptedProvider(
        [
            "garbage",
            _vjson(
                {"text_id": "cover_letter", "claim": "I built two scrapers in Python.",
                 "fact_id": "experience.acme.did.0", "verdict": "supported"}
            ),
        ]
    )  # fmt: skip
    texts = [("cover_letter", "I built two scrapers in Python. I have 7 awards.")]
    v = verify.verify_texts(BANK, texts, provider=fake, drafter_claims=[{"claim": "x"}])
    assert v["status"] == "ok" and v["error"] is None and v["provider"] == "scripted"
    assert v["counts"] == {"supported": 1, "unsupported": 1, "check": 0, "total": 2}
    assert "INVALID" in fake.prompts[1][1]  # one repair request
    system, user = fake.prompts[0]
    assert system.startswith("[radar-task: verify]")
    assert "### cover_letter" in user and "[experience.acme.did.0]" in user
    assert v["texts_hash"] == verify.texts_hash(texts)


@pytest.mark.parametrize(
    ("error", "expected"),
    [(LLMError("boom"), "verifier failed"), (RateLimitExhausted("day"), "rate_limit")],
)
def test_verify_texts_provider_failure_keeps_precheck(error: Exception, expected: str) -> None:
    v = verify.verify_texts(
        BANK, [("cover_letter", "My GPA is 30.")], provider=ScriptedProvider([error])
    )
    assert v["status"] == "precheck_only" and v["error"].startswith(expected)
    assert v["counts"]["unsupported"] == 1


def test_verify_texts_without_provider() -> None:
    v = verify.verify_texts(BANK, [("cover_letter", "Fine.")], provider=None)
    assert v["status"] == "precheck_only" and v["error"] == "disabled"
    assert v["counts"]["total"] == 0


def test_fake_verifier_is_deterministic() -> None:
    bank = load_facts()
    texts = [
        ("cover_letter", "I built tinybench. I won 3 prizes. [CHECK: team]"),
        ("linkedin_message", "Hi [NOME], what does the team build?"),
    ]
    user = prompts.verify_user(prompts.facts_block(bank), texts)
    out1 = prompts.fake_completion(prompts.verify_system(), user)
    assert out1 == prompts.fake_completion(prompts.verify_system(), user)
    claims = json.loads(out1)["claims"]
    assert [(c["claim"], c["verdict"]) for c in claims] == [
        ("I built tinybench.", "supported"),
        ("I won 3 prizes.", "unsupported"),
        ("[CHECK: team]", "check"),
    ]
    assert claims[0]["fact_id"] == "projects.tinybench.name"


def test_reverify_pack_marks_stale_and_updates(session: Session, fake_env: None) -> None:
    make_posting(session, priority="Alta")
    packs.build_packs(session, run_id=None, limit=5)
    pack = session.exec(select(ApplicationPack)).one()
    assert not verify.is_stale(session, pack)
    pack.cover_letter += "\n\nI am a native English speaker with a GPA of 30."
    session.add(pack)
    session.flush()
    assert verify.is_stale(session, pack)
    v = verify.verify_pack(session, pack, use_llm=False)
    assert v["status"] == "precheck_only" and not verify.is_stale(session, pack)
    bad = [c for c in v["claims"] if c["verdict"] == "unsupported"]
    assert bad and "native" in bad[0]["claim"]
    assert v["drafter_claims"]  # kept from the generation
    note = session.exec(select(Draft).where(Draft.kind == "linkedin_message")).one()
    assert note.verification is not None and not verify.draft_is_stale(note)


def test_contact_draft_is_verified(session: Session, fake_env: None) -> None:
    p = make_posting(session, priority="Alta")
    c = Contact(posting_id=p.id, name="Giulia Esempio", role="Data Lead")
    session.add(c)
    session.commit()
    assert p.id is not None and c.id is not None
    d = packs.draft_for_contact(session, p.id, c.id, "linkedin_message")
    assert d.verification is not None and d.verification["status"] == "ok"
    assert d.verification["drafter_claims"][0]["fact_ids"] == [
        "projects.tinybench.name",
        "projects.tinybench.what",
    ]
    assert all(cl["text_id"] == "linkedin_message" for cl in d.verification["claims"])


def test_verification_disabled_by_setting(
    session: Session, fake_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from radar import config as radar_config

    monkeypatch.setenv("RADAR_VERIFY_DRAFTS", "false")
    radar_config.get_settings.cache_clear()
    make_posting(session, priority="Alta")
    stats = packs.build_packs(session, run_id=None, limit=5)
    assert stats["verify_requests"] == 0
    pack = session.exec(select(ApplicationPack)).one()
    assert pack.verification is not None and pack.verification["status"] == "precheck_only"


def test_native_italian_next_to_english_is_not_flagged() -> None:
    import re

    from radar.facts import NATIVE_ENGLISH_PATTERNS

    ok = "I am fluent in English and native Italian."
    bad = "I speak English at a native level."
    assert not any(re.search(p, ok, re.I) for p in NATIVE_ENGLISH_PATTERNS)
    assert any(re.search(p, bad, re.I) for p in NATIVE_ENGLISH_PATTERNS)


def test_verifier_prompt_carries_posting_context() -> None:
    from radar.llm import prompts

    user = prompts.verify_user("FACTS", [("cover_letter", "Hi")], "Rotations in Zurich")
    assert "POSTING (context only" in user and "Rotations in Zurich" in user
    assert "POSTING" not in prompts.verify_user("FACTS", [("cover_letter", "Hi")])


def test_verifier_uses_its_own_model_on_the_scoring_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from radar.config import get_settings
    from radar.llm.provider import get_provider

    monkeypatch.setenv("RADAR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("RADAR_DRAFT_MODEL", "big-model")
    monkeypatch.setenv("RADAR_VERIFY_MODEL", "small-model")
    get_settings.cache_clear()
    try:
        assert get_provider("verify").model == "small-model"
        assert get_provider("draft").model == "big-model"
    finally:
        get_settings.cache_clear()
