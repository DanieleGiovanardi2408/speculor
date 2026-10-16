#!/usr/bin/env python3
"""Pre-commit guard: block personal data from being committed.

Fails if any file passed by pre-commit (the staged files):

* lives at a forbidden path (private/, data/, *.db, real config files, the seed export, PDFs,
  .env files);
* contains a forbidden string listed in ``private/forbidden_strings.txt`` (gitignored, one
  string per line, ``#`` comments allowed; matched case-insensitively);
* contains a personal-looking email address (common webmail domains) or a phone-number-like
  string.

A line can opt out of the content checks with the marker ``check-private: ignore``.
Usage: ``python scripts/check_private.py FILE...`` (exit code 1 on violations).
"""

from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_LIST = REPO_ROOT / "private" / "forbidden_strings.txt"
IGNORE_MARKER = "check-private: ignore"

FORBIDDEN_PATH_GLOBS = (
    "private/*",
    "data/*",
    "*.db",
    "*.db-journal",
    "*.sqlite",
    "config/profile.yaml",
    "config/companies.yaml",
    "config/rules.yaml",
    "config/facts.yaml",
    "radar_seed.json",
    "*/radar_seed.json",
    "*.pdf",
    ".env",
    "*/.env",
    ".env.*",
    "*/.env.*",
)
ALLOWED_PATHS = (".env.example", "*/.env.example")

# Files whose content is not scanned (machine generated, full of hashes and numbers).
SKIP_CONTENT_GLOBS = ("uv.lock", "*/pnpm-lock.yaml", "pnpm-lock.yaml", "*.png", "*.jpg", "*.ico")

PERSONAL_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@(?:gmail|googlemail|hotmail|outlook|live|yahoo|icloud|me|libero|"
    r"virgilio|tiscali|alice|proton|protonmail|gmx)\.[A-Za-z.]{2,6}",
    re.IGNORECASE,
)
PHONE_RES = (
    # International, e.g. +39 333 123 4567  # check-private: ignore
    re.compile(r"(?<![\w+])\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}(?!\w)"),
    # Italian mobile without prefix, e.g. 333-1234567  # check-private: ignore
    re.compile(r"(?<![\w.-])3\d{2}[\s.-]?\d{3}[\s.-]?\d{3,4}(?![\w.-])"),
)


def _match_any(path: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def load_forbidden_strings(path: Path = FORBIDDEN_LIST) -> list[str]:
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(line.lower())
    return items


def check_path(path: str) -> str | None:
    norm = path.replace("\\", "/").removeprefix("./")
    if _match_any(norm, ALLOWED_PATHS):
        return None
    if _match_any(norm, FORBIDDEN_PATH_GLOBS):
        return f"{norm}: personal/private file must never be committed"
    return None


def check_content(path: str, text: str, forbidden: list[str]) -> list[str]:
    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if IGNORE_MARKER in line:
            continue
        lower = line.lower()
        problems.extend(
            f"{path}:{lineno}: forbidden string from private/forbidden_strings.txt"
            for item in forbidden
            if item in lower
        )
        if PERSONAL_EMAIL_RE.search(line):
            problems.append(f"{path}:{lineno}: personal email address")
        if any(rx.search(line) for rx in PHONE_RES):
            problems.append(f"{path}:{lineno}: phone-number-like string")
    return problems


def main(argv: list[str]) -> int:
    forbidden = load_forbidden_strings()
    problems: list[str] = []
    for name in argv:
        path_problem = check_path(name)
        if path_problem:
            problems.append(path_problem)
            continue
        if _match_any(name, SKIP_CONTENT_GLOBS):
            continue
        file = Path(name)
        if not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary files: covered by path rules and check-added-large-files
        problems.extend(check_content(name, text, forbidden))
    if problems:
        print("check_private: personal data detected, commit blocked:")
        for p in problems:
            print(f"  - {p}")
        print(f"(false positive? add '{IGNORE_MARKER}' on that line)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
