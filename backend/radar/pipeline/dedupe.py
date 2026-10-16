"""Cross-source de-duplication: one primary posting per job, every source listed on it.

The same job can arrive from the company board (ATS), JobTeaser, LinkedIn (search connectors,
manual adds) and email alerts. Each source keeps its own ``postings`` row (``uid`` =
``<source>:<external_id>``, so upserts and closing stay per source); one row of the group is the
**primary** (``duplicate_of`` is NULL) and the others point at it. The primary carries:

* ``sources``: one entry per member row ``{uid, source, url, via, external_id, first_seen,
  last_seen, listed}`` (``listed`` = still listed by that source);
* ``url``/``via``: the best direct link (company board > JobTeaser > LinkedIn > email);
* ``raw_text``: its own text, or a richer member's (``description_from`` = that member's uid).

Matching (see :func:`find_roots`): (a) same ``canonical_url`` (any source); (b) fuzzy: same
company key and country, compatible cities, title similarity >= ``TITLE_THRESHOLD``, only across
different source families (two rows of one ATS board with the same title are two jobs).

Ids are stable: a better source arriving later never replaces the primary, it only improves its
link/description. When two existing groups meet, the one with user work (packs, drafts,
applications, contacts, triage) survives, else the oldest; two groups that both have user work
are never merged.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, select

from radar.models import Application, ApplicationPack, ChangeEvent, Contact, Draft, Posting
from radar.pipeline.normalize import (
    canonical_url,
    cities,
    company_key,
    content_hash,
    dedupe_key,
    title_similarity,
)

ATS_SOURCES = frozenset(
    {
        "greenhouse",
        "lever",
        "ashby",
        "smartrecruiters",
        "workday",
        "personio",
        "recruitee",
        "teamtailor",
        "workable",
        "join",
        "successfactors",
    }
)
LINKEDIN_SOURCES = frozenset({"linkedin", "linkedin_jobs"})
#: Sources that never list "everything": their silence never closes a posting.
NON_EXHAUSTIVE_SOURCES = frozenset({"linkedin", "linkedin_jobs", "email", "seed", "manual"})
TITLE_THRESHOLD = 0.9
RICH_TEXT = 300  # chars: below this a description counts as a snippet
CONTENT_CHANGED = "content changed"

KIND_RANK = {"company": 0, "jobteaser": 1, "linkedin": 2, "email": 3}
KIND_LABEL = {
    "company": "Sito aziendale",
    "jobteaser": "JobTeaser",
    "linkedin": "LinkedIn",
    "email": "Email alert",
}
_VIA_CHANNEL = {
    "company": "Sito aziendale",
    "jobteaser": "Career Service Luiss",
    "linkedin": "LinkedIn",
}


def _host(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0].lower()


def source_kind(source: str, url: str = "") -> str:
    """company | jobteaser | linkedin | email (for ranking and labels)."""
    if source == "email":
        return "email"
    host = _host(url)
    if source in LINKEDIN_SOURCES or host.endswith("linkedin.com"):
        return "linkedin"
    if source == "jobteaser" or host.endswith("jobteaser.com"):
        return "jobteaser"
    return "company"


def source_family(source: str, url: str = "") -> str:
    """Rows of one family are never fuzzy-merged (same platform, different job ids). Rows the
    user tracked by hand (seed, manual) are one family each, whatever the link."""
    if source in ("seed", "manual"):
        return source
    host = _host(url)
    if source in LINKEDIN_SOURCES or host.endswith("linkedin.com"):
        return "linkedin"
    if source == "jobteaser" or host.endswith("jobteaser.com"):
        return "jobteaser"
    return source


def source_rank(source: str, url: str = "") -> int:
    """Lower is better: ATS boards, then seed/manual company links, JobTeaser, LinkedIn, email."""
    kind = source_kind(source, url)
    return KIND_RANK[kind] * 2 + (0 if source in ATS_SOURCES else 1)


def is_exhaustive_source(source: str) -> bool:
    return source not in NON_EXHAUSTIVE_SOURCES


def source_label(source: str, url: str = "") -> str:
    return KIND_LABEL[source_kind(source, url)]


def alert_via(source: str, url: str) -> str | None:
    if source != "email":
        return None
    kind = source_family("", url)
    return f"{kind}_alert" if kind in ("linkedin", "jobteaser") else "email_alert"


def _iso(dt: datetime) -> str:
    return (dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)).isoformat()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def own_url(posting: Posting) -> str:
    """The URL this row's own source gave (the primary's ``url`` may be another member's)."""
    for e in posting.sources or []:
        if e.get("uid") == posting.uid and e.get("url"):
            return str(e["url"])
    return posting.url


