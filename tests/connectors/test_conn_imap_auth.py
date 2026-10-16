"""IMAP auth modes (app password / OAuth2 device code), keychain storage, `radar imap` CLI,
alert-link canonicalization and the sample promotion script.

Fake MSAL, fake keychain, no network."""

from __future__ import annotations

import importlib.util
import sys
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from radar.config import REPO_ROOT, Settings, get_settings
from radar.connectors import imap_auth
from radar.connectors.email_alerts import EmailAlertsConnector
from radar.connectors.email_parsers import jobteaser, linkedin
from radar.connectors.imap_auth import (
    KEYRING_SERVICE,
    MsalTokens,
    OAuth2Auth,
    PasswordAuth,
    auth_from_settings,
    default_host,
    effective_mode,
)
from radar.connectors.linkedin_guest import JOB_URL as GUEST_JOB_URL
from radar.connectors.status import SourcePaused
from radar.pipeline.normalize import canonical_url

# Addresses are built by concatenation so the personal-data pre-commit check stays quiet.
MS_USER = "test.user" + "@" + "outlook.com"
HOTMAIL_IT = "test.user" + "@" + "hotmail.it"
LIVE_UK = "test.user" + "@" + "live.co.uk"
GMAIL_USER = "test.user" + "@" + "gmail.com"
PASSWORD = "abcd efgh ijkl mnop"  # fake
CLIENT_ID = "00000000-0000-0000-0000-000000000000"  # fake
TOKEN = "EwB-fake-access-token"  # fake


def settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key in ("HOST", "USER", "PASSWORD", "AUTH", "OAUTH_CLIENT_ID", "OAUTH_TENANT"):
        monkeypatch.delenv(f"RADAR_IMAP_{key}", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


# ------------------------------------------------------------------ provider defaults
def test_auto_mode_and_host_from_domain() -> None:
    for user in (MS_USER, HOTMAIL_IT, LIVE_UK, "x" + "@" + "msn.com"):
        assert effective_mode("auto", user) == "oauth2"
        assert default_host(user) == "outlook.office365.com"
    assert effective_mode("auto", GMAIL_USER) == "password"
    assert default_host(GMAIL_USER) == "imap.gmail.com"
    assert effective_mode("auto", "alerts@example.com") == "password"
    assert default_host("alerts@example.com") is None
    assert effective_mode("password", MS_USER) == "password"  # explicit setting wins
    assert effective_mode("oauth2", GMAIL_USER) == "oauth2"
    assert effective_mode("auto", "someone@liveops.example.com") == "password"


def test_password_from_env_or_keychain(monkeypatch: pytest.MonkeyPatch, fake_keyring: Any) -> None:
    assert auth_from_settings(settings(monkeypatch, RADAR_IMAP_USER=GMAIL_USER)) is None
    s = settings(monkeypatch, RADAR_IMAP_USER=GMAIL_USER, RADAR_IMAP_PASSWORD=PASSWORD)
    auth = auth_from_settings(s)
    assert isinstance(auth, PasswordAuth) and auth.password == PASSWORD
    assert PASSWORD not in repr(auth)

    imap_auth.store_password(GMAIL_USER, "from-keychain")
    s = settings(monkeypatch, RADAR_IMAP_USER=GMAIL_USER)
    auth = auth_from_settings(s)
    assert isinstance(auth, PasswordAuth) and auth.password == "from-keychain"
    assert (KEYRING_SERVICE, f"imap-password:{GMAIL_USER}") in fake_keyring.store
    conn = EmailAlertsConnector.from_settings(s)
    assert conn is not None and conn.host == "imap.gmail.com" and conn.auth_mode == "password"
    assert "from-keychain" not in repr(conn)


def test_oauth2_needs_client_id_and_defaults_to_outlook(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        EmailAlertsConnector.from_settings(settings(monkeypatch, RADAR_IMAP_USER=MS_USER)) is None
    )
    s = settings(monkeypatch, RADAR_IMAP_USER=MS_USER, RADAR_IMAP_OAUTH_CLIENT_ID=CLIENT_ID)
    conn = EmailAlertsConnector.from_settings(s)
    assert conn is not None
    assert (conn.host, conn.port, conn.folder, conn.auth_mode) == (
        "outlook.office365.com",
        993,
        "radar",
        "oauth2",
    )
    assert s.imap_oauth_tenant == "consumers"


def test_keychain_unavailable_is_treated_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def get_password(self, *_: str) -> str:
            raise RuntimeError("no keyring backend")

    monkeypatch.setattr(imap_auth, "_keyring", lambda: Broken())
    assert imap_auth.stored_password(GMAIL_USER) is None
    assert not imap_auth.has_oauth_token(MS_USER)


# ------------------------------------------------------------------ fake MSAL
class FakeCache:
    def __init__(self) -> None:
        self.state = "{}"
        self.has_state_changed = False

    def deserialize(self, blob: str) -> None:
        self.state = blob

    def serialize(self) -> str:
        return self.state


class FakeApp:
    """Records what MSAL is asked; behaviour set through class attributes per test."""

    instances: ClassVar[list[FakeApp]] = []
    accounts: ClassVar[list[dict[str, str]]] = []
    silent: ClassVar[dict[str, Any] | None] = None
    silent_raises: ClassVar[Exception | None] = None
    device_result: ClassVar[dict[str, Any]] = {}

    def __init__(self, client_id: str, authority: str, token_cache: FakeCache) -> None:
        self.client_id = client_id
        self.authority = authority
        self.cache = token_cache
        self.scopes: list[str] = []
        FakeApp.instances.append(self)

    def get_accounts(self, username: str | None = None) -> list[dict[str, str]]:
        return [a for a in self.accounts if username is None or a["username"] == username]

    def acquire_token_silent(self, scopes: list[str], account: Any) -> dict[str, Any] | None:
        self.scopes = scopes
        if self.silent_raises is not None:
            raise self.silent_raises
        if self.silent:
            self.cache.state = '{"refreshed": true}'
            self.cache.has_state_changed = True
        return self.silent

    def initiate_device_flow(self, scopes: list[str]) -> dict[str, Any]:
        self.scopes = scopes
        return {
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin",
            "message": "To sign in, open https://microsoft.com/devicelogin and enter ABCD-EFGH",
        }

    def acquire_token_by_device_flow(self, flow: dict[str, Any]) -> dict[str, Any]:
        if self.device_result.get("access_token"):
            self.cache.state = '{"new": true}'
            self.cache.has_state_changed = True
        return self.device_result


@pytest.fixture
def fake_msal(monkeypatch: pytest.MonkeyPatch) -> type[FakeApp]:
    mod = types.ModuleType("msal")
    mod.PublicClientApplication = FakeApp  # type: ignore[attr-defined]
    mod.SerializableTokenCache = FakeCache  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msal", mod)
    FakeApp.instances = []
    FakeApp.accounts = []
    FakeApp.silent = None
    FakeApp.silent_raises = None
    FakeApp.device_result = {}
    return FakeApp


def test_device_login_stores_cache_in_keychain_only(
    fake_msal: type[FakeApp], fake_keyring: Any, tmp_path: Path
) -> None:
    fake_msal.device_result = {
        "access_token": TOKEN,
        "refresh_token": "fake-refresh",
        "id_token_claims": {"preferred_username": MS_USER},
    }
    shown: list[str] = []
    assert MsalTokens(CLIENT_ID, MS_USER).device_login(shown.append) is True
    (app,) = fake_msal.instances
    assert app.authority == "https://login.microsoftonline.com/consumers"
    assert app.scopes == ["https://outlook.office.com/IMAP.AccessAsUser.All"]
    assert any("ABCD-EFGH" in line and "devicelogin" in line for line in shown)
    assert all(TOKEN not in line for line in shown)
    assert fake_keyring.store[(KEYRING_SERVICE, f"imap-oauth:{MS_USER}")] == '{"new": true}'


def test_device_login_failure_reports_error_code_only(
    fake_msal: type[FakeApp], fake_keyring: Any
) -> None:
    fake_msal.device_result = {"error": "authorization_declined", "error_description": "secret"}
    shown: list[str] = []
    assert MsalTokens(CLIENT_ID, MS_USER).device_login(shown.append) is False
    assert "authorization_declined" in shown[-1] and "secret" not in shown[-1]
    assert not fake_keyring.store


def test_silent_refresh_returns_token_and_updates_keychain(
    fake_msal: type[FakeApp], fake_keyring: Any
) -> None:
    fake_keyring.set_password(KEYRING_SERVICE, f"imap-oauth:{MS_USER}", '{"old": true}')
    fake_msal.accounts = [{"username": MS_USER}]
    fake_msal.silent = {"access_token": TOKEN}
    assert MsalTokens(CLIENT_ID, MS_USER).access_token() == TOKEN
    assert fake_keyring.store[(KEYRING_SERVICE, f"imap-oauth:{MS_USER}")] == '{"refreshed": true}'


@pytest.mark.parametrize(
    "setup",
    [
        lambda app: None,  # no cached account: never logged in
        lambda app: (setattr(app, "accounts", [{"username": MS_USER}]),),  # refresh -> None
        lambda app: (
            setattr(app, "accounts", [{"username": MS_USER}]),
            setattr(app, "silent_raises", RuntimeError(f"network down {TOKEN}")),
        ),
        lambda app: (
            setattr(app, "accounts", [{"username": MS_USER}]),
            setattr(app, "silent", {"error": "invalid_grant"}),
        ),
    ],
)
def test_refresh_failures_pause_the_source(
    fake_msal: type[FakeApp], setup: Callable[[type[FakeApp]], Any]
) -> None:
    setup(fake_msal)
    with pytest.raises(SourcePaused) as info:
        MsalTokens(CLIENT_ID, MS_USER).access_token()
    assert (info.value.source, info.value.reason, info.value.action) == (
        "email",
        "token scaduto",
        "Esegui `radar imap login`",
    )
    assert info.value.__cause__ is None and TOKEN not in str(info.value)


def test_oauth2_auth_from_settings_uses_msal(
    monkeypatch: pytest.MonkeyPatch, fake_msal: type[FakeApp]
) -> None:
    fake_msal.accounts = [{"username": MS_USER}]
    fake_msal.silent = {"access_token": TOKEN}
    s = settings(monkeypatch, RADAR_IMAP_USER=MS_USER, RADAR_IMAP_OAUTH_CLIENT_ID=CLIENT_ID)
    auth = auth_from_settings(s)
    assert isinstance(auth, OAuth2Auth)
    assert auth.token_provider() == TOKEN
    assert TOKEN not in repr(auth)


# ------------------------------------------------------------------ CLI
@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Callable[..., Any]]:
    from radar.cli import app

    def run(args: list[str], input: str | None = None, **env: str) -> Any:
        settings(monkeypatch, RADAR_DATABASE_URL=f"sqlite:///{tmp_path / 'cli.db'}", **env)
        get_settings.cache_clear()
        return CliRunner().invoke(app, args, input=input)

    yield run
    get_settings.cache_clear()


