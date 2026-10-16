"""IMAP authentication for the "email" source: app password or OAuth2 (XOAUTH2).

* ``password`` (Gmail, other providers): ``LOGIN`` with an app password taken from
  ``RADAR_IMAP_PASSWORD`` or, as an alternative to ``.env``, from the macOS keychain
  (``radar imap set-password``).
* ``oauth2`` (Outlook.com / Hotmail / Live personal accounts, where basic auth was retired on
  16 Sep 2024): the user's own Microsoft Entra app (``RADAR_IMAP_OAUTH_CLIENT_ID``, public client)
  signs in once with the device-code flow (``radar imap login``); the MSAL token cache (refresh
  token included) lives only in the keychain. Each run refreshes the access token silently and
  authenticates with ``AUTHENTICATE XOAUTH2``. When that is no longer possible the source pauses
  with :class:`SourcePaused` and the user runs ``radar imap login`` again.

Secrets are never written to files, never logged, never part of an exception message.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from radar.connectors.status import SourcePaused

if TYPE_CHECKING:
    from radar.config import Settings

log = logging.getLogger(__name__)

AuthMode = Literal["password", "oauth2"]

KEYRING_SERVICE = "speculor-radar"
IMAP_SCOPES = ("https://outlook.office.com/IMAP.AccessAsUser.All",)  # offline_access: added by MSAL
DEFAULT_TENANT = "consumers"
OUTLOOK_HOST = "outlook.office365.com"
GMAIL_HOST = "imap.gmail.com"
# Second-level labels of Microsoft consumer mail domains (outlook.it, hotmail.co.uk, live.com ...).
_MICROSOFT_LABELS = frozenset({"outlook", "hotmail", "live", "msn", "passport", "windowslive"})
_GOOGLE_DOMAINS = frozenset({"gmail.com", "googlemail.com"})

PAUSE_TOKEN = ("email", "token scaduto", "Esegui `radar imap login`")
PAUSE_PASSWORD = (
    "email",
    "login IMAP rifiutato",
    "Controlla la password per app (`radar imap set-password`), poi riattiva la fonte",
)


# ------------------------------------------------------------------ keychain
def _keyring() -> Any:
    import keyring

    return keyring


def _password_key(user: str) -> str:
    return f"imap-password:{user.strip().lower()}"


def _token_key(user: str) -> str:
    return f"imap-oauth:{user.strip().lower()}"


def keychain_get(key: str) -> str | None:
    """A secret from the OS keychain (None if missing or no keychain is available)."""
    try:
        value = _keyring().get_password(KEYRING_SERVICE, key)
    except Exception:  # no backend (CI/Linux), locked keychain ...: treated as "not set"
        log.debug("keychain: read unavailable")
        return None
    return value if isinstance(value, str) and value else None


def keychain_set(key: str, value: str) -> None:
    _keyring().set_password(KEYRING_SERVICE, key, value)


def keychain_delete(key: str) -> bool:
    try:
        _keyring().delete_password(KEYRING_SERVICE, key)
    except Exception:
        return False
    return True


def stored_password(user: str) -> str | None:
    return keychain_get(_password_key(user))


def store_password(user: str, password: str) -> None:
    keychain_set(_password_key(user), password)


def has_oauth_token(user: str) -> bool:
    return keychain_get(_token_key(user)) is not None


def forget(user: str) -> list[str]:
    """Delete every stored IMAP secret of ``user``; returns what was removed (labels only)."""
    removed = []
    if keychain_delete(_password_key(user)):
        removed.append("password")
    if keychain_delete(_token_key(user)):
        removed.append("oauth token")
    return removed


# ------------------------------------------------------------------ provider defaults
def mail_domain(user: str | None) -> str:
    return (user or "").rsplit("@", 1)[-1].strip().lower() if user and "@" in user else ""


def is_microsoft_consumer(user: str | None) -> bool:
    labels = mail_domain(user).split(".")
    return len(labels) >= 2 and labels[0] in _MICROSOFT_LABELS


def default_host(user: str | None) -> str | None:
    if is_microsoft_consumer(user):
        return OUTLOOK_HOST
    if mail_domain(user) in _GOOGLE_DOMAINS:
        return GMAIL_HOST
    return None


def effective_mode(setting: str, user: str | None) -> AuthMode:
    """``password``/``oauth2`` as configured; ``auto``: oauth2 for Microsoft consumer domains."""
    s = (setting or "auto").strip().lower()
    if s in ("password", "oauth2"):
        return s  # type: ignore[return-value]
    return "oauth2" if is_microsoft_consumer(user) else "password"


# ------------------------------------------------------------------ authenticators
class ImapLoginTarget(Protocol):
    """What an authenticator needs from the IMAP connection."""

    def login(self, user: str, password: str) -> tuple[str, list[Any]]: ...

    def authenticate(
        self, mechanism: str, authobject: Callable[[bytes], bytes | None]
    ) -> tuple[str, Any]: ...


def xoauth2_string(user: str, access_token: str) -> bytes:
    """SASL XOAUTH2 initial response (imaplib base64-encodes it)."""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01".encode()


@dataclass
class PasswordAuth:
    user: str
    password: str = field(repr=False)
    mode: AuthMode = "password"

    def authenticate(self, conn: ImapLoginTarget) -> None:
        import imaplib

        try:
            typ, _ = conn.login(self.user, self.password)
        except imaplib.IMAP4.error:
            raise SourcePaused(*PAUSE_PASSWORD) from None
        if typ != "OK":
            raise SourcePaused(*PAUSE_PASSWORD)


TokenProvider = Callable[[], str]


@dataclass
class OAuth2Auth:
    user: str
    token_provider: TokenProvider = field(repr=False)
    mode: AuthMode = "oauth2"

    def authenticate(self, conn: ImapLoginTarget) -> None:
        import imaplib

        token = self.token_provider()  # raises SourcePaused when it cannot refresh
        payload = xoauth2_string(self.user, token)
        try:
            typ, _ = conn.authenticate("XOAUTH2", lambda _challenge: payload)
        except imaplib.IMAP4.error:
            raise SourcePaused(*PAUSE_TOKEN) from None
        if typ != "OK":
            raise SourcePaused(*PAUSE_TOKEN)


ImapAuth = PasswordAuth | OAuth2Auth


# ------------------------------------------------------------------ MSAL (device code + silent)
class MsalTokens:
    """MSAL public-client wrapper whose serialized token cache lives in the keychain."""

    def __init__(self, client_id: str, user: str, tenant: str = DEFAULT_TENANT) -> None:
        self.client_id = client_id
        self.user = user
        self.tenant = tenant or DEFAULT_TENANT

    def __repr__(self) -> str:
        return f"MsalTokens(tenant={self.tenant!r})"

    def _app(self) -> tuple[Any, Any]:
        import msal

        cache = msal.SerializableTokenCache()
        blob = keychain_get(_token_key(self.user))
        if blob:
            try:
                cache.deserialize(blob)
            except (ValueError, json.JSONDecodeError):
                log.debug("keychain: unreadable token cache ignored")
        app = msal.PublicClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant}",
            token_cache=cache,
        )
        return app, cache

    def _save(self, cache: Any) -> None:
        if cache.has_state_changed:
            keychain_set(_token_key(self.user), cache.serialize())

    def access_token(self) -> str:
        """A valid access token, refreshed silently; SourcePaused when a new login is needed."""
        try:
            app, cache = self._app()
            accounts = app.get_accounts(username=self.user) or app.get_accounts()
            result = (
                app.acquire_token_silent(list(IMAP_SCOPES), account=accounts[0])
                if accounts
                else None
            )
        except SourcePaused:
            raise
        except Exception:  # network/MSAL errors: never leak details
            raise SourcePaused(*PAUSE_TOKEN) from None
        if not isinstance(result, dict) or not result.get("access_token"):
            raise SourcePaused(*PAUSE_TOKEN)
        try:
            self._save(cache)
        except Exception:
            log.debug("keychain: token cache not updated")
        return str(result["access_token"])

    def device_login(self, show: Callable[[str], None]) -> bool:
        """Interactive device-code sign-in; ``show`` prints Microsoft's instructions (URL + code).

        Returns True when a token was obtained and stored in the keychain.
        """
        app, cache = self._app()
        flow = app.initiate_device_flow(scopes=list(IMAP_SCOPES))
        if "user_code" not in flow:
            show(
                "Impossibile avviare il login (controlla RADAR_IMAP_OAUTH_CLIENT_ID e che l'app "
                "consenta i 'public client flows')."
            )
            return False
        show(str(flow.get("message") or f"{flow.get('verification_uri')} {flow['user_code']}"))
        result = app.acquire_token_by_device_flow(flow)  # blocks until done or expired
        if not isinstance(result, dict) or not result.get("access_token"):
            code = str(result.get("error", "unknown")) if isinstance(result, dict) else "unknown"
            show(f"Login non riuscito ({code}).")
            return False
        claims = result.get("id_token_claims") or {}
        signed_in = str(claims.get("preferred_username") or claims.get("email") or "")
        if signed_in and signed_in.lower() != self.user.lower():
            show("Attenzione: hai fatto login con un account diverso da RADAR_IMAP_USER.")
        self._save(cache)
        return True


def auth_from_settings(
    settings: Settings, *, token_provider: TokenProvider | None = None
) -> ImapAuth | None:
    """The configured authenticator, or None when the mode's credentials are missing."""
    user = (settings.imap_user or "").strip()
    if not user:
        return None
    mode = effective_mode(settings.imap_auth, user)
    if mode == "oauth2":
        client_id = (settings.imap_oauth_client_id or "").strip()
        if token_provider is None:
            if not client_id:
                return None
            token_provider = MsalTokens(client_id, user, settings.imap_oauth_tenant).access_token
        return OAuth2Auth(user, token_provider)
    password = settings.imap_password.get_secret_value() if settings.imap_password else ""
    password = password or stored_password(user) or ""
    if not password:
        return None
    return PasswordAuth(user, password)