def make_entry(
    posting: Posting, *, url: str | None = None, via: str | None = None
) -> dict[str, Any]:
    link = url or own_url(posting)
    return {
        "uid": posting.uid,
        "source": posting.source,
        "url": link,
        "via": via or alert_via(posting.source, link),
        "external_id": posting.external_id,
        "first_seen": _iso(posting.first_seen),
        "last_seen": _iso(posting.last_seen),
        "listed": posting.status == "open",
    }


def public_sources(posting: Posting) -> list[dict[str, Any]]:
    """{source, url, via, label, listed} per source, best first (for the API)."""
    entries = posting.sources or [make_entry(posting)]
    out = []
    for e in sorted(entries, key=_entry_order):
        source, url = str(e["source"]), str(e.get("url") or "")
        out.append(
            {
                "source": source,
                "url": url,
                "via": e.get("via"),
                "label": source_label(source, url),
                "listed": bool(e.get("listed", True)),
            }
        )
    return out


def _entry_order(e: dict[str, Any]) -> tuple[int, str, str]:
    return (source_rank(str(e["source"]), str(e.get("url") or "")), str(e["first_seen"]), e["uid"])


def put_entries(primary: Posting, entries: Iterable[dict[str, Any]]) -> None:
    """Store entries (one per uid, later wins) in a stable order. Always assigns a new list."""
    by_uid: dict[str, dict[str, Any]] = {}
    for e in entries:
        by_uid[str(e["uid"])] = dict(e)
    primary.sources = sorted(by_uid.values(), key=_entry_order)


def members(session: Session, primary: Posting) -> list[Posting]:
    """The primary plus every row pointing at it."""
    rows = session.exec(select(Posting).where(Posting.duplicate_of == primary.id)).all()
    return [primary, *sorted(rows, key=lambda p: p.id or 0)]


def root_of(session: Session, posting: Posting) -> Posting:
    seen: set[int] = set()
    node = posting
    while node.duplicate_of is not None and node.duplicate_of not in seen:
        seen.add(node.duplicate_of)
        parent = session.get(Posting, node.duplicate_of)
        if parent is None:
            break
        node = parent
    return node


def ensure_entries(session: Session, primary: Posting) -> list[dict[str, Any]]:
    """The primary's entries, rebuilt from its member rows when missing ones are found."""
    entries = {str(e["uid"]): e for e in (primary.sources or [])}
    changed = False
    for m in members(session, primary):
        if m.uid not in entries:
            entries[m.uid] = make_entry(m)
            changed = True
    if changed or primary.sources is None:
        put_entries(primary, entries.values())
    return list(primary.sources or [])


def update_entry(primary: Posting, uid: str, **fields: Any) -> None:
    entries = [dict(e) for e in (primary.sources or [])]
    for e in entries:
        if e["uid"] == uid:
            e.update(fields)
    put_entries(primary, entries)


def has_user_work(session: Session, posting: Posting) -> bool:
    """Packs, drafts, applications, contacts, triage or a note: the id must stay visible."""
    if posting.user_status != "todo" or (posting.note or "").strip():
        return True
    pid = posting.id
    checks = (
        select(ApplicationPack.id).where(ApplicationPack.posting_id == pid),
        select(Draft.id).where(Draft.posting_id == pid),
        select(Application.id).where(Application.posting_id == pid),
        select(Contact.id).where(Contact.posting_id == pid),
    )
    return any(session.exec(stmt).first() is not None for stmt in checks)


