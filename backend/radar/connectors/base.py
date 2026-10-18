"""Connector interface: every source implements ``fetch() -> list[RawPosting]``."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from radar.schemas import RawPosting


class SourceError(RuntimeError):
    """Raised when a source cannot be read (network, blocked, bad format).

    ``problem`` (plain Italian: what is wrong) and ``action`` (what the user should do, with
    the exact command) are optional; when set they are stored with the run and shown on the
    Fonti screen. Neither may contain personal data, company names or titles.
    """

    def __init__(
        self, message: str = "", *, problem: str | None = None, action: str | None = None
    ) -> None:
        super().__init__(message)
        self.problem = problem
        self.action = action


@runtime_checkable
class Connector(Protocol):
    #: source name stored on postings, e.g. "greenhouse"
    source: str
    #: human label for logs/reports WITHOUT company names (counts-only logging rule)
    label: str

    def fetch(self) -> list[RawPosting]:
        """Return all open postings for this source/board. Raise SourceError on failure."""
        ...