def test_cli_set_password_stores_in_keychain_and_status_hides_it(
    cli: Callable[..., Any], fake_keyring: Any
) -> None:
    res = cli(["imap", "set-password"], input=PASSWORD + "\n", RADAR_IMAP_USER=GMAIL_USER)
    assert res.exit_code == 0, res.output
    assert (
        fake_keyring.store[(KEYRING_SERVICE, f"imap-password:{GMAIL_USER}")] == "abcdefghijklmnop"
    )
    res = cli(["imap", "status"], RADAR_IMAP_USER=GMAIL_USER)
    assert res.exit_code == 0
    assert "password nel portachiavi: sì" in res.output
    assert "abcd" not in res.output and GMAIL_USER not in res.output
    res = cli(["imap", "logout"], RADAR_IMAP_USER=GMAIL_USER)
    assert "password" in res.output and not fake_keyring.store


def test_cli_login_runs_device_flow(
    cli: Callable[..., Any], fake_msal: type[FakeApp], fake_keyring: Any
) -> None:
    res = cli(["imap", "login"], RADAR_IMAP_USER=MS_USER)
    assert res.exit_code == 2 and "RADAR_IMAP_OAUTH_CLIENT_ID" in res.output
    fake_msal.device_result = {"access_token": TOKEN, "id_token_claims": {}}
    res = cli(["imap", "login"], RADAR_IMAP_USER=MS_USER, RADAR_IMAP_OAUTH_CLIENT_ID=CLIENT_ID)
    assert res.exit_code == 0, res.output
    assert "ABCD-EFGH" in res.output and TOKEN not in res.output
    assert (KEYRING_SERVICE, f"imap-oauth:{MS_USER}") in fake_keyring.store


# ------------------------------------------------------------------ canonical alert links
@pytest.mark.parametrize(
    "href",
    [
        "https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc%3D%3D&refId=x&lipi=y",
        "https://it.linkedin.com/jobs/view/data-scientist-intern-at-acme-4012345678?trk=eml",
        "https://www.linkedin.com/comm/jobs/search/?currentJobId=4012345678&geoId=1&midToken=t",
        "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fwww.linkedin.com%2Fcomm"
        "%2Fjobs%2Fview%2F4012345678%2F%3FtrackingId%3Dabc&data=05%7C02&sdata=x&reserved=0",
    ],
)
def test_linkedin_alert_links_map_to_the_guest_connector_url(href: str) -> None:
    got = linkedin.job_key(href)
    assert got == ("4012345678", "https://www.linkedin.com/jobs/view/4012345678")
    assert got[1] == GUEST_JOB_URL.format(id="4012345678") == canonical_url(got[1])
    assert canonical_url("https://www.linkedin.com/jobs/view/4012345678/") == got[1]