def _event(
    session: Session,
    posting: Posting,
    kind: str,
    run_id: int | None,
    now: datetime,
    changes: dict[str, list[Any]] | None = None,
) -> None:
    assert posting.id is not None
    session.add(
        ChangeEvent(posting_id=posting.id, run_id=run_id, kind=kind, changes=changes, at=now)
    )


# --- matching -------------------------------------------------------------------------------


def _cities(p: Posting) -> set[str]:
    return cities(p.location) | cities(p.title)


def _cities_compatible(a: Posting, b: Posting) -> bool:
    """Unknown city on either side is fine; otherwise the same set ("Roma / Milano" is not
    the Milan-only requisition)."""
    ca, cb = _cities(a), _cities(b)
    return not ca or not cb or ca == cb


def fuzzy_match(a: Posting, b: Posting) -> bool:
    """Same job by company + country + city + title (different source families only)."""
    if not a.country_code or a.country_code != b.country_code:
        return False
    if source_family(a.source, own_url(a)) == source_family(b.source, own_url(b)):
        return False
    ka = company_key(a.company_name)
    if not ka or ka != company_key(b.company_name):
        return False
    if not _cities_compatible(a, b):
        return False
    return title_similarity(a.title, b.title) >= TITLE_THRESHOLD


def _family_clash(group: Sequence[Posting], newcomer: Posting) -> bool:
    """A fuzzy match must not put two different jobs of one platform/board in one group."""
    fam = source_family(newcomer.source, own_url(newcomer))
    return any(
        m.id != newcomer.id
        and source_family(m.source, own_url(m)) == fam
        and m.canonical_url != newcomer.canonical_url
        for m in group
    )


def find_roots(session: Session, posting: Posting) -> list[Posting]:
    """Primaries of the groups ``posting`` belongs to (not including its own group)."""
    roots: dict[int, Posting] = {}
    by_url = session.exec(
        select(Posting)
        .where(col(Posting.canonical_url) == posting.canonical_url)
        .where(col(Posting.id) != posting.id)
    ).all()
    for m in by_url:
        root = root_of(session, m)
        if root.id is not None and root.id != posting.id:
            roots[root.id] = root
    ckey = company_key(posting.company_name)
    if ckey and posting.country_code and posting.title.strip():
        rows = session.exec(
            select(Posting.id, Posting.company_name)
            .where(Posting.country_code == posting.country_code)
            .where(col(Posting.id) != posting.id)
        ).all()
        for pid, name in rows:
            if company_key(name) != ckey or pid is None:
                continue
            cand = session.get(Posting, pid)
            if cand is None or not fuzzy_match(posting, cand):
                continue
            root = root_of(session, cand)
            if root.id is None or root.id == posting.id or root.id in roots:
                continue
            if _family_clash(members(session, root), posting):
                continue
            roots[root.id] = root
    return list(roots.values())


def survivor(session: Session, roots: Sequence[Posting]) -> Posting:
    """Which existing primary keeps its id: user work first, then the oldest."""
    return min(
        roots,
        key=lambda p: (
            not has_user_work(session, p),
            _aware(p.first_seen),
            source_rank(p.source, own_url(p)),
            p.id or 0,
        ),
    )


def merge_into(session: Session, primary: Posting, other: Posting) -> None:
    """Point ``other`` and its members at ``primary``; their entries move too."""
    entries = ensure_entries(session, primary)
    other_entries = ensure_entries(session, other)
    for child in members(session, other)[1:]:
        child.duplicate_of = primary.id
        session.add(child)
    other.duplicate_of = primary.id
    other.sources = None
    other.description_from = None
    session.add(other)
    put_entries(primary, [*entries, *other_entries])
    session.add(primary)


def sync_primary(
    session: Session,
    primary: Posting,
    run_id: int | None,
    now: datetime,
    *,
    events: bool = True,
) -> bool:
    """Best link/channel and richest description for a group's primary. True if text changed."""
    entries = ensure_entries(session, primary)
    if not entries:
        return False
    listed = [e for e in entries if e.get("listed")] or entries
    best = min(listed, key=_entry_order)
    if best["uid"] == primary.uid:
        primary.url = str(best["url"])
    elif primary.url != best["url"]:
        primary.url = str(best["url"])
        kind = source_kind(str(best["source"]), str(best["url"]))
        if best.get("via") == "jobteaser_alert":
            kind = "jobteaser"
        elif best.get("via") == "linkedin_alert":
            kind = "linkedin"
        primary.via = _VIA_CHANNEL.get(kind, primary.via)
    changed = _sync_description(session, primary)
    session.add(primary)
    if changed and events:
        _event(session, primary, "changed", run_id, now, {"raw_text": [None, CONTENT_CHANGED]})
    return changed


