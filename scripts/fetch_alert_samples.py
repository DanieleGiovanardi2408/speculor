#!/usr/bin/env python3
"""LOCAL ONLY: download a few real alert emails, anonymize them into a gitignored review folder.

Usage (the same RADAR_IMAP_* setup as the "email" source: Gmail app password or Outlook OAuth2,
see docs/RUNNING.md)::

    make email-samples                     # = uv run python scripts/fetch_alert_samples.py
    uv run python scripts/fetch_alert_samples.py [--per-kind 3] [--redact "Some Name" ...]

It connects READ-ONLY (EXAMINE + ``BODY.PEEK``, through the same read-only client as the
connector: nothing is marked read, moved or deleted), takes the latest ``--per-kind`` LinkedIn and
JobTeaser alerts from the configured folder, anonymizes them and writes
``private/email_samples/real_<kind>_<n>.eml`` (gitignored). Only counts are printed.

Nothing lands in ``tests/`` automatically. Review each file by hand, then promote it:
``make promote-email-sample FILE=private/email_samples/real_linkedin_1.eml`` (runs
``scripts/check_private.py`` and copies it to ``tests/fixtures/email/``).

Anonymization (best effort, hence the manual review):

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
    EmailAlertsConnector,
    alert_ids,
    fetch_message,
)
from radar.connectors.email_parsers import classify  # noqa: E402
from radar.connectors.email_parsers.common import message_bodies  # noqa: E402
from radar.connectors.status import SourcePaused  # noqa: E402

OUT_DIR = REPO_ROOT / "private" / "email_samples"  # gitignored: review before promoting
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
    connector = EmailAlertsConnector.from_settings(s)
    if connector is None:
        print("IMAP not configured: run `uv run radar imap status` (docs/RUNNING.md).")
        return 2
    from datetime import date, timedelta

    since = date.today() - timedelta(days=max(s.imap_since_days, 60))
    written = {"linkedin": 0, "jobteaser": 0}
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        with connector.mailbox() as conn:
            for kind in written:
                for msg_id, _ in alert_ids(conn, since, kinds=[kind], limit=args.per_kind * 3):
                    if written[kind] >= args.per_kind:
                        break
                    msg = fetch_message(conn, msg_id)
                    if msg is None or classify(msg) != kind:
                        continue
                    written[kind] += 1
                    clean = anonymize(msg, connector.user, args.redact)
                    path = args.out / f"real_{kind}_{written[kind]}.eml"
                    path.write_bytes(clean.as_bytes())
    except SourcePaused as exc:
        print(f"paused: {exc.reason}. {exc.action}")
        return 1
    except SourceError as exc:
        print(f"error: {exc}")
        return 1
    shown = args.out.relative_to(REPO_ROOT) if args.out.is_relative_to(REPO_ROOT) else args.out
    print(
        f"wrote {written['linkedin']} LinkedIn, {written['jobteaser']} JobTeaser samples -> {shown}"
    )
    print("Review every file by hand, then: make promote-email-sample FILE=<file>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
