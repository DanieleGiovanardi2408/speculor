#!/usr/bin/env python3
"""LOCAL ONLY: download a few real alert emails, anonymize them, write test fixtures.

Usage (needs RADAR_IMAP_HOST/USER/PASSWORD in .env)::

    uv run python scripts/fetch_alert_samples.py [--per-kind 3] [--redact "Some Name" ...]

It connects READ-ONLY (``select(readonly=True)`` + ``BODY.PEEK[]``: nothing is marked read,
moved or deleted), takes the latest ``--per-kind`` LinkedIn and JobTeaser alerts, anonymizes them
and writes ``tests/fixtures/email/real_<kind>_<n>.eml``. Only counts are printed.

Anonymization (best effort: REVIEW EVERY FILE BEFORE COMMITTING, then run
``scripts/check_private.py`` on it):

* only the text/plain and text/html parts are kept (no images/attachments); headers are rebuilt
  (From kept since it is the alert service, To -> ``Test User <test.user@example.com>``);
* the mailbox address, the recipient's display name (and each word of it), ``--redact`` strings
  and ``private/forbidden_strings.txt`` entries -> placeholders;
* any other email address -> ``redacted@example.com``; phone-like numbers -> zeros;
* URLs: unsubscribe/settings/preferences links -> ``https://example.com/unsubscribe``; LinkedIn
  profile links -> ``/in/test-user``; every query value -> ``REDACTED`` (redirect targets are
  anonymized recursively); long token-like path segments -> ``REDACTED``.
"""

from __future__ import annotations

import argparse
import email
import email.policy
import email.utils
import re
import sys
from email.message import EmailMessage, Message
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_private import PHONE_RES, load_forbidden_strings  # noqa: E402

from radar.config import get_settings  # noqa: E402
from radar.connectors.base import SourceError  # noqa: E402
from radar.connectors.email_alerts import (  # noqa: E402
    fetch_message,
    open_mailbox,
    search_ids,
)
from radar.connectors.email_parsers import classify, linkedin  # noqa: E402
from radar.connectors.email_parsers.common import message_bodies  # noqa: E402

OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "email"
FAKE_ADDR = "test.user@example.com"
FAKE_NAME = "Test User"
KEEP_DOMAINS = ("linkedin.com", "jobteaser.com", "example.com")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s<>\"'()\]]+")
_UNSUB_RE = re.compile(r"unsubscribe|psettings|notification_settings|preferences|opt-?out", re.I)
_PROFILE_RE = re.compile(r"(linkedin\.com/(?:comm/)?in/)[^/?#\s\"'<>]+", re.I)
_TOKEN_SEG = re.compile(r"^[A-Za-z0-9_~=%-]{24,}$")
_KEEP_SEG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")  # readable slugs like job-offers/<id>-<slug>


def anonymize_url(url: str, depth: int = 0) -> str:
    trailing = ""
    while url and url[-1] in ".,;:!?":
        trailing = url[-1] + trailing
        url = url[:-1]
    if _UNSUB_RE.search(url):
        return "https://example.com/unsubscribe" + trailing
    url = _PROFILE_RE.sub(r"\1test-user", url)
    parts = urlsplit(url.replace("&amp;", "&"))
    segs = [
        "REDACTED" if _TOKEN_SEG.match(s) and not _KEEP_SEG.match(s) else s
        for s in parts.path.split("/")
    ]
    query: list[tuple[str, str]] = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if v.startswith(("http://", "https://")) and depth < 3:
            query.append((k, anonymize_url(v, depth + 1)))
        else:
            query.append((k, "REDACTED" if v else ""))
    return (
        urlunsplit(
            (parts.scheme, parts.netloc, "/".join(segs), urlencode(query, quote_via=quote), "")
        )
        + trailing
    )


