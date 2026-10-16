"""Shared helpers for alert-email parsers: bodies, link unwrapping, job-card extraction.

Alert emails are parsed from their own content only: no URL found in an email is ever fetched.
"""

from __future__ import annotations

import base64
import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage, Message
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from radar.connectors.http import html_to_text
from radar.pipeline.normalize import canonical_url

# Query parameters that can carry the real target of a click-tracking redirect
# (Outlook safelinks ``url``, Google ``q``, generic ``u``/``target``/``redirect`` ...).
_REDIRECT_KEYS = (
    "url",
    "u",
    "q",
    "target",
    "dest",
    "destination",
    "redirect",
    "redirect_url",
    "redirectUrl",
    "redirect_uri",
    "link",
    "to",
    "r",
)
# Tracking/identity params that normalize.canonical_url does not already drop.
_EXTRA_TRACKING = re.compile(
    r"^(lipi|midToken|midSig|trkEmail|eid|otpToken|mc_cid|mc_eid|_hsenc|_hsmi|fbclid|gclid|"
    r"dclid|msclkid|token|sig|signature|upn|email|e|uid|user_id|recipient|campaign|cid|"
    r"jobteaser_source|jt_source|origin)$",
    re.I,
)
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+")
_B64_SEG = re.compile(r"^[A-Za-z0-9_-]{16,}={0,2}$")
_WS = re.compile(r"\s+")
_SEPARATOR_LINE = re.compile(r"^[-_=*·•.\s]{5,}$")

# Short call-to-action anchor texts that are never a job title.
_CTA = re.compile(
    r"^(view job|view|apply|apply now|easy apply|see job|see more|see all jobs|see all|more|"
    r"learn more|details|open|here|click here|candidati|candidati ora|vedi|vedi offerta|"
    r"vedi l'offerta|scopri|scopri di più|scopri l'offerta|visualizza|visualizza offerta|"
    r"voir l'offre|postuler|unsubscribe|annulla iscrizione)$",
    re.I,
)
# Card lines that are badges/insights, not title/company/location.
_NOISE = re.compile(
    r"^(actively recruiting|recruiting attivamente|easy apply|candidatura semplice|promoted|"
    r"promosso|new|nuovo|nuova|be an early applicant|candidati tra i primi|top applicant|"
    r"\d+\s+(connections?|collegamenti|alumni|applicants?|candidati|school alumni)\b.*|"
    r".*\b(ago|fa)|.*\bworks? here|.*\blavora(no)? qui|"
    r"view job|apply|vedi offerta|vedi l'offerta|candidati)$",
    re.I,
)


@dataclass
class Card:
    """All anchors in an email that point at the same posting, plus their common container."""

    key: str
    url: str  # cleaned (unwrapped, tracking-free) URL of the first anchor
    anchor_texts: list[str] = field(default_factory=list)
    container: LexborNode | None = None


def clean_space(text: str) -> str:
    return _WS.sub(" ", text.replace("‌", "").replace("\xa0", " ")).strip()


def is_cta(text: str) -> bool:
    return bool(_CTA.match(clean_space(text)))


def is_noise(line: str) -> bool:
    line = clean_space(line)
    return not line or bool(_NOISE.match(line)) or is_cta(line)


def _target_in_path(path: str) -> str | None:
    """A URL embedded in a click-tracking path: percent-encoded or base64(url)-encoded segment.

    E.g. ``/CL0/https:%2F%2Fexample.com%2F...`` (Amazon SES) or ``/e/c/<base64 JSON with href>``.
    Purely offline: the tracking link itself is never requested.
    """
    for seg in path.split("/"):
        if len(seg) < 12:
            continue
        decoded = unquote(seg)
        if decoded.startswith(("http://", "https://", "http:/", "https:/")):
            return re.sub(r"^(https?):/+", r"\1://", decoded)
        if not _B64_SEG.match(seg):
            continue
        try:
            raw = base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        found = _URL_IN_TEXT.search(raw.replace("\\/", "/"))
        if found:
            return found.group(0)
    return None


def unwrap_url(href: str, is_target: Callable[[str], bool] | None = None) -> str:
    """Follow redirect wrappers whose target URL is inside the link itself (no network).

    The target may be in a query parameter (Outlook safelinks ``url``, Google ``q``, ...) or, when
    ``is_target`` is given, encoded in a path segment; a wrapper without a recoverable target is
    returned unchanged (and callers drop it, since it does not match a posting URL).
    """
    url = html.unescape(href.strip())
    for _ in range(4):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return url
        if is_target is not None and is_target(url):
            return url
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        target = next(
            (
                params[k]
                for k in _REDIRECT_KEYS
                if k in params and params[k].startswith(("http://", "https://"))
            ),
            None,
        )
        if target is None and is_target is not None:
            target = _target_in_path(parts.path)
        if target is None:
            return url
        url = target
    return url


def strip_tracking(url: str) -> str:
    """Drop tracking params (beyond those canonical_url drops) and canonicalize."""
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _EXTRA_TRACKING.match(k)])
    return canonical_url(urlunsplit((parts.scheme, parts.netloc, parts.path, query, "")))


def clean_url(href: str, is_target: Callable[[str], bool] | None = None) -> str:
    return strip_tracking(unwrap_url(href, is_target))


