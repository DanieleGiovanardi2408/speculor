from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from typing import Any

import pytest
from rich.console import Console
from sqlalchemy import Engine
from sqlmodel import Session, select

from radar.models import Contact, Draft, Posting
from radar.send import send_approved


class FakeRunner:
    """Records commands; answers like linkedin-cli v0.2 with --json."""

    def __init__(
        self,
        *,
        authenticated: bool = True,
        fail_on: dict[str, tuple[int, str]] | None = None,
        member_id: str = "urn:li:member:42",
    ) -> None:
        self.calls: list[list[str]] = []
        self.authenticated = authenticated
        self.fail_on = fail_on or {}
        self.member_id = member_id

    def __call__(self, cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        args = cmd[2:]  # strip "linkedin --json"
        sub = " ".join(args[:2])
        if sub == "auth status":
            if not self.authenticated:
                return self._done(cmd, 2, {"authenticated": False})
            return self._done(cmd, 0, {"authenticated": True, "li_at": "AQ…xx"})
        if sub == "profile get":
            return self._done(cmd, 0, {"public_id": args[2], "member_id": self.member_id})
        target = args[-1] if sub == "connections send" else args[-2]
        if target in self.fail_on:
            code, err = self.fail_on[target]
            return subprocess.CompletedProcess(cmd, code, "", f"error: {err}\n")
        if "--dry-run" in args:
            return self._done(cmd, 0, {"status": "dry-run", "recipient": target})
        return self._done(cmd, 0, {"status": "ok", "recipient": target})

    @staticmethod
    def _done(cmd: list[str], code: int, data: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, code, json.dumps(data) + "\n", "")

    def send_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[2:4] in (["messages", "send"], ["connections", "send"])]


def scripted(answers: Iterable[str]) -> Any:
    it = iter(answers)

    def _input(_prompt: str) -> str:
        try:
            return next(it)
        except StopIteration as exc:
            raise EOFError from exc

    return _input


@pytest.fixture(autouse=True)
def _no_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.fixture
def seeded(engine: Engine) -> Engine:
    with Session(engine) as s:
        posting = Posting(
            uid="manual:1",
            source="manual",
            external_id="1",
            url="https://example.com/job/1",
            canonical_url="https://example.com/job/1",
            title="Strategy Intern",
            company_name="Acme",
        )
        s.add(posting)
        s.commit()
        contact = Contact(
            posting_id=posting.id,
            name="Mario Rossi",
            profile_url="https://www.linkedin.com/in/mario-rossi/",
        )
        s.add(contact)
        s.commit()
        for i, (kind, recipient) in enumerate(
            [
                ("linkedin_message", "111"),
                ("followup", "222"),
                ("linkedin_connection_note", "https://www.linkedin.com/in/mario-rossi/"),
            ]
        ):
            s.add(
                Draft(
                    posting_id=posting.id,
                    contact_id=contact.id,
                    kind=kind,
                    channel="linkedin",
                    recipient=recipient,
                    text=f"Ciao Mario, testo numero {i}",
                    status="approved",
                )
            )
        s.add(Draft(kind="linkedin_message", recipient="333", text="draft", status="draft"))
        s.add(Draft(kind="email", channel="email", recipient="x@example.com", status="approved"))
        s.commit()
    return engine


def statuses(engine: Engine) -> list[str]:
    with Session(engine) as s:
        rows = s.exec(select(Draft).where(Draft.channel == "linkedin").order_by(Draft.id)).all()  # type: ignore[arg-type]
        return [d.status for d in rows]


def run(engine: Engine, answers: list[str], runner: FakeRunner, **kw: Any) -> dict[str, Any]:
    return send_approved(
        runner=runner,
        input_fn=scripted(answers),
        engine=engine,
        console=Console(width=100, force_terminal=False),
        clipboard_fn=lambda _t: False,
        **kw,
    )


def test_send_on_s_only(seeded: Engine) -> None:
    runner = FakeRunner()
    stats = run(seeded, ["s", "", "n"], runner)
    assert stats["sent"] == 1 and stats["deferred"] == 2
    sends = runner.send_calls()
    assert len(sends) == 1
    assert sends[0][2:] == ["messages", "send", "--", "111", "Ciao Mario, testo numero 0"]
    assert statuses(seeded) == ["sent", "approved", "approved", "draft"]
    with Session(seeded) as s:
        sent = s.exec(select(Draft).where(Draft.status == "sent")).one()
        assert sent.sent_at is not None and sent.send_result and "ok" in sent.send_result


def test_default_and_n_send_nothing(seeded: Engine) -> None:
    runner = FakeRunner()
    stats = run(seeded, ["", "n", "whatever"], runner)
    assert stats["sent"] == 0 and stats["deferred"] == 3
    assert runner.send_calls() == []
    assert statuses(seeded) == ["approved", "approved", "approved", "draft"]


