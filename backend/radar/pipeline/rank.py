"""Free local pre-ranking: embedding similarity between each posting and the user's profile.

The profile side is a handful of short chunks (target roles from profile.yaml, then each
experience, project and skill group from config/facts.yaml, items with ``use_in_drafts: false``
skipped; profile.yaml's own experience/projects/skills if there is no fact bank). The posting
side is title plus the start of the text. ``Posting.similarity`` (0..1) orders the LLM scoring
queue, so the limited free budget goes to the closest postings first.

Embeddings are cached on disk by content hash (data/cache/embeddings.sqlite), so a daily run
only embeds new or changed postings. ``Posting.similarity_version`` is a hash of the model and
the profile chunks: when facts or targets change every similarity is recomputed (cheap: the
posting vectors are cached, only the profile is re-embedded).

The model runs locally through the optional ``embed`` extra (sentence-transformers). Without it
ranking is skipped with one log line and the previous ordering is kept.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import sqlite3
from array import array
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, col, select

from radar import config as radar_config
from radar.models import Posting

log = logging.getLogger(__name__)

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
RANKABLE_FILTER_STATES = ("pass", "flagged", "overridden")
TEXT_VERSION = "t1"  # bump when posting_text() changes, to invalidate cached posting vectors
POSTING_CHARS = 1500
CHUNK_CHARS = 600
TOP_K = 3
ROLE_WEIGHT = 0.5  # similarity = ROLE_WEIGHT * best target role + rest * mean top-k fact chunks
# Keys never embedded (dates, ids, what must not be claimed).
_SKIP_KEYS = frozenset(
    {
        "id",
        "dates",
        "period",
        "location",
        "do_not_claim",
        "honest_status",
        "use_in_drafts",
        "links",
        "tags",
    }
)


class Embedder(Protocol):
    """Returns one L2-normalized vector per text. ``query`` marks profile-side texts."""

    model_id: str

    def embed(self, texts: Sequence[str], *, query: bool) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Local sentence-transformers model (CPU or Apple MPS), loaded on first use."""

    def __init__(self, model_name: str = DEFAULT_MODEL, cache_folder: Path | None = None) -> None:
        self.model_id = model_name
        self._cache_folder = cache_folder
        self._model: Any = None
        # E5 models are trained with these prefixes; others take raw text.
        self._prefixes = ("query: ", "passage: ") if "e5" in model_name.lower() else ("", "")

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            folder = str(self._cache_folder) if self._cache_folder else None
            self._model = SentenceTransformer(self.model_id, cache_folder=folder)
        return self._model

    def embed(self, texts: Sequence[str], *, query: bool) -> list[list[float]]:
        if not texts:
            return []
        prefix = self._prefixes[0] if query else self._prefixes[1]
        vectors = self._load().encode(
            [prefix + t for t in texts],
            batch_size=32,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[float(x) for x in v] for v in vectors]


class HashingEmbedder:
    """Deterministic bag-of-words hashing vectors: offline, for tests and demos."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.model_id = f"hashing-{dim}"
        self.calls = 0  # number of texts embedded, to check the cache in tests

    def embed(self, texts: Sequence[str], *, query: bool) -> list[list[float]]:
        self.calls += len(texts)
        out: list[list[float]] = []
        for text in texts:
            v = [0.0] * self.dim
            for token in re.findall(r"\w+", text.lower()):
                h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big")
                v[h % self.dim] += 1.0
            out.append(_normalize(v))
        return out


def load_default_embedder() -> Embedder | None:
    """The real model if the ``embed`` extra is installed, else None (one log line)."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        log.info("ranking skipped: embed extra not installed (uv sync --extra embed)")
        return None
    # The model itself lives in the shared Hugging Face cache (~/.cache/huggingface).
    return SentenceTransformerEmbedder(radar_config.get_settings().embed_model)


# --------------------------------------------------------------------------- cache


class EmbeddingCache:
    """Vectors on disk, keyed by (model, key). Float32 blobs in a tiny SQLite file."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS vectors "
            "(model TEXT NOT NULL, key TEXT NOT NULL, vec BLOB NOT NULL, PRIMARY KEY (model, key))"
        )

    def get_many(self, model: str, keys: Iterable[str]) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        wanted = list(dict.fromkeys(keys))
        for i in range(0, len(wanted), 500):
            part = wanted[i : i + 500]
            marks = ",".join("?" * len(part))
            rows = self._db.execute(
                f"SELECT key, vec FROM vectors WHERE model = ? AND key IN ({marks})",
                [model, *part],
            )
            for key, blob in rows:
                out[key] = array("f", blob).tolist()
        return out

    def put_many(self, model: str, items: dict[str, list[float]]) -> None:
        self._db.executemany(
            "INSERT OR REPLACE INTO vectors (model, key, vec) VALUES (?, ?, ?)",
            [(model, k, array("f", v).tobytes()) for k, v in items.items()],
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()


# --------------------------------------------------------------------------- texts


def _strings(value: Any, key: str | None = None) -> list[str]:
    if key in _SKIP_KEYS:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, int | float) and not isinstance(value, bool):
        return []
    if isinstance(value, dict):
        if value.get("use_in_drafts") is False:
            return []
        return [s for k, v in value.items() for s in _strings(v, str(k))]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _chunk(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())[:CHUNK_CHARS]


def _section_chunks(source: dict[str, Any]) -> list[str]:
    chunks: list[str] = []
    for section in ("experience", "projects"):
        items = source.get(section)
        for item in items if isinstance(items, list) else []:
            text = _chunk(_strings(item))
            if text:
                chunks.append(text)
    skills = source.get("skills")
    if isinstance(skills, dict):
        for group in skills.values():
            text = _chunk(_strings(group))
            if text:
                chunks.append(text)
    elif isinstance(skills, list):
        text = _chunk(_strings(skills))
        if text:
            chunks.append(text)
    return chunks


def profile_chunks(profile: dict[str, Any], facts: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(target role chunks, fact chunks). Facts fall back to profile.yaml's own sections."""
    roles = [r for r in _strings(profile.get("target_roles")) if r]
    headline = profile.get("headline")
    if not roles and isinstance(headline, str) and headline.strip():
        roles = [headline.strip()]
    facts_chunks = _section_chunks(facts) or _section_chunks(profile)
    return roles, facts_chunks


