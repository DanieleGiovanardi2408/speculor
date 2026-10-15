"""Connector interface: every source implements ``fetch() -> list[RawPosting]``."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from radar.schemas import RawPosting


class SourceError(RuntimeError):
    """Raised when a source cannot be read (network, blocked, bad format)."""


@runtime_checkable
class Connector(Protocol):
    #: source name stored on postings, e.g. "greenhouse"
    source: str
    #: human label for logs/reports WITHOUT company names (counts-only logging rule)
    label: str

    def fetch(self) -> list[RawPosting]:
        """Return all open postings for this source/board. Raise SourceError on failure."""
        ...