def _sync_description(session: Session, primary: Posting) -> bool:
    group = members(session, primary)
    provider: Posting | None = None
    if primary.description_from and primary.description_from != primary.uid:
        provider = next((m for m in group if m.uid == primary.description_from), None)
    else:
        own = len(primary.raw_text or "")
        if own < RICH_TEXT:
            rich = [
                m
                for m in group[1:]
                if len(m.raw_text or "") >= max(RICH_TEXT, 2 * own) and m.status == "open"
            ]
            if rich:
                provider = min(
                    rich, key=lambda m: (source_rank(m.source, m.url), -len(m.raw_text or ""))
                )
    if provider is None or not provider.raw_text:
        return False
    if primary.description_from is None:
        # keep the primary's own (short) text with its entry, so nothing is lost
        update_entry(primary, primary.uid, own_text=primary.raw_text or "")
    primary.description_from = provider.uid
    if primary.raw_text == provider.raw_text:
        return False
    primary.raw_text = provider.raw_text
    primary.content_hash = content_hash(
        primary.title, primary.company_name, primary.location, primary.raw_text
    )
    return True


def link_new(
    session: Session,
    posting: Posting,
    run_id: int | None,
    now: datetime,
    *,
    via: str | None = None,
) -> Posting:
    """Attach a just-inserted posting to its group (or start one). Returns the primary."""
    own = make_entry(posting, via=via)
    roots = find_roots(session, posting)
    if not roots:
        put_entries(posting, [own])
        session.add(posting)
        session.flush()
        return posting
    primary = survivor(session, roots)
    primary_work = has_user_work(session, primary)
    for other in roots:
        if other.id == primary.id:
            continue
        if primary_work and has_user_work(session, other):
            continue  # two groups with user work: never hide one of them
        merge_into(session, primary, other)
    entries = ensure_entries(session, primary)
    posting.duplicate_of = primary.id
    posting.sources = None
    session.add(posting)
    put_entries(primary, [*entries, own])
    session.flush()
    sync_primary(session, primary, run_id, now)
    if primary.status == "closed" and posting.status == "open":
        reopen(session, primary, run_id, now)
    session.flush()
    return primary


def reopen(session: Session, primary: Posting, run_id: int | None, now: datetime) -> None:
    primary.status = "open"
    primary.closed_at = None
    primary.last_seen = now
    session.add(primary)
    session.flush()
    _event(session, primary, "reopened", run_id, now)


def covered_by_board(primary: Posting) -> bool:
    """An exhaustive source still lists it (its absence will close it; no URL check needed)."""
    return any(
        e.get("listed") and is_exhaustive_source(str(e["source"])) for e in primary.sources or []
    )


def all_exhaustive_gone(primary: Posting) -> bool:
    """True when the group has exhaustive sources and none of them lists it any more."""
    exhaustive = [e for e in primary.sources or [] if is_exhaustive_source(str(e["source"]))]
    return bool(exhaustive) and not any(e.get("listed") for e in exhaustive)


# --- backfill -------------------------------------------------------------------------------

_TRACKED = ("canonical_url", "dedupe_key", "duplicate_of", "sources", "url", "via", "raw_text")


def _snapshot(p: Posting) -> tuple[Any, ...]:
    return tuple(getattr(p, f) for f in _TRACKED)