def test_k_skips_permanently(seeded: Engine) -> None:
    runner = FakeRunner()
    stats = run(seeded, ["k", "n", "n"], runner)
    assert stats["skipped"] == 1
    assert runner.send_calls() == []
    assert statuses(seeded) == ["skipped", "approved", "approved", "draft"]


def test_q_stops(seeded: Engine) -> None:
    runner = FakeRunner()
    prompts: list[str] = []

    def _input(prompt: str) -> str:
        prompts.append(prompt)
        return "q"

    stats = send_approved(runner=runner, input_fn=_input, engine=seeded)
    assert stats["stopped"] == "user"
    assert len(prompts) == 1
    assert runner.send_calls() == []


def test_failure_recorded_and_loop_continues(seeded: Engine) -> None:
    runner = FakeRunner(fail_on={"111": (1, "HTTP 400: recipient not reachable")})
    stats = run(seeded, ["s", "s", "n"], runner)
    assert stats["failed"] == 1 and stats["sent"] == 1
    assert statuses(seeded) == ["failed", "sent", "approved", "draft"]
    with Session(seeded) as s:
        failed = s.exec(select(Draft).where(Draft.status == "failed")).one()
        assert failed.send_result == "HTTP 400: recipient not reachable"


def test_quota_stops_loop(seeded: Engine) -> None:
    err = "daily quota exceeded for messages: 25/25 — try again tomorrow"
    runner = FakeRunner(fail_on={"111": (1, err)})
    stats = run(seeded, ["s", "s", "s"], runner)
    assert stats["stopped"] == "quota"
    assert len(runner.send_calls()) == 1
    assert statuses(seeded) == ["approved", "approved", "approved", "draft"]


def test_refuses_in_ci(seeded: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    runner = FakeRunner()
    with pytest.raises(SystemExit) as exc:
        run(seeded, ["s"], runner)
    assert exc.value.code != 0
    assert runner.calls == []


def test_unauthenticated_stops(seeded: Engine) -> None:
    runner = FakeRunner(authenticated=False)
    stats = run(seeded, ["s", "s", "s"], runner)
    assert stats["stopped"] == "unauthenticated"
    assert runner.send_calls() == []
    assert statuses(seeded) == ["approved", "approved", "approved", "draft"]


def test_dry_run_keeps_statuses(seeded: Engine) -> None:
    runner = FakeRunner()
    stats = run(seeded, ["s", "s", "s"], runner, dry_run=True)
    assert stats["sent"] == 3 and stats["dry_run"] is True
    sends = runner.send_calls()
    assert len(sends) == 3
    assert all("--dry-run" in c for c in sends)
    assert statuses(seeded) == ["approved", "approved", "approved", "draft"]


def test_connection_note_never_in_command(seeded: Engine) -> None:
    runner = FakeRunner()
    run(seeded, ["n", "n", "s"], runner)
    sends = runner.send_calls()
    assert len(sends) == 1
    assert sends[0][2:] == ["connections", "send", "--", "mario-rossi"]
    assert all("testo numero 2" not in part for part in sends[0])
    assert statuses(seeded) == ["approved", "approved", "sent", "draft"]


def test_connection_copy_path_marks_manual(seeded: Engine) -> None:
    runner = FakeRunner()
    copied: list[str] = []

    def _copy(text: str) -> bool:
        copied.append(text)
        return True

    stats = send_approved(
        runner=runner,
        input_fn=scripted(["n", "n", "c", "s"]),
        engine=seeded,
        clipboard_fn=_copy,
    )
    assert stats["manual"] == 1
    assert copied == ["Ciao Mario, testo numero 2"]
    assert runner.send_calls() == []
    assert statuses(seeded) == ["approved", "approved", "sent", "draft"]


def test_public_id_resolved_before_message(seeded: Engine) -> None:
    with Session(seeded) as s:
        d = s.exec(select(Draft).where(Draft.recipient == "111")).one()
        d.recipient = "https://www.linkedin.com/in/mario-rossi/"
        s.add(d)
        s.commit()
    runner = FakeRunner(member_id="urn:li:member:42")
    run(seeded, ["s", "n", "n"], runner)
    assert runner.calls[1][2:] == ["profile", "get", "mario-rossi"]
    assert runner.send_calls()[0][2:5] == ["messages", "send", "--"]
    assert runner.send_calls()[0][5] == "urn:li:member:42"


def test_edit_updates_text(seeded: Engine) -> None:
    runner = FakeRunner()
    run(seeded, ["e", "s", "n", "n"], runner, edit_fn=lambda _t: "Testo nuovo")
    assert runner.send_calls()[0][-1] == "Testo nuovo"
    with Session(seeded) as s:
        assert s.exec(select(Draft).where(Draft.recipient == "111")).one().text == "Testo nuovo"
