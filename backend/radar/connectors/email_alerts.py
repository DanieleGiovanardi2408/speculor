"""Optional source "email": job-alert emails read from a dedicated mailbox over IMAP.

* IMAP4 over SSL only, credentials only from the environment (``RADAR_IMAP_*``).
* Strictly read-only: the folder is opened with ``select(folder, readonly=True)`` and messages
  are fetched with ``BODY.PEEK[]``, so nothing is marked as read. Nothing is ever stored,
  flagged, moved or deleted on the server.
* Search: ``SINCE <today - since_days> FROM <sender>`` for each known alert sender
  (LinkedIn job alerts, JobTeaser) plus configured extra senders.
* Parsing (``email_parsers``) uses the email content only; no URL from an email is fetched.
* Alerts are not exhaustive: the connector sets ``exhaustive = False`` so the pipeline never
  closes postings "by absence" for this source.
* Counts-only logging; errors are generic (no server text, never credentials).
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import imaplib
import logging
import ssl
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from radar.connectors.base import SourceError
from radar.connectors.email_parsers import BUILTIN_SEARCH_SENDERS, parse_message
from radar.schemas import RawPosting

if TYPE_CHECKING:
    from radar.config import Settings

log = logging.getLogger(__name__)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_MESSAGES = 300


class ImapClient(Protocol):
    """The subset of ``imaplib.IMAP4`` the connector uses (injectable for tests)."""

    def login(self, user: str, password: str) -> tuple[str, list[Any]]: ...

    def select(self, mailbox: str = ..., readonly: bool = ...) -> tuple[str, list[Any]]: ...

    def search(self, charset: str | None, *criteria: str) -> tuple[str, list[Any]]: ...

    def fetch(self, message_set: str, message_parts: str) -> tuple[str, list[Any]]: ...

    def logout(self) -> tuple[str, list[Any]]: ...


ImapFactory = Callable[[str, int], ImapClient]


def default_factory(host: str, port: int) -> ImapClient:
    return imaplib.IMAP4_SSL(
        host, port, ssl_context=ssl.create_default_context(), timeout=DEFAULT_TIMEOUT
    )


def imap_date(day: date) -> str:
    """IMAP date (RFC 3501 ``date-text``), locale independent: ``02-Oct-2026``."""
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _mailbox(folder: str) -> str:
    folder = folder.strip() or "radar"
    if folder.startswith('"') or not any(c in folder for c in ' "\\(){%*'):
        return folder
    return _quote(folder)


def _message_bytes(data: Sequence[Any]) -> bytes | None:
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes | bytearray):
            return bytes(item[1])
    return None


@contextmanager
def open_mailbox(
    host: str,
    port: int,
    user: str,
    password: str,
    folder: str,
    factory: ImapFactory = default_factory,
) -> Iterator[ImapClient]:
    """Connect, log in and select ``folder`` READ-ONLY. Errors -> generic SourceError."""
    try:
        conn = factory(host, port)
    except (OSError, imaplib.IMAP4.error, ssl.SSLError):
        raise SourceError("email: cannot connect to the IMAP server") from None
    try:
        try:
            typ, _ = conn.login(user, password)
        except (OSError, imaplib.IMAP4.error):
            raise SourceError("email: IMAP login failed") from None
        if typ != "OK":
            raise SourceError("email: IMAP login failed")
        try:
            typ, _ = conn.select(_mailbox(folder), readonly=True)
        except (OSError, imaplib.IMAP4.error):
            raise SourceError("email: cannot open the configured IMAP folder") from None
        if typ != "OK":
            raise SourceError("email: cannot open the configured IMAP folder")
        yield conn
    finally:
        with contextlib.suppress(Exception):  # best effort; never mask the real error
            conn.logout()


def search_ids(conn: ImapClient, senders: Iterable[str], since: date) -> list[bytes]:
    """Message numbers matching ``SINCE since FROM sender`` for any sender, newest first."""
    ids: set[bytes] = set()
    for sender in senders:
        try:
            typ, data = conn.search(None, "SINCE", imap_date(since), "FROM", _quote(sender))
        except (OSError, imaplib.IMAP4.error):
            raise SourceError("email: IMAP search failed") from None
        if typ != "OK":
            raise SourceError("email: IMAP search failed")
        for chunk in data:
            if isinstance(chunk, bytes | bytearray):
                ids.update(bytes(x) for x in bytes(chunk).split() if x.isdigit())
    return sorted(ids, key=int, reverse=True)


def fetch_message(conn: ImapClient, msg_id: bytes) -> email.message.Message | None:
    """Fetch one message with BODY.PEEK[] (does not set \\Seen)."""
    try:
        typ, data = conn.fetch(msg_id.decode("ascii"), "(BODY.PEEK[])")
    except (OSError, imaplib.IMAP4.error):
        raise SourceError("email: IMAP fetch failed") from None
    if typ != "OK":
        return None
    raw = _message_bytes(data)
    if raw is None:
        return None
    return email.message_from_bytes(raw, policy=email.policy.default)


class EmailAlertsConnector:
    source = "email"
    label = "email alerts"
    #: alerts list only some postings: never close by absence (see pipeline/closed.py)
    exhaustive = False

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        *,
        port: int = 993,
        folder: str = "radar",
        since_days: int = 14,
        extra_senders: Sequence[str] = (),
        max_messages: int = DEFAULT_MAX_MESSAGES,
        factory: ImapFactory = default_factory,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self._password = password
        self.folder = folder
        self.since_days = max(1, since_days)
        self.extra_senders = tuple(s.strip() for s in extra_senders if s and s.strip())
        self.max_messages = max_messages
        self._factory = factory
        self._today = today

    def __repr__(self) -> str:  # never show credentials
        return f"EmailAlertsConnector(folder={self.folder!r}, since_days={self.since_days})"

    @classmethod
    def from_settings(
        cls, settings: Settings, *, extra_senders: Sequence[str] = (), **kwargs: Any
    ) -> EmailAlertsConnector | None:
        """A connector when host, user and password are all set; else None."""
        password = (
            settings.imap_password.get_secret_value() if settings.imap_password is not None else ""
        )
        if not (settings.imap_host and settings.imap_user and password):
            return None
        return cls(
            settings.imap_host,
            settings.imap_user,
            password,
            port=settings.imap_port,
            folder=settings.imap_folder,
            since_days=settings.imap_since_days,
            extra_senders=extra_senders,
            **kwargs,
        )

    @property
    def search_senders(self) -> tuple[str, ...]:
        seen: list[str] = []
        for s in (*BUILTIN_SEARCH_SENDERS, *self.extra_senders):
            if s.lower() not in (x.lower() for x in seen):
                seen.append(s)
        return tuple(seen)

    def fetch(self) -> list[RawPosting]:
        since = self._today() - timedelta(days=self.since_days)
        by_key: dict[str, RawPosting] = {}
        n_messages = n_alerts = n_unparsed = 0
        with open_mailbox(
            self.host, self.port, self.user, self._password, self.folder, self._factory
        ) as conn:
            ids = search_ids(conn, self.search_senders, since)[: self.max_messages]
            for msg_id in ids:  # newest first: the newest snippet wins
                msg = fetch_message(conn, msg_id)
                if msg is None:
                    continue
                n_messages += 1
                try:
                    postings = parse_message(msg, self.extra_senders)
                except Exception:  # one odd email never fails the source
                    n_unparsed += 1
                    continue
                if postings:
                    n_alerts += 1
                for p in postings:
                    by_key.setdefault(p.url, p)
        log.info(
            "email: %d messages, %d with postings, %d unparsable, %d postings",
            n_messages,
            n_alerts,
            n_unparsed,
            len(by_key),
        )
        return list(by_key.values())