def message_bodies(msg: Message) -> tuple[str | None, str | None]:
    """(html, plain text) bodies of a message; either can be None."""
    html_body: str | None = None
    text_body: str | None = None
    parts = msg.walk() if msg.is_multipart() else iter([msg])
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        content = _decode_part(part)
        if content is None:
            continue
        if ctype == "text/html" and html_body is None:
            html_body = content
        elif ctype == "text/plain" and text_body is None:
            text_body = content
    return html_body, text_body


def _decode_part(part: Message) -> str | None:
    if isinstance(part, EmailMessage):
        try:
            content = part.get_content()
        except (LookupError, ValueError, AssertionError):
            content = None
        if isinstance(content, str):
            return content
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return None
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def parse_html(body: str) -> LexborHTMLParser:
    """Parse an email HTML body, dropping hidden preheaders, styles and scripts."""
    tree = LexborHTMLParser(body)
    for node in tree.css("head, style, script, noscript, template"):
        node.decompose()
    for node in tree.css("[style]"):
        style = (node.attributes.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "mso-hide:all" in style:
            node.decompose()
    return tree


def find_cards(
    tree: LexborHTMLParser,
    key_of: Callable[[str], tuple[str, str] | None],
    *,
    max_foreign_links: int = 2,
) -> list[Card]:
    """Group anchors by posting key and find each group's card container.

    ``key_of(href) -> (key, cleaned url) | None``. The container is the largest ancestor of the
    group's first anchor that contains no anchor of another posting and at most
    ``max_foreign_links`` unrelated links (headers/footers are full of those).
    """
    href_keys: dict[str, str | None] = {}
    cards: dict[str, Card] = {}
    first_anchor: dict[str, LexborNode] = {}

    def key_for(href: str) -> str | None:
        if href not in href_keys:
            got = key_of(href)
            href_keys[href] = got[0] if got else None
            if got and got[0] not in cards:
                cards[got[0]] = Card(key=got[0], url=got[1])
        return href_keys[href]

    for a in tree.css("a[href]"):
        key = key_for(a.attributes.get("href") or "")
        if key is None:
            continue
        first_anchor.setdefault(key, a)
        text = clean_space(a.text(deep=True, separator=" "))
        if text:
            cards[key].anchor_texts.append(text)

    for key, card in cards.items():
        node: LexborNode | None = first_anchor.get(key)
        container = node
        while node is not None:
            parent = node.parent
            if parent is None or parent.tag in ("body", "html", "-undef"):
                break
            foreign = 0
            ok = True
            for a in parent.css("a[href]"):
                k = key_for(a.attributes.get("href") or "")
                if k is None:
                    foreign += 1
                elif k != key:
                    ok = False
                    break
            if not ok or foreign > max_foreign_links:
                break
            container = parent
            node = parent
        card.container = container
    return list(cards.values())


def container_lines(node: LexborNode | None) -> list[str]:
    """Visible text lines of a card container (blocks preserved), whitespace-normalized."""
    if node is None:
        return []
    text = html_to_text(node.html or "")
    return [ln for ln in (clean_space(x) for x in text.splitlines()) if ln]


def pick_title(card: Card, lines: list[str]) -> str:
    for text in card.anchor_texts:
        if not is_noise(text) and len(text) > 1:
            return text
    for line in lines:
        if not is_noise(line):
            return line
    return ""


def lines_after(title: str, lines: list[str]) -> list[str]:
    """Card lines after the title line (badges/CTAs removed)."""
    try:
        idx = lines.index(title)
        rest = lines[idx + 1 :]
    except ValueError:
        rest = lines
    return [ln for ln in rest if not is_noise(ln) and ln != title]


def split_dot(line: str) -> list[str]:
    """'Acme · Milan, Italy (Hybrid)' -> ['Acme', 'Milan, Italy (Hybrid)']."""
    return [p.strip() for p in re.split(r"\s+[·•|]\s+|\s*·\s*", line) if p.strip()]


@dataclass
class TextBlock:
    key: str
    url: str
    lines: list[str]


def text_blocks(text: str, key_of: Callable[[str], tuple[str, str] | None]) -> list[TextBlock]:
    """Plain-text fallback: each posting URL closes a block made of the lines before it.

    Only the last paragraph (after the last blank line) before the URL is kept, so headers
    ("Your job alert for ...") don't leak into the first posting.
    """
    blocks: list[TextBlock] = []
    buffer: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if _SEPARATOR_LINE.match(line):
            buffer = []
            continue
        urls = _URL_IN_TEXT.findall(line)
        matched = None
        for u in urls:
            got = key_of(u)
            if got:
                matched = got
                break
        if matched is None:
            if not line:
                buffer.append("")
            elif not urls:
                buffer.append(clean_space(line))
            continue
        before = _URL_IN_TEXT.sub("", line).strip(" :-\t")
        if before and not is_noise(before):
            buffer.append(clean_space(before))
        para: list[str] = []
        for ln in reversed(buffer):
            if ln == "":
                if para:
                    break
                continue
            para.insert(0, ln)
        blocks.append(
            TextBlock(key=matched[0], url=matched[1], lines=[ln for ln in para if not is_noise(ln)])
        )
        buffer = []
    return blocks
