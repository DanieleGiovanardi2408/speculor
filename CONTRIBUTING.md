# Contributing

Thanks for your interest. Radar is a personal tool first, so keep changes small and focused.

## Setup

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20 with pnpm 9,
[gitleaks](https://github.com/gitleaks/gitleaks) (`brew install gitleaks`).

```bash
make install   # uv sync + pnpm install + pre-commit install
make dev       # demo mode: fictional data, fake LLM, no API keys
```

## Make targets

| Target | What it does |
|---|---|
| `make dev` | Demo DB + fake LLM, API on :8000 and web app on :5173 |
| `make dev-real` | Same on your real `data/radar.db` (local only) |
| `make test` | pytest + frontend tests |
| `make lint` | ruff check, ruff format --check, mypy (strict), eslint, tsc |
| `make fmt` | ruff format + autofix |
| `make run-pipeline` | Run the daily pipeline locally |
| `make schedule-install` / `make schedule-status` / `make schedule-run-now` / `make schedule-remove` | Manage the local launchd daily run (07:00) |
| `make send` / `make send-dry` | Send approved LinkedIn drafts, locally, one by one |
| `make build` | Production build of the frontend |

Run `make help` for the full list.

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`, `chore:`, `ci:` (optional scope, e.g. `feat(connectors): ...`).
- `make lint` and `make test` must pass. New behaviour needs tests; connectors need fixtures
  (recorded responses with fictional data), never live calls in tests.
- Update `CHANGELOG.md` under *Unreleased*.

## Privacy rules (non-negotiable)

- Never commit personal data: CVs, real profile/companies/rules configs, the database, the
  seed export, reports, `.env`, names, emails or phone numbers of real people.
  The real files are gitignored; commit only `*.example.yaml` with fictional content.
- Tests, fixtures, screenshots and docs use fictional data only ("the user", "Acme").
- The pre-commit hooks enforce this: **gitleaks** (secrets), **check-private**
  (`scripts/check_private.py`: blocks `private/`, `data/`, `*.db`, real config files,
  `radar_seed.json`, PDFs, `.env`, personal-looking emails, phone-number-like strings and any
  string listed in your local, gitignored `private/forbidden_strings.txt`), plus ruff and basic
  hygiene checks. Do not bypass them with `--no-verify` on `main`.
- Never add code that sends messages or submits applications automatically, runs LinkedIn
  actions in CI, stores LinkedIn cookies outside the user's machine, ignores robots.txt, or
  evades anti-bot measures (CAPTCHAs, fingerprinting, proxies rotation).
- CI runs with no secrets and no real data; never add a workflow that runs the pipeline or
  touches personal data on GitHub. The daily run stays local (launchd).
