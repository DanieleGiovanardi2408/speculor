"""Fact verification of drafts: a deterministic pre-check plus a second LLM call (the verifier).

The verifier receives the draft texts and the fact list and returns, for every claim sentence,
``{text_id, claim, fact_id | null, verdict: supported | unsupported | check}``. The pre-check
marks as unsupported, without any LLM:

* any number in a text that appears neither in the fact bank (digits, or number words such as
  "two") nor in the posting (claims about the company may quote it);
* any match of a prohibition pattern from the fact bank: "native" English when the level is not
  native, languages that are not listed, GPA or grades when they are ``not_stated``, the name of
  an item with ``use_in_drafts: false``. A language that the posting itself mentions becomes
  ``check`` instead (the text may only be quoting the requirement).

Texts are never rewritten: unsupported claims are only marked in the stored result
(``ApplicationPack.verification`` / ``Draft.verification``) and shown in the *Verifica fatti*
panel. Wrapping them in ``[CHECK: ...]`` automatically could break sentences or hide a true
statement the fact bank forgot, so the user decides.

Stored shape (``VERIFICATION_VERSION`` = 1)::

    {"version": 1, "status": "ok" | "precheck_only", "verified_at": ISO-8601 UTC,
     "provider": str | null, "model": str | null, "error": str | null,
     "facts_source": str, "facts_version": str | null, "texts_hash": str,
     "counts": {"supported": int, "unsupported": int, "check": int, "total": int},
     "claims": [{"text_id": str, "claim": str, "fact_id": str | null,
                 "verdict": "supported" | "unsupported" | "check",
                 "source": "llm" | "precheck" | "llm+precheck", "reason": str | null}],
     "drafter_claims": [{"claim": str, "fact_ids": [str]}]}

Logs are counts-only.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from typing import Any, Literal

from sqlmodel import Session, col, select

from radar.config import get_settings
from radar.facts import FactBank, load_facts, select_facts
from radar.llm import prompts
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
from radar.models import ApplicationPack, Draft, Posting
from radar.timeutil import utcnow

log = logging.getLogger(__name__)

VERIFICATION_VERSION = 1
VERIFY_MAX_TOKENS = 2000
VERIFY_FACTS_CHARS = 14000  # facts sent to the verifier on Groq (core + most relevant)
VERDICTS = ("supported", "unsupported", "check")
Verdict = Literal["supported", "unsupported", "check"]
TextItem = tuple[str, str]  # (text_id, text)

_NUM = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*")
_THOUSANDS = re.compile(r"\d{1,3}(?:[.,]\d{3})+")
_MARKERS = re.compile(r"\[(?:CHECK:[^\]]*|NOME)\]", re.I)
_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "twenty": "20",
    "hundred": "100", "uno": "1", "due": "2", "tre": "3", "quattro": "4", "cinque": "5",
    "sei": "6", "sette": "7", "otto": "8", "nove": "9", "dieci": "10", "venti": "20",
}  # fmt: skip
# Process numbers that are not claims about the candidate ("14 days after applying").
_KIND_NUMBERS: dict[str, frozenset[str]] = {"followup": frozenset({"14", "2"})}


# --------------------------------------------------------------------------- helpers


def norm_number(raw: str) -> str:
    if _THOUSANDS.fullmatch(raw):
        return re.sub(r"[.,]", "", raw)
    return raw.replace(",", ".")


def numbers_in(text: str) -> set[str]:
    return {norm_number(m.group(0)) for m in _NUM.finditer(text)}


def allowed_numbers(bank: FactBank, extra_text: str = "") -> set[str]:
    text = bank.facts_text
    out = numbers_in(text) | numbers_in(extra_text)
    lowered = text.lower()
    for word, digit in _NUMBER_WORDS.items():
        if re.search(rf"(?<!\w){word}(?!\w)", lowered):
            out.add(digit)
    return out


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text) if s and s.strip()]


def _norm_claim(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


def texts_hash(texts: Sequence[TextItem]) -> str:
    h = hashlib.sha256()
    for tid, text in texts:
        h.update(tid.encode())
        h.update(b"\0")
        h.update(text.strip().encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


def counts(claims: Sequence[dict[str, Any]]) -> dict[str, int]:
    out = {v: 0 for v in VERDICTS}
    for c in claims:
        out[c["verdict"]] = out.get(c["verdict"], 0) + 1
    out["total"] = len(claims)
    return out


def _claim(
    text_id: str,
    claim: str,
    fact_id: str | None,
    verdict: str,
    source: str,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "text_id": text_id,
        "claim": claim,
        "fact_id": fact_id,
        "verdict": verdict,
        "source": source,
        "reason": reason,
    }


def normalize_drafter_claims(raw: Any) -> list[dict[str, Any]]:
    """The drafter's ``claims`` list, made safe to store (bad items dropped, sizes capped)."""
    out: list[dict[str, Any]] = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict) or not str(c.get("claim") or "").strip():
            continue
        ids = c.get("fact_ids", c.get("fact_id")) or []
        if isinstance(ids, str):
            ids = [ids]
        if not isinstance(ids, list):
            ids = []
        out.append(
            {"claim": str(c["claim"]).strip()[:500], "fact_ids": [str(i) for i in ids if i][:10]}
        )
    return out[:60]