def backfill(
    session: Session, *, apply: bool = False, now: datetime | None = None
) -> dict[str, int]:
    """Re-key every posting and merge duplicate groups the new matching finds.

    Idempotent (a second run changes nothing); never deletes rows; never hides a posting with
    user work behind another one that also has user work. ``apply=False`` (dry run) rolls back.
    Returns counts only.
    """
    now = now or datetime.now(UTC)
    rows = list(session.exec(select(Posting).order_by(col(Posting.id))).all())
    before = {p.id: _snapshot(p) for p in rows}
    counts = {
        "postings": len(rows),
        "rekeyed": 0,
        "groups_merged": 0,
        "marked_duplicate": 0,
        "conflicts_skipped": 0,
        "primaries_updated": 0,
        "descriptions_upgraded": 0,
        "changed_rows": 0,
    }
    by_id = {p.id: p for p in rows if p.id is not None}

    for p in rows:
        canon = canonical_url(own_url(p)) if own_url(p).startswith("http") else p.canonical_url
        key = dedupe_key(p.company_name, p.title, p.country_code)
        if (canon, key) != (p.canonical_url, p.dedupe_key):
            counts["rekeyed"] += 1
            p.canonical_url, p.dedupe_key = canon, key

    def root_id(p: Posting) -> int:
        seen: set[int] = set()
        node = p
        while node.duplicate_of is not None and node.duplicate_of in by_id:
            if node.duplicate_of in seen:
                break
            seen.add(node.duplicate_of)
            node = by_id[node.duplicate_of]
        assert node.id is not None
        return node.id

    groups: dict[int, list[Posting]] = {}
    for p in rows:
        groups.setdefault(root_id(p), []).append(p)
    work = {gid: any(has_user_work(session, m) for m in g) for gid, g in groups.items()}

    order = sorted(groups, key=lambda gid: (_aware(by_id[gid].first_seen), gid))
    final: dict[int, list[Posting]] = {}
    url_index: dict[str, int] = {}
    bucket: dict[tuple[str, str], list[tuple[Posting, int]]] = {}
    owner: dict[int, int] = {}  # group id -> final group id

    def resolve(gid: int) -> int:
        while owner.get(gid, gid) != gid:
            gid = owner[gid]
        return gid

    for gid in order:
        group = groups[gid]
        target: int | None = None
        for m in group:
            hit = url_index.get(m.canonical_url)
            if hit is not None:
                target = resolve(hit)
                break
        if target is None:
            for m in group:
                bkey = (company_key(m.company_name), m.country_code or "")
                for cand, cgid in bucket.get(bkey, []):
                    tg = resolve(cgid)
                    if fuzzy_match(m, cand) and not _family_clash(final[tg], m):
                        target = tg
                        break
                if target is not None:
                    break
        if target is not None and work[target] and work[gid]:
            counts["conflicts_skipped"] += 1
            target = None
        if target is None:
            final[gid] = list(group)
            dest = gid
        else:
            keep = gid if (work[gid] and not work[target]) else target
            lose = target if keep == gid else gid
            final[keep] = [*final.pop(target), *group]
            owner[lose] = keep
            work[keep] = work[target] or work[gid]
            counts["groups_merged"] += 1
            dest = keep
        for m in group:
            url_index.setdefault(m.canonical_url, dest)
            if m.country_code:
                bkey = (company_key(m.company_name), m.country_code)
                bucket.setdefault(bkey, []).append((m, dest))

    for gid, group in final.items():
        primary = by_id[gid]
        old_entries = {str(e["uid"]): e for m in group for e in (m.sources or [])}
        entries = []
        for m in group:
            entries.append(old_entries.get(m.uid) or make_entry(m))
            if m.id != primary.id:
                if m.duplicate_of != primary.id:
                    counts["marked_duplicate"] += 1
                m.duplicate_of = primary.id
                m.sources = None
                m.description_from = None
                session.add(m)
        primary.duplicate_of = None
        put_entries(primary, entries)
        session.add(primary)
    session.flush()
    for gid, group in final.items():
        primary = by_id[gid]
        if len(group) > 1 and sync_primary(session, primary, None, now, events=apply):
            counts["descriptions_upgraded"] += 1
    session.flush()
    for p in rows:
        if _snapshot(p) != before[p.id]:
            counts["changed_rows"] += 1
            if p.duplicate_of is None and len(p.sources or []) > 1:
                counts["primaries_updated"] += 1
    if apply:
        session.commit()
    else:
        session.rollback()
    return counts
