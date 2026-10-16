"""Optional source "email": job-alert emails read from a dedicated folder over IMAP.

* IMAP4 over SSL only. Auth (``imap_auth.py``): app password (``LOGIN``; Gmail) or OAuth2
  (``AUTHENTICATE XOAUTH2``; Outlook.com/Hotmail personal accounts). Secrets come from the
  environment or the macOS keychain, never from files in the repo, and are never logged.
* Strictly read-only, enforced twice: every client is wrapped in :class:`ReadOnlyClient`
  (only LOGIN/AUTHENTICATE/EXAMINE/SEARCH/FETCH-with-PEEK/LOGOUT exist on it) and the real
  connection is a :class:`ReadOnlyIMAP4_SSL` that refuses any other IMAP command on the wire
  (STORE, COPY, MOVE, EXPUNGE, APPEND, DELETE, UID ..., SELECT without read-only).
  Nothing is ever marked as read, flagged, moved or deleted.
* Only one folder/label is read (``RADAR_IMAP_FOLDER``, default ``radar``).
* Search: ``SINCE <today - since_days>``; then only the ``From`` header of each hit is fetched
  (``BODY.PEEK[HEADER.FIELDS (FROM)]``) and only messages from allowlisted alert senders
  (LinkedIn job alerts, JobTeaser) are downloaded with ``BODY.PEEK[]``. Server-side ``FROM``
  search semantics differ between Gmail and Exchange, so the sender check is done locally.
* Parsing (``email_parsers``) uses the email content only; no URL from an email is fetched.
  Non-alert emails never reach the parsers, the database or the LLM.
* Alerts are not exhaustive: the connector sets ``exhaustive = False`` so the pipeline never
  closes postings "by absence" for this source.
* Counts-only logging; errors are generic (no server text, never credentials). Auth failures
  pause the source (``SourcePaused``) until the user fixes them and resumes it.
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import imaplib
import logging
import re
import ssl
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from radar.connectors.base import SourceError
from radar.connectors.email_parsers import classify, parse_message
from radar.connectors.imap_auth import ImapAuth, auth_from_settings, default_host
from radar.connectors.status import SourcePaused
from radar.schemas import RawPosting

if TYPE_CHECKING:
    from radar.config import Settings

log = logging.getLogger(__name__)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_MESSAGES = 300
#: messages whose From header is checked per FETCH command
HEADER_BATCH = 100
#: SINCE hits considered at most (newest first) before the sender check
MAX_CANDIDATES = 2000
HEADER_PARTS = "(BODY.PEEK[HEADER.FIELDS (FROM DATE)])"
BODY_PARTS = "(BODY.PEEK[])"

# IMAP commands that can be sent on the wire. Everything else (STORE, COPY, MOVE, EXPUNGE,
# APPEND, CREATE, DELETE, RENAME, SUBSCRIBE, SELECT, UID, CLOSE, SETACL ...) is refused.
ALLOWED_COMMANDS = frozenset(
    {"CAPABILITY", "NOOP", "LOGIN", "AUTHENTICATE", "EXAMINE", "SEARCH", "FETCH", "LOGOUT"}
)
_PEEK = re.compile(r"BODY\.PEEK\[[^\]]*\](?:<[\d.]+>)?", re.I)


class ReadOnlyViolation(SourceError):
    """A command that could change the mailbox was attempted (a bug: never sent)."""


def check_fetch_parts(parts: str) -> None:
    """Only ``BODY.PEEK[...]`` (and harmless metadata) may be fetched: no ``\\Seen`` side effect."""
    rest = _PEEK.sub("", parts).upper()
    if "BODY[" in rest or re.search(r"RFC822(?!\.SIZE)", rest):
        raise ReadOnlyViolation("email: refused FETCH without PEEK")


def check_command(name: str, args: Sequence[Any] = ()) -> None:
    upper = name.upper()
    if upper not in ALLOWED_COMMANDS:
        raise ReadOnlyViolation(f"email: refused IMAP command {upper}")
    if upper == "FETCH" and len(args) >= 2:
        part = args[1]
        check_fetch_parts(part.decode() if isinstance(part, bytes) else str(part))


class ReadOnlyIMAP4_SSL(imaplib.IMAP4_SSL):
    """``imaplib.IMAP4_SSL`` that refuses, before sending, any command outside the allowlist."""

    def _command(self, name: str, *args: Any) -> Any:
        check_command(name, args)
        return super()._command(name, *args)  # type: ignore[misc]


class ImapClient(Protocol):
    """The subset of ``imaplib.IMAP4`` the connector uses (injectable for tests)."""

    def login(self, user: str, password: str) -> tuple[str, list[Any]]: ...

    def authenticate(
        self, mechanism: str, authobject: Callable[[bytes], bytes | None]
    ) -> tuple[str, Any]: ...

    def select(self, mailbox: str = ..., readonly: bool = ...) -> tuple[str, list[Any]]: ...

    def search(self, charset: str | None, *criteria: str) -> tuple[str, list[Any]]: ...

    def fetch(self, message_set: str, message_parts: str) -> tuple[str, list[Any]]: ...

    def logout(self) -> tuple[str, list[Any]]: ...


class ReadOnlyClient:
    """The only IMAP surface the connector and scripts get: read-only by construction.

    There is no store/copy/move/expunge/append/delete/uid method; ``select`` is always
    read-only (EXAMINE) and ``fetch`` accepts only ``BODY.PEEK`` parts.
    """

    _FORBIDDEN = frozenset(
        {
            "store",
            "copy",
            "move",
            "expunge",
            "append",
            "create",
            "delete",
            "rename",
            "uid",
            "subscribe",
            "unsubscribe",
            "setacl",
            "setquota",
            "setannotation",
            "close",
            "xatom",
            "deleteacl",
            "enable",
            "_command",
            "_simple_command",
        }
    )

    def __init__(self, conn: ImapClient) -> None:
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        if name.lower() in self._FORBIDDEN:
            raise ReadOnlyViolation(f"email: refused IMAP command {name.upper()}")
        raise AttributeError(name)

    def login(self, user: str, password: str) -> tuple[str, list[Any]]:
        return self._conn.login(user, password)

    def authenticate(
        self, mechanism: str, authobject: Callable[[bytes], bytes | None]
    ) -> tuple[str, Any]:
        return self._conn.authenticate(mechanism, authobject)

    def select(self, mailbox: str = "INBOX", readonly: bool = True) -> tuple[str, list[Any]]:
        if not readonly:
            raise ReadOnlyViolation("email: refused SELECT (read-write)")
        return self._conn.select(mailbox, readonly=True)

    def examine(self, mailbox: str) -> tuple[str, list[Any]]:
        return self._conn.select(mailbox, readonly=True)

    def search(self, charset: str | None, *criteria: str) -> tuple[str, list[Any]]:
        return self._conn.search(charset, *criteria)

    def fetch(self, message_set: str, message_parts: str) -> tuple[str, list[Any]]:
        check_fetch_parts(message_parts)
        return self._conn.fetch(message_set, message_parts)

    def logout(self) -> tuple[str, list[Any]]:
        return self._conn.logout()


ImapFactory = Callable[[str, int], ImapClient]


def default_factory(host: str, port: int) -> ImapClient:
    return ReadOnlyIMAP4_SSL(
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
    auth: ImapAuth,
    folder: str,
    factory: ImapFactory = default_factory,
) -> Iterator[ReadOnlyClient]:
    """Connect, authenticate and EXAMINE ``folder``. Errors -> generic SourceError/SourcePaused."""
    try:
        raw = factory(host, port)
    except (OSError, imaplib.IMAP4.error, ssl.SSLError):
        raise SourceError("email: cannot connect to the IMAP server") from None
    conn = ReadOnlyClient(raw)
    try:
        try:
            auth.authenticate(conn)
        except SourceError:
            raise
        except (OSError, imaplib.IMAP4.error):
            raise SourceError("email: IMAP login failed") from None
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


def search_since(conn: ReadOnlyClient, since: date) -> list[bytes]:
    """Message numbers matching ``SINCE since``, newest first."""
    try:
        typ, data = conn.search(None, "SINCE", imap_date(since))
    except (OSError, imaplib.IMAP4.error):
        raise SourceError("email: IMAP search failed") from None
    if typ != "OK":
        raise SourceError("email: IMAP search failed")
    ids: set[bytes] = set()
    for chunk in data:
        if isinstance(chunk, bytes | bytearray):
            ids.update(bytes(x) for x in bytes(chunk).split() if x.isdigit())
    return sorted(ids, key=int, reverse=True)


def _fetch_headers(
    conn: ReadOnlyClient, ids: Sequence[bytes]
) -> dict[bytes, email.message.Message]:
    out: dict[bytes, email.message.Message] = {}
    try:
        typ, data = conn.fetch(b",".join(ids).decode("ascii"), HEADER_PARTS)
    except (OSError, imaplib.IMAP4.error):
        raise SourceError("email: IMAP fetch failed") from None
    if typ != "OK":
        return out
    for item in data:
        if not (isinstance(item, tuple) and len(item) >= 2):
            continue
        prefix, raw = item[0], item[1]
        if not isinstance(prefix, bytes | bytearray) or not isinstance(raw, bytes | bytearray):
            continue
        num = bytes(prefix).split(b" ", 1)[0]
        if num.isdigit():
            out[num] = email.message_from_bytes(bytes(raw), policy=email.policy.default)
    return out


def alert_ids(
    conn: ReadOnlyClient,
    since: date,
    *,
    extra_senders: Iterable[str] = (),
    kinds: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[tuple[bytes, str]]:
    """(message number, alert kind) of allowlisted alert emails since ``since``, newest first.

    Only the From/Date headers of the candidates are read; ``kinds`` restricts the kinds.
    """
    extra = tuple(extra_senders)
    wanted = set(kinds) if kinds is not None else None
    out: list[tuple[bytes, str]] = []
    candidates = search_since(conn, since)[:MAX_CANDIDATES]
    for start in range(0, len(candidates), HEADER_BATCH):
        batch = candidates[start : start + HEADER_BATCH]
        headers = _fetch_headers(conn, batch)
        for msg_id in batch:
            msg = headers.get(msg_id)
            kind = classify(msg, extra) if msg is not None else None
            if kind is not None and (wanted is None or kind in wanted):
                out.append((msg_id, kind))
                if limit is not None and len(out) >= limit:
                    return out
    return out


def fetch_message(conn: ReadOnlyClient, msg_id: bytes) -> email.message.Message | None:
    """Fetch one message with BODY.PEEK[] (does not set \\Seen)."""
    try:
        typ, data = conn.fetch(msg_id.decode("ascii"), BODY_PARTS)
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
        auth: ImapAuth,
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
        self._auth = auth
        self.folder = folder
        self.since_days = max(1, since_days)
        self.extra_senders = tuple(s.strip() for s in extra_senders if s and s.strip())
        self.max_messages = max_messages
        self._factory = factory
        self._today = today

    def __repr__(self) -> str:  # never show credentials
        return (
            f"EmailAlertsConnector(auth={self.auth_mode!r}, folder={self.folder!r}, "
            f"since_days={self.since_days})"
        )

    @property
    def auth_mode(self) -> str:
        return self._auth.mode

    @property
    def user(self) -> str:
        return self._auth.user

    @classmethod
    def from_settings(
        cls, settings: Settings, *, extra_senders: Sequence[str] = (), **kwargs: Any
    ) -> EmailAlertsConnector | None:
        """A connector when the user, the host and the auth mode's credentials are set; else None.

        password mode: ``RADAR_IMAP_PASSWORD`` or a keychain password (``radar imap set-password``).
        oauth2 mode: ``RADAR_IMAP_OAUTH_CLIENT_ID``; a missing/expired token pauses the source
        at fetch time (``radar imap login``).
        """
        auth = auth_from_settings(settings)
        host = (settings.imap_host or "").strip() or default_host(settings.imap_user)
        if auth is None or not host:
            return None
        return cls(
            host,
            auth,
            port=settings.imap_port,
            folder=settings.imap_folder,
            since_days=settings.imap_since_days,
            extra_senders=extra_senders,
            **kwargs,
        )

    @contextmanager
    def mailbox(self) -> Iterator[ReadOnlyClient]:
        """The configured folder, opened read-only (also used by scripts/fetch_alert_samples.py)."""
        with open_mailbox(self.host, self.port, self._auth, self.folder, self._factory) as conn:
            yield conn

    def fetch(self) -> list[RawPosting]:
        since = self._today() - timedelta(days=self.since_days)
        by_key: dict[str, RawPosting] = {}
        n_messages = n_alerts = n_unparsed = 0
        with self.mailbox() as conn:
            ids = alert_ids(conn, since, extra_senders=self.extra_senders, limit=self.max_messages)
            for msg_id, _kind in ids:  # newest first: the newest snippet wins
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
            "email: %d alert messages, %d with postings, %d unparsable, %d postings",
            n_messages,
            n_alerts,
            n_unparsed,
            len(by_key),
        )
        return list(by_key.values())


__all__ = [
    "EmailAlertsConnector",
    "ReadOnlyClient",
    "ReadOnlyIMAP4_SSL",
    "ReadOnlyViolation",
    "SourcePaused",
    "alert_ids",
    "fetch_message",
    "imap_date",
    "open_mailbox",
    "search_since",
]