# --------------------------------------------------------------------------- pre-check


def precheck(
    bank: FactBank, texts: Sequence[TextItem], posting_text: str = ""
) -> list[dict[str, Any]]:
    """Deterministic findings: unknown numbers and prohibited mentions, one per sentence."""
    allowed = allowed_numbers(bank, posting_text)
    posting_lower = posting_text.lower()
    compiled = [
        (p, [re.compile(rx, re.I) for rx in p.patterns]) for p in bank.prohibitions if p.patterns
    ]
    out: list[dict[str, Any]] = []
    for tid, text in texts:
        kind_ok = _KIND_NUMBERS.get(tid, frozenset())
        for sentence in sentences(text):
            clean = _MARKERS.sub(" ", sentence)
            reasons: list[str] = []
            fact_id: str | None = None
            verdict = "unsupported"
            unknown = sorted(n for n in numbers_in(clean) if n not in allowed and n not in kind_ok)
            if unknown:
                reasons.append(f"numero non presente nei fatti: {', '.join(unknown)}")
            for p, regexes in compiled:
                hits = [m.group(0) for rx in regexes if (m := rx.search(clean))]
                if not hits:
                    continue
                fact_id = p.id
                if p.id == "identity.languages.unlisted" and all(
                    h.lower() in posting_lower for h in hits
                ):
                    if not reasons:
                        verdict = "check"
                    reasons.append(
                        f'lingua citata anche nell\'annuncio ("{hits[0]}"): '
                        "verifica che non sia attribuita al candidato"
                    )
                else:
                    reasons.append(f'viola un divieto ("{hits[0]}"): {p.text}')
            if reasons:
                out.append(_claim(tid, sentence, fact_id, verdict, "precheck", "; ".join(reasons)))
    return out


# --------------------------------------------------------------------------- verifier output


# Sentences that are not claims about the candidate: questions, greetings, interest/politeness.
_NON_CLAIM = re.compile(
    r"^\s*(?:hi|hello|dear|ciao|gentile|buongiorno)\b[^.!?]*[,!]?\s*$"
    r"|\?\s*$"
    r"|^\s*(?:i am|i'm) (?:interested|curious|keen)\b"
    r"|^\s*i would (?:welcome|love|be glad|be happy)\b"
    r"|^\s*(?:would you|could you|can you|may i)\b"
    r"|^\s*(?:hi|hello|ciao) \[nome\],?\s*i (?:applied|recently applied|sent)\b"
    r"|^\s*i (?:applied|recently applied) for\b"
    r"|^\s*(?:sono interessat[oa]|mi piacerebbe|potrebbe|potresti)\b",
    re.I,
)