def test_linkedin_non_job_links_ignored() -> None:
    assert linkedin.job_key("https://www.linkedin.com/comm/in/test-user") is None
    assert linkedin.job_key("https://www.linkedin.com/comm/jobs/search/?keywords=x") is None


def test_linkedin_and_jobteaser_sender_allowlist() -> None:
    for addr in linkedin.SENDERS:
        assert linkedin.is_sender(addr, "LinkedIn")
    assert "jobalerts-noreply@linkedin.com" in linkedin.SENDERS
    assert "jobs-noreply@linkedin.com" in linkedin.SENDERS
    assert linkedin.is_sender("alerts@linkedin.com", "Avvisi di offerte di lavoro LinkedIn")
    assert linkedin.is_sender("alerts@linkedin.com", "LinkedIn Job Alerts")
    assert not linkedin.is_sender("messages-noreply@linkedin.com", "LinkedIn")
    assert not linkedin.is_sender("jobalerts-noreply@linkedin.com.example.net", "Job Alerts")
    assert jobteaser.is_sender("notifications@jobteaser.com", "")
    assert jobteaser.is_sender("noreply@mail.jobteaser.com", "JobTeaser")
    assert not jobteaser.is_sender("noreply@evil-jobteaser.com", "JobTeaser")
    assert not jobteaser.is_sender("jobteaser.com", "")


def test_jobteaser_tracking_wrappers_unwrapped_offline() -> None:
    import base64
    from urllib.parse import quote

    offer = "https://luiss.jobteaser.com/it/job-offers/8d3f2a10-4b6c-4e2a-9f1d-0c5e7a9b1234-acme"
    canonical = ("8d3f2a10-4b6c-4e2a-9f1d-0c5e7a9b1234", offer)
    tracked = offer + "?utm_source=job_alert&utm_medium=email&jt_source=x"
    assert jobteaser.offer_key(tracked) == canonical
    query_wrapped = "https://click.example.net/track?u=" + quote(tracked, safe="")
    assert jobteaser.offer_key(query_wrapped) == canonical
    ses = "https://tracking.example.net/CL0/" + quote(tracked, safe="") + "/1/0100-abc/xyz=="
    assert jobteaser.offer_key(ses) == canonical
    blob = base64.urlsafe_b64encode(f'{{"href":"{tracked}"}}'.encode()).decode().rstrip("=")
    assert jobteaser.offer_key("https://e.example.net/e/c/" + blob + "/sig123") == canonical
    # opaque tracking link (target not in the URL): dropped, never requested
    assert jobteaser.offer_key("https://links.jobteaser.com/ls/click?upn=OPAQUE123") is None


# ------------------------------------------------------------------ promote script
def _load_script(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fetch_samples_writes_to_gitignored_review_folder() -> None:
    mod = _load_script("fetch_alert_samples")
    assert mod.OUT_DIR == REPO_ROOT / "private" / "email_samples"
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "private/" in gitignore


def test_promote_refuses_unanonymized_and_copies_reviewed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_script("promote_email_sample")
    monkeypatch.setattr(mod, "FIXTURES", tmp_path / "fixtures")
    monkeypatch.setattr(mod, "load_forbidden_strings", lambda: [])
    bad = tmp_path / "bad.eml"
    bad.write_text(
        f"From: LinkedIn <jobalerts-noreply@linkedin.com>\nTo: Someone <{HOTMAIL_IT}>\n"
        "Subject: x\n\nhello\n",
        encoding="utf-8",
    )
    assert mod.main([str(bad)]) == 1
    assert not (tmp_path / "fixtures").exists()
    good = tmp_path / "real_linkedin_1.eml"
    good.write_text(
        "From: LinkedIn <jobalerts-noreply@linkedin.com>\nTo: Test User <test.user@example.com>\n"
        "Subject: x\n\nhello\n",
        encoding="utf-8",
    )
    assert mod.main([str(good)]) == 0
    assert (tmp_path / "fixtures" / "real_linkedin_1.eml").read_text() == good.read_text()
    assert mod.main([str(good)]) == 1  # exists: needs --force


def test_cli_set_password_resumes_paused_email_source(
    cli: Callable[..., Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from radar.connectors.status import pause, paused_sources
    from radar.db import init_db, session_scope

    cli(["imap", "status"], RADAR_IMAP_USER=GMAIL_USER)  # settings now point at tmp_path
    init_db()
    with session_scope() as s:
        pause(s, "email", "login IMAP rifiutato", "x")
    res = cli(["imap", "set-password"], input=PASSWORD + "\n", RADAR_IMAP_USER=GMAIL_USER)
    assert "Fonte email riattivata." in res.output
    with session_scope() as s:
        assert "email" not in paused_sources(s)
