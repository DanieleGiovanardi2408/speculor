"""Local, dependency-free language detection for posting text (no LLM, no network).

The text is cleaned (HTML tags, entities, URLs, e-mails), split into segments (lines, bullets,
sentences) and every segment is classified on its own by weighted stopwords plus a few
spelling cues (diacritics, typical suffixes). The result is the share of classified words per
language, so a French body with an appended English boilerplate paragraph reads as "mostly
French" instead of an undecided mix, and an English posting that quotes a French address or
tagline stays English.

A stopword shared by k languages counts 1/k for each of them ("de", "la", "en", ...). Segments
with too little signal, or a near tie between two languages, are left unclassified.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

LANGS: tuple[str, ...] = ("en", "it", "fr", "de", "es", "nl", "pt")

_STOPWORDS: dict[str, str] = {
    "en": """the and of to in with for you we our your is are will be on this that have has from
        as an or who what about at by can it its they their us all more which would should not
        been also into work team experience skills looking join strong ability within across""",
    "it": """il lo la gli le di che per con una uno un del della dello dei delle degli nel nella
        nei sono alla alle al anche ed questo questa nostro nostra nostri si non più come essere
        ha hanno sarà sarai tra fra tuo tua ti cerchiamo esperienza conoscenza lavoro capacità
        sei ogni dal dalla sul sulla oppure ma attività ambito risorsa requisiti""",
    "fr": """le les des du et est une un pour dans sur vous nous avec au aux ce cette ces qui que
        être votre vos notre nos sont ou en la de par plus pas ne il elle ils leur leurs avez
        êtes sera mais comme aussi chez très tout tous sein poste équipe expérience mission
        missions profil rejoindre""",
    "de": """der die das und ist mit für sie wir ein eine einen einem einer zu den dem des von
        auf bei auch im in ihre ihr nicht unser unsere unseren oder du dich dein deine deinen
        bist hast sind wird werden als wie an aus nach über sowie durch um es kenntnisse
        erfahrung aufgaben""",
    "es": """el los las y para con una un por que es se como su sus nuestro nuestra nuestros más
        al del lo tu tus en de la ser son está estar también sobre muy entre equipo experiencia
        conocimientos buscamos puesto trabajo empresa""",
    "nl": """het een van voor met je jij wij we zijn op te ook bij niet dat die naar ons onze
        jouw wordt aan en de in is of als om wat hebben heb hebt kun kunt kan binnen over dit
        deze bent werk ervaring functie""",
    "pt": """os as um uma para com não são você nós nosso nossa seu sua mais também pelo pela em
        de da do das dos que na no nas nos é ser ter ou como experiência equipe trabalho vaga
        conhecimento""",
}


def _weights() -> dict[str, dict[str, float]]:
    owners: dict[str, list[str]] = {}
    for lang, words in _STOPWORDS.items():
        for w in set(words.split()):
            owners.setdefault(w, []).append(lang)
    out: dict[str, dict[str, float]] = {}
    for w, langs in owners.items():
        out[w] = {lang: 1.0 / len(langs) for lang in langs}
    return out


WEIGHTS = _weights()

# Spelling cues, each match adds CUE_WEIGHT to the language.
_CUES: dict[str, list[re.Pattern[str]]] = {
    "en": [re.compile(r"\b(?:th\w+|\w{3,}ing)\b")],
    "it": [
        re.compile(r"\w[àèìòù](?!\w)"),
        re.compile(r"\w+zion[ei]\b"),
        re.compile(r"\w*(?:ggi|gli|cch)\w*"),
    ],
    "fr": [
        re.compile(r"[çœêâîôûëï]"),
        re.compile(r"\w+(?:eux|euse|ées?|és)\b"),
        re.compile(r"\b(?:qu|d|l|n|c)['’]\w"),
    ],
    "de": [re.compile(r"[äöüß]"), re.compile(r"\w+(?:ung|keit|heit|lich|isch)(?:en|e)?\b")],
    "es": [re.compile(r"[ñ¿¡]"), re.compile(r"\w+ci[óo]n(?:es)?\b")],
    "pt": [re.compile(r"[ãõ]"), re.compile(r"\w+ç(?:ão|ões)\b")],
    "nl": [re.compile(r"\w*ij\w*"), re.compile(r"\w+(?:lijk|heid|baar)\b"), re.compile(r"aa|uu")],
}
CUE_WEIGHT = 0.5
MIN_SEGMENT_WORDS = 3
MIN_SEGMENT_SCORE = 2.0
MARGIN = 1.3  # the best language must beat the second by this factor

_TAG = re.compile(r"<[^>]+>")
_URL = re.compile(r"\b(?:https?://|www\.)\S+|\S+@\S+\.\w+")
_SPLIT = re.compile(r"\n+|[•·▪●|]|(?<=[.!?;:])\s+")
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")


@dataclass(frozen=True)
class Detection:
    lang: str | None  # top language, None when nothing could be classified
    share: float  # share of classified words in `lang` (0..1)
    words: int  # classified words
    shares: dict[str, float] = field(default_factory=dict)


def clean(text: str) -> str:
    """Strip HTML tags / entities, URLs and e-mail addresses."""
    text = html.unescape(_TAG.sub("\n", text))
    return _URL.sub(" ", text)


def segments(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(clean(text)) if s and s.strip()]


def classify_segment(segment: str) -> tuple[str | None, int]:
    """(language, word count) of one segment; language None when unsure."""
    low = segment.lower()
    tokens = [t.replace("’", "'") for t in _WORD.findall(low)]
    if len(tokens) < MIN_SEGMENT_WORDS:
        return None, len(tokens)
    scores = dict.fromkeys(LANGS, 0.0)
    for tok in tokens:
        for part in tok.split("'"):
            for lang, w in WEIGHTS.get(part, {}).items():
                scores[lang] += w
    for lang, cues in _CUES.items():
        for cue in cues:
            scores[lang] += CUE_WEIGHT * len(cue.findall(low))
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < MIN_SEGMENT_SCORE or top < MARGIN * second:
        return None, len(tokens)
    return best, len(tokens)


def detect(texts: str | Iterable[str]) -> Detection:
    """Share of words per language over all classified segments of ``texts``."""
    parts = [texts] if isinstance(texts, str) else list(texts)
    counts = dict.fromkeys(LANGS, 0)
    for text in parts:
        for seg in segments(text or ""):
            lang, n = classify_segment(seg)
            if lang is not None:
                counts[lang] += n
    total = sum(counts.values())
    if not total:
        return Detection(None, 0.0, 0, {})
    shares = {k: v / total for k, v in counts.items() if v}
    lang = max(shares, key=lambda k: shares[k])
    return Detection(lang, shares[lang], total, shares)