def is_non_claim(sentence: str) -> bool:
    return bool(_NON_CLAIM.search(sentence.strip()))


def parse_verifier(raw: str, bank: FactBank, text_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Validate the verifier JSON. Raises ValueError when it is not usable at all."""
    data = extract_json(raw)
    items = data.get("claims")
    if not isinstance(items, list):
        raise ValueError("missing 'claims' list")
    known = set(text_ids)
    out: list[dict[str, Any]] = []
    unrecognized = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        if not claim or is_non_claim(claim):
            continue
        tid = str(item.get("text_id") or "").strip()
        if tid not in known:
            tid = text_ids[0] if len(text_ids) == 1 else (tid or "unknown")
        fid_raw = item.get("fact_id")
        fact_id = str(fid_raw).strip().strip("[]") if fid_raw not in (None, "", "null") else None
        verdict = (
            str(item.get("verdict") or item.get("status") or item.get("result") or "")
            .strip()
            .lower()
        )
        reason: str | None = None
        if verdict not in VERDICTS:
            unrecognized += 1
            verdict = "unsupported" if "unsupported" in verdict or "not" in verdict else "check"
        if "[CHECK" in claim.upper() and verdict == "supported":
            verdict = "check"
        if fact_id is not None and bank.prohibition(fact_id) is not None:
            verdict, reason = "unsupported", "viola un divieto del fact bank"
        elif verdict == "supported" and fact_id is None:
            verdict, reason = "check", "nessun fatto indicato dal verificatore"
        elif fact_id is not None and bank.get(fact_id) is None:
            if verdict == "supported":
                verdict = "check"
            reason = f"id fatto sconosciuto: {fact_id}"
            fact_id = None
        out.append(_claim(tid, claim, fact_id, verdict, "llm", reason))
    if out and unrecognized * 2 > len(out):
        raise ValueError("verifier verdicts not recognised")  # -> one repair request
    return out


def merge(llm: list[dict[str, Any]], pre: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pre-check findings override the matching LLM claim (same text, overlapping sentence)."""
    merged = [dict(c) for c in llm]
    for finding in pre:
        target = _norm_claim(finding["claim"])
        match = next(
            (
                c
                for c in merged
                if c["text_id"] == finding["text_id"]
                and (n := _norm_claim(c["claim"]))
                and (n in target or target in n)
            ),
            None,
        )
        if match is None:
            merged.append(dict(finding))
            continue
        if finding["verdict"] == "unsupported" or match["verdict"] == "supported":
            match["verdict"] = finding["verdict"]
        if finding["fact_id"]:
            match["fact_id"] = finding["fact_id"]
        match["reason"] = "; ".join(r for r in (match.get("reason"), finding["reason"]) if r)
        match["source"] = "llm+precheck"
    order = {"unsupported": 0, "check": 1, "supported": 2}
    return sorted(merged, key=lambda c: order.get(c["verdict"], 3))


# --------------------------------------------------------------------------- verification


def _facts_budget(provider: LLMProvider | None) -> int | None:
    return None if provider is not None and provider.name == "anthropic" else VERIFY_FACTS_CHARS


def verify_texts(
    bank: FactBank,
    texts: Sequence[TextItem],
    *,
    provider: LLMProvider | None,
    posting_text: str = "",
    results: list[LLMResult] | None = None,
    drafter_claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pre-check + (when ``provider`` is given) the LLM verifier. Never raises for LLM errors:
    the result then has ``status: "precheck_only"`` and ``error`` (``"rate_limit"`` means the
    caller should stop making requests)."""
    texts = [(tid, t) for tid, t in texts if t and t.strip()]
    pre = precheck(bank, texts, posting_text)
    llm_claims: list[dict[str, Any]] = []
    status, error = "precheck_only", None
    used_provider = used_model = None
    if provider is None:
        error = "disabled"
    elif texts:
        budget = _facts_budget(provider)
        facts = select_facts(bank, "\n".join(t for _, t in texts), budget or 10**9)
        user = prompts.verify_user(prompts.facts_block(bank, facts), texts, posting_text)
        used_provider, used_model = provider.name, provider.model
        try:
            r = provider.complete(
                prompts.verify_system(),
                user,
                model=provider.model,
                max_tokens=VERIFY_MAX_TOKENS,
                temperature=0.0,
                json_mode=True,
            )
            if results is not None:
                results.append(r)
            try:
                llm_claims = parse_verifier(r.text, bank, [tid for tid, _ in texts])
            except ValueError as exc:
                r = provider.complete(
                    prompts.verify_system(),
                    user + prompts.repair_suffix(r.text, str(exc)),
                    model=provider.model,
                    max_tokens=VERIFY_MAX_TOKENS,
                    temperature=0.0,
                    json_mode=True,
                )
                if results is not None:
                    results.append(r)
                llm_claims = parse_verifier(r.text, bank, [tid for tid, _ in texts])
            status = "ok"
        except RateLimitExhausted:
            error = "rate_limit"
        except LLMError as exc:
            error = f"verifier failed: {exc}"[:200]
        except ValueError:
            error = "verifier output not valid"
    else:
        status = "ok"
    claims = merge(llm_claims, pre)
    return {
        "version": VERIFICATION_VERSION,
        "status": status,
        "verified_at": utcnow().isoformat(),
        "provider": used_provider,
        "model": used_model,
        "error": error,
        "facts_source": bank.source,
        "facts_version": bank.version,
        "texts_hash": texts_hash(texts),
        "counts": counts(claims),
        "claims": claims,
        "drafter_claims": list(drafter_claims or []),
    }


def keep_previous_llm(previous: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """A re-check whose LLM call failed (rate limit, error) must not wipe a complete earlier
    check of the same texts: keep the earlier LLM claims and apply the new pre-check on top."""
    if (
        new.get("status") != "precheck_only"
        or new.get("error") in (None, "disabled")
        or previous.get("status") != "ok"
        or previous.get("texts_hash") != new.get("texts_hash")
    ):
        return new
    old_llm = [
        {**c, "source": "llm"}
        for c in previous.get("claims", [])
        if str(c.get("source", "")).startswith("llm")
    ]
    pre = [c for c in new.get("claims", []) if c.get("source") == "precheck"]
    claims = merge(old_llm, pre)
    out = dict(previous)
    out.update(
        claims=claims,
        counts=counts(claims),
        facts_source=new.get("facts_source"),
        facts_version=new.get("facts_version"),
        precheck_at=new.get("verified_at"),
        note=f"verificatore LLM non disponibile ({new.get('error')}): risultato LLM precedente",
    )
    return out


def subset(verification: dict[str, Any], text_id: str, text: str) -> dict[str, Any]:
    """The part of a pack verification that concerns one text (stored on Draft rows)."""
    claims = [c for c in verification.get("claims", []) if c.get("text_id") == text_id]
    out = dict(verification)
    out["claims"] = claims
    out["counts"] = counts(claims)
    out["texts_hash"] = texts_hash([(text_id, text)])
    out["drafter_claims"] = []
    return out


# --------------------------------------------------------------------------- packs


def pack_texts(
    cover_letter: str, form_answers: Sequence[dict[str, str]], drafts: dict[str, str]
) -> list[TextItem]:
    texts: list[TextItem] = [("cover_letter", cover_letter)]
    texts += [(f"form_answers.{i}", a.get("answer", "")) for i, a in enumerate(form_answers, 1)]
    texts += list(drafts.items())
    return texts


def _generic_drafts(session: Session, pack: ApplicationPack) -> list[Draft]:
    """The pack's own outreach drafts (not contact drafts), newest per kind."""
    rows = session.exec(
        select(Draft)
        .where(Draft.pack_id == pack.id, col(Draft.contact_id).is_(None))
        .order_by(col(Draft.id))
    ).all()
    by_kind: dict[str, Draft] = {}
    for d in rows:
        if d.channel == "linkedin" and d.kind != "email":
            by_kind[d.kind] = d
    return list(by_kind.values())


def current_pack_texts(session: Session, pack: ApplicationPack) -> list[TextItem]:
    drafts = {d.kind: d.text for d in _generic_drafts(session, pack)}
    return pack_texts(pack.cover_letter, pack.form_answers or [], drafts)


def is_stale(session: Session, pack: ApplicationPack) -> bool:
    v = pack.verification
    if not isinstance(v, dict):
        return False
    texts = [(t, x) for t, x in current_pack_texts(session, pack) if x and x.strip()]
    return v.get("texts_hash") != texts_hash(texts)


def draft_is_stale(draft: Draft) -> bool:
    v = draft.verification
    if not isinstance(v, dict):
        return False
    return v.get("texts_hash") != texts_hash([(draft.kind, draft.text)])


def posting_text(posting: Posting | None) -> str:
    if posting is None:
        return ""
    parts = [posting.title, posting.company_name, posting.location or "", posting.start_info or ""]
    if posting.source != "seed":
        parts.append(posting.raw_text or "")
    return "\n".join(parts)


def store_pack_verification(
    session: Session, pack: ApplicationPack, verification: dict[str, Any]
) -> None:
    pack.verification = verification
    session.add(pack)
    for d in _generic_drafts(session, pack):
        d.verification = subset(verification, d.kind, d.text)
        session.add(d)
    session.flush()


def verifier_provider() -> LLMProvider | None:
    """The verifier provider (scoring provider, RADAR_VERIFY_MODEL) when verification is
    enabled, else None (pre-check only)."""
    if not get_settings().verify_drafts:
        return None
    try:
        return get_provider("verify")
    except MissingKeyError:
        return None


def verify_pack(
    session: Session,
    pack: ApplicationPack,
    *,
    provider: LLMProvider | None = None,
    use_llm: bool = True,
    bank: FactBank | None = None,
) -> dict[str, Any]:
    """Re-verify the current texts of ``pack`` (and its outreach drafts). Flushes, no commit."""
    bank = bank or load_facts()
    if use_llm and provider is None:
        provider = verifier_provider()
    posting = session.get(Posting, pack.posting_id)
    texts = current_pack_texts(session, pack)
    previous = pack.verification if isinstance(pack.verification, dict) else {}
    results: list[LLMResult] = []
    try:
        verification = verify_texts(
            bank,
            texts,
            provider=provider if use_llm else None,
            posting_text=posting_text(posting),
            results=results,
            drafter_claims=previous.get("drafter_claims") or [],
        )
    finally:
        for r in results:
            record_usage(session, None, "verify", r)
    verification = keep_previous_llm(previous, verification)
    store_pack_verification(session, pack, verification)
    log.info(
        "verify: pack checked, %d claims (%d unsupported)",
        verification["counts"]["total"],
        verification["counts"]["unsupported"],
    )
    return verification


def verify_draft(
    session: Session,
    draft: Draft,
    *,
    provider: LLMProvider | None = None,
    use_llm: bool = True,
    bank: FactBank | None = None,
    drafter_claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify one Draft row (contact drafts). Flushes, no commit."""
    bank = bank or load_facts()
    if use_llm and provider is None:
        provider = verifier_provider()
    posting = session.get(Posting, draft.posting_id) if draft.posting_id else None
    results: list[LLMResult] = []
    try:
        verification = verify_texts(
            bank,
            [(draft.kind, draft.text)],
            provider=provider if use_llm else None,
            posting_text=posting_text(posting),
            results=results,
            drafter_claims=drafter_claims,
        )
    finally:
        for r in results:
            record_usage(session, None, "verify", r)
    draft.verification = verification
    session.add(draft)
    session.flush()
    return verification
