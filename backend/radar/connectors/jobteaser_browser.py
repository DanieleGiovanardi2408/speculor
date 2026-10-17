"""Playwright browser for the jobteaser connector (optional ``browser`` extra, local Mac only).

``uv sync --extra browser && uv run playwright install chromium`` installs it. One persistent
Chromium profile (``RADAR_JOBTEASER_PROFILE``) holds the user's own session: ``login()`` opens
it headed on the sign-in page and waits until the user closes the window; ``PlaywrightBrowser``
reuses it headless for the daily detail fetch, one tab, one navigation at a time, only for the
URLs the connector passes (dashboard and single offer pages). Network responses are not
intercepted. Cookies stay in the profile: they are never read, exported or logged. No stealth
plugins, no UA spoofing, no captcha handling: a block page simply pauses the source.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from radar.connectors.base import SourceError
from radar.connectors.jobteaser import DEFAULT_BASE_URL, DEFAULT_LOCALE, LOGIN_PATH, Page

NAV_TIMEOUT_MS = 45_000
IDLE_TIMEOUT_MS = 15_000


def _playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise SourceError(
            "jobteaser: Playwright is not installed "
            "(uv sync --extra browser && uv run playwright install chromium)"
        ) from exc
    return sync_playwright


def prepare_profile(profile: Path) -> Path:
    profile = profile.expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    profile.chmod(0o700)
    return profile


def login(profile: Path, base_url: str = DEFAULT_BASE_URL, locale: str = DEFAULT_LOCALE) -> None:
    """Headed Chromium on the JobTeaser sign-in page; returns when the user closes it."""
    profile = prepare_profile(profile)
    with _playwright()() as pw:
        context = pw.chromium.launch_persistent_context(str(profile), headless=False)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(f"{base_url.rstrip('/')}{LOGIN_PATH.format(locale=locale)}")
            while context.pages:
                try:
                    context.pages[0].wait_for_event("close", timeout=0)
                except Exception:  # browser window closed by the user
                    break
        finally:
            with contextlib.suppress(Exception):  # already gone
                context.close()


class PlaywrightBrowser:
    """Headless persistent-profile browser. ``visit`` returns the final URL, status and HTML
    (kept in memory only)."""

    def __init__(self, profile: Path, *, headless: bool = True) -> None:
        profile = profile.expanduser()
        if not profile.is_dir():
            raise SourceError("jobteaser: browser profile missing (run `make jobteaser-login`)")
        self._pw = _playwright()().start()
        try:
            self._context = self._pw.chromium.launch_persistent_context(
                str(profile), headless=headless
            )
        except Exception:
            self._pw.stop()
            raise
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def visit(self, url: str) -> Page:
        try:
            response = self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as exc:
            raise SourceError(f"jobteaser: navigation failed ({type(exc).__name__})") from exc
        # busy pages never go idle; what loaded so far is enough
        with contextlib.suppress(Exception):
            self._page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_MS)
        return Page(
            url=self._page.url,
            status=response.status if response is not None else None,
            html=self._page.content(),
        )

    def close(self) -> None:
        try:
            self._context.close()
        finally:
            self._pw.stop()