def build_scrubber(msg: Message, user: str, extra: list[str]) -> list[str]:
    """Strings to replace with FAKE_NAME / FAKE_ADDR (longest first)."""
    names: list[str] = []
    for header in ("To", "Delivered-To", "Cc"):
        for name, _addr in email.utils.getaddresses([str(v) for v in msg.get_all(header, [])]):
            if name:
                names.append(name)
                names.extend(w for w in re.split(r"\s+", name) if len(w) > 2)
    names.extend(s for s in extra if s.strip())
    names.extend(load_forbidden_strings())
    return sorted({n.strip() for n in names if n.strip()}, key=len, reverse=True)


def scrub(text: str, user: str, recipients: list[str], names: list[str]) -> str:
    text = _URL_RE.sub(lambda m: anonymize_url(m.group(0)), text)
    for addr in {user.lower(), *recipients}:
        if addr:
            text = re.sub(re.escape(addr), FAKE_ADDR, text, flags=re.I)

    def _addr(m: re.Match[str]) -> str:
        found = m.group(0)
        domain = found.rsplit("@", 1)[-1].lower()
        if any(domain == d or domain.endswith("." + d) for d in KEEP_DOMAINS):
            return found
        return "redacted@example.com"

    text = _EMAIL_RE.sub(_addr, text)
    for name in names:
        text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", FAKE_NAME, text, flags=re.I)
    for rx in PHONE_RES:
        text = rx.sub(lambda m: re.sub(r"\d", "0", m.group(0)), text)
    return text


def anonymize(msg: Message, user: str, extra: list[str]) -> EmailMessage:
    recipients = [
        a.lower()
        for _n, a in email.utils.getaddresses(
            [str(v) for h in ("To", "Delivered-To", "Cc") for v in msg.get_all(h, [])]
        )
        if a
    ]
    names = build_scrubber(msg, user, extra)
    html_body, text_body = message_bodies(msg)
    out = EmailMessage(policy=email.policy.SMTP)
    out["From"] = str(msg.get("From", ""))
    out["To"] = f"{FAKE_NAME} <{FAKE_ADDR}>"
    out["Subject"] = scrub(str(msg.get("Subject", "")), user, recipients, names)
    out["Date"] = str(msg.get("Date", ""))
    out["Message-ID"] = email.utils.make_msgid(domain="example.com")
    out.set_content(scrub(text_body or "", user, recipients, names), cte="quoted-printable")
    if html_body:
        out.add_alternative(
            scrub(html_body, user, recipients, names), subtype="html", cte="quoted-printable"
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--per-kind", type=int, default=3)
    ap.add_argument("--redact", action="append", default=[], help="extra string to anonymize")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    s = get_settings()
    password = s.imap_password.get_secret_value() if s.imap_password is not None else ""
    if not (s.imap_host and s.imap_user and password):
        print("RADAR_IMAP_HOST, RADAR_IMAP_USER and RADAR_IMAP_PASSWORD must be set.")
        return 2
    from datetime import date, timedelta

    since = date.today() - timedelta(days=max(s.imap_since_days, 60))
    wanted = {"linkedin": [*linkedin.SENDERS], "jobteaser": ["jobteaser.com"]}
    written = {"linkedin": 0, "jobteaser": 0}
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        with open_mailbox(s.imap_host, s.imap_port, s.imap_user, password, s.imap_folder) as conn:
            for kind, senders in wanted.items():
                for msg_id in search_ids(conn, senders, since):
                    if written[kind] >= args.per_kind:
                        break
                    msg = fetch_message(conn, msg_id)
                    if msg is None or classify(msg) != kind:
                        continue
                    written[kind] += 1
                    clean = anonymize(msg, s.imap_user, args.redact)
                    path = args.out / f"real_{kind}_{written[kind]}.eml"
                    path.write_bytes(clean.as_bytes())
    except SourceError as exc:
        print(f"error: {exc}")
        return 1
    print(
        f"wrote {written['linkedin']} LinkedIn and {written['jobteaser']} JobTeaser samples "
        f"to {args.out.relative_to(REPO_ROOT) if args.out.is_relative_to(REPO_ROOT) else args.out}"
    )
    print("Review every real_*.eml by hand, then: python scripts/check_private.py <files>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