def load_facts() -> dict[str, Any]:
    try:
        return radar_config.load_yaml_config("facts")
    except (FileNotFoundError, ValueError):
        return {}


def posting_text(p: Posting) -> str:
    body = " ".join((p.raw_text or "").split())[:POSTING_CHARS]
    return f"{p.title}. {body}".strip()


def profile_version(model_id: str, roles: list[str], facts: list[str]) -> str:
    payload = "\n".join([model_id, TEXT_VERSION, *roles, "--", *facts])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- math


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def similarity(vec: list[float], roles: list[list[float]], facts: list[list[float]]) -> float:
    """Best target role and mean of the top-k fact chunks, cosine clamped to 0..1."""
    parts: list[tuple[float, float]] = []
    if roles:
        parts.append((ROLE_WEIGHT, max(_dot(vec, r) for r in roles)))
    if facts:
        top = sorted((_dot(vec, f) for f in facts), reverse=True)[:TOP_K]
        parts.append((1 - ROLE_WEIGHT, sum(top) / len(top)))
    if not parts:
        return 0.0
    total = sum(w for w, _ in parts)
    value = sum(w * s for w, s in parts) / total
    return round(min(1.0, max(0.0, value)), 4)


# --------------------------------------------------------------------------- ranking


def rankable_postings(session: Session) -> list[Posting]:
    return list(
        session.exec(
            select(Posting).where(
                Posting.status == "open",
                col(Posting.filter_state).in_(RANKABLE_FILTER_STATES),
                col(Posting.duplicate_of).is_(None),
                Posting.content_hash != "",
            )
        ).all()
    )


def rank_postings(
    session: Session,
    embedder: Embedder | None = None,
    cache: EmbeddingCache | None = None,
    *,
    profile: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute ``similarity`` for every open posting that passed the filters.

    Returns counts only. Without an embedder (extra missing) nothing changes."""
    embedder = embedder or load_default_embedder()
    if embedder is None:
        return {"skipped": "embed extra not installed"}
    profile = profile if profile is not None else radar_config.load_yaml_config("profile")
    facts = facts if facts is not None else load_facts()
    roles, fact_chunks = profile_chunks(profile, facts)
    if not roles and not fact_chunks:
        log.info("ranking skipped: empty profile")
        return {"skipped": "empty profile"}
    own_cache = cache is None
    cache = cache or EmbeddingCache(radar_config.get_settings().cache_dir / "embeddings.sqlite")
    try:
        version = profile_version(embedder.model_id, roles, fact_chunks)
        role_vecs, fact_vecs = _profile_vectors(embedder, cache, roles, fact_chunks)
        postings = rankable_postings(session)
        keys = {p.id: f"{TEXT_VERSION}:{p.content_hash}" for p in postings}
        vectors = cache.get_many(embedder.model_id, keys.values())
        cached = sum(1 for k in set(keys.values()) if k in vectors)
        todo: dict[str, str] = {}
        for p in postings:
            key = keys[p.id]
            if key not in vectors and key not in todo:
                todo[key] = posting_text(p)
        if todo:
            new = embedder.embed(list(todo.values()), query=False)
            fresh = dict(zip(todo.keys(), new, strict=True))
            cache.put_many(embedder.model_id, fresh)
            vectors.update(fresh)
        updated = 0
        for p in postings:
            sim = similarity(vectors[keys[p.id]], role_vecs, fact_vecs)
            if p.similarity != sim or p.similarity_version != version:
                p.similarity, p.similarity_version = sim, version
                session.add(p)
                updated += 1
        session.commit()
    finally:
        if own_cache:
            cache.close()
    stats = {
        "ranked": len(postings),
        "embedded": len(todo),
        "cached": cached,
        "updated": updated,
        "model": embedder.model_id,
        "profile_version": version,
    }
    log.info(
        "ranking: %d postings, %d embedded, %d cached, %d updated",
        len(postings),
        len(todo),
        cached,
        updated,
    )
    return stats


def _profile_vectors(
    embedder: Embedder, cache: EmbeddingCache, roles: list[str], facts: list[str]
) -> tuple[list[list[float]], list[list[float]]]:
    def key(text: str) -> str:
        return "q:" + hashlib.sha256(text.encode()).hexdigest()[:24]

    texts = list(dict.fromkeys(roles + facts))
    vectors = cache.get_many(embedder.model_id, [key(t) for t in texts])
    missing = [t for t in texts if key(t) not in vectors]
    if missing:
        fresh = {
            key(t): v for t, v in zip(missing, embedder.embed(missing, query=True), strict=True)
        }
        cache.put_many(embedder.model_id, fresh)
        vectors.update(fresh)
    return [vectors[key(t)] for t in roles], [vectors[key(t)] for t in facts]


def top_postings(session: Session, n: int = 10) -> list[Posting]:
    stmt = (
        select(Posting)
        .where(
            Posting.status == "open",
            col(Posting.filter_state).in_(RANKABLE_FILTER_STATES),
            col(Posting.duplicate_of).is_(None),
            col(Posting.similarity).is_not(None),
        )
        .order_by(col(Posting.similarity).desc(), col(Posting.id))
        .limit(n)
    )
    return list(session.exec(stmt).all())
