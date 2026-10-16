#!/usr/bin/env python3
"""Promote ONE reviewed, anonymized alert sample into the test fixtures.

Usage::

    make promote-email-sample FILE=private/email_samples/real_linkedin_1.eml [NAME=real_linkedin_1]
    uv run python scripts/promote_email_sample.py private/email_samples/real_linkedin_1.eml

Run it only after reading the file yourself (scripts/fetch_alert_samples.py anonymizes on a best
effort basis). It refuses files that fail ``scripts/check_private.py`` content checks (personal
email addresses, phone numbers, strings in ``private/forbidden_strings.txt``) or that do not
look anonymized (recipient must be the placeholder), then copies the file to
``tests/fixtures/email/<name>.eml``. It never commits: review ``git diff`` and commit yourself.
"""

from __future__ import annotations

import argparse
import email
import email.policy
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_private import check_content, load_forbidden_strings  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "email"
PLACEHOLDER = "test.user@example.com"
_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def _shown(path: Path) -> Path:
    return path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path


def problems(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    found = check_content(str(path.name), text, load_forbidden_strings())
    msg = email.message_from_string(text, policy=email.policy.default)
    # quoted-printable soft line breaks can split an address: scan the decoded parts too
    for part in msg.walk():
        if part.get_content_maintype() == "text":
            decoded = str(part.get_content())
            found.extend(check_content(f"{path.name} (decoded)", decoded, load_forbidden_strings()))
    if "check-private: ignore" in text:
        found.append(f"{path.name}: contains the check-private opt-out marker")
    if PLACEHOLDER not in str(msg.get("To", "")):
        found.append(f"{path.name}: To header is not the anonymized placeholder")
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("file", type=Path)
    ap.add_argument("--name", help="fixture name without .eml (default: the file's stem)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing fixture")
    args = ap.parse_args(argv)

    src: Path = args.file
    if not src.is_file() or src.suffix != ".eml":
        print("pass one .eml file from private/email_samples/")
        return 2
    name = args.name or src.stem
    if not _NAME_RE.match(name):
        print("name: lowercase letters, digits and underscores only")
        return 2
    issues = problems(src)
    if issues:
        print("refused, fix the sample first:")
        for line in issues:
            print(f"  {line}")
        return 1
    dest = FIXTURES / f"{name}.eml"
    if dest.exists() and not args.force:
        print(f"{_shown(dest)} exists (use --force)")
        return 1
    FIXTURES.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    print(f"copied to {_shown(dest)}; review `git diff`, then commit yourself")
    return 0


if __name__ == "__main__":
    sys.exit(main())
