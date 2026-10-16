# Running Radar

Radar runs **locally on the user's Mac**. There is no cloud deployment: GitHub only hosts the
code and runs CI.

| Where | What runs | What never runs there |
|---|---|---|
| The user's Mac | daily `radar run` (launchd), web app + API (`make dev-real`), `radar send` | - |
| GitHub Actions | CI only: ruff, ruff format, mypy, pytest (fake LLM), frontend lint / typecheck / test / build, gitleaks | any real data, secrets, `radar run`, `radar send`, linkedin-cli, cookies |

## First setup

Requires Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20 + pnpm.

```bash
make install                                                        # uv, pnpm, pre-commit
for f in config/*.example.yaml; do cp "$f" "${f/.example/}"; done   # profile, companies, rules
cp .env.example .env                                                # set GROQ_API_KEY
make seed            # optional: import private/radar_seed.json into data/radar.db
make run-pipeline    # one run by hand
make dev-real        # web app on data/radar.db: http://localhost:5173
```

All real files (`config/*.yaml`, `.env`, `data/`, `logs/`, `private/`) are gitignored, and the
pre-commit personal-data guard blocks them. `make dev` instead runs the demo (`data/demo.db`,
fictional data, fake LLM, no keys).

## Daily run (launchd)

```bash
make schedule-install   # install the launchd job (every day at 07:00)
make schedule-status    # installed? state, last exit code, last log lines
make schedule-run-now   # trigger it now
make schedule-remove    # uninstall
```

- `scripts/schedule.sh install` writes `~/Library/LaunchAgents/io.github.speculor.daily.plist`
  (a user LaunchAgent, `StartCalendarInterval` 07:00 local time) and loads it with
  `launchctl bootstrap`. It runs `scripts/daily.sh` from the repository directory.
- If the Mac is **asleep** at 07:00, launchd runs the missed job when it wakes. If the Mac was
  **shut down**, that day's run is skipped; `make schedule-run-now` or `make run-pipeline` runs
  it by hand.
- `scripts/daily.sh` takes a lock (`logs/.daily.lock`), so only one run happens at a time (a
  missed run fired on wake never overlaps a running one), and runs `uv run --frozen radar run`.
- Output is appended to `logs/daily.log`: counts only, never company names, titles or email
  content. The log is rotated to `logs/daily.log.1` at about 2 MB. launchd's own output goes to
  `logs/launchd.log`.
- The run report (Markdown, private) is written to `data/reports/` and shown in the web app
  under *Esecuzioni*; `make report` prints the latest one.

### What a run does

`radar run` collects from every enabled source, normalizes and dedupes, checks closed postings,
applies the hard filters, computes the daily diff, scores new postings (deterministic
relevance check first, then the LLM), builds packs and drafts for the best ones and records the
run with counts and LLM usage. A failing source makes the run `partial`, never aborts it. When
the free-tier daily LLM limit is reached, the remaining postings stay unscored and the next run
picks them up. See [ARCHITECTURE.md](ARCHITECTURE.md).

## Reviewing and sending

```bash
make dev-real    # Oggi, Annunci, Pacchetto, Approvazioni, ... on data/radar.db
make send        # approved LinkedIn drafts, one by one, each after an explicit "s"
make send-dry    # the same with linkedin-cli --dry-run; nothing changes
```

`radar send` runs only on the user's Mac, through the user's own linkedin-cli session, and
refuses to run when `CI` or `GITHUB_ACTIONS` is set. Applications on company sites are always
submitted by the user by hand.

## CI (`.github/workflows/ci.yml`)

On every push to `main` and every pull request: backend (ruff, ruff format, mypy strict, pytest
with the `fake` LLM and a coverage summary), frontend (eslint, tsc, vitest, build) and a gitleaks
scan. The workflow has read-only permissions, uses no secrets and never touches real data.

## Self-hosting on Postgres (optional)

> Optional and **not tested in production**. SQLite (`data/radar.db`) is the default and the
> setup Radar is used with. Use this only if you want the database on a Postgres server, for
> example a Supabase project.

- **Connect**: set `RADAR_DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME` in `.env`
  (`postgres://` and `postgresql+psycopg://` also work). The psycopg driver is already a
  dependency. A transaction pooler URL (port `6543`, e.g. Supabase's) is detected and handled:
  no client-side pool and no prepared statements.
- **Create the tables**: `uv run radar init-db`.
- **Copy existing data**: `uv run radar db copy --source sqlite:///data/radar.db --target
  postgresql://...` copies every table in foreign-key order, keeping ids, and resets the Postgres
  sequences. It refuses a non-empty target unless `--replace` is given, and prints row counts
  only.
- **Lock down the tables**: `uv run radar db secure` enables row level security on every table of
  `RADAR_DATABASE_URL`. On Supabase this blocks the public REST API from reading the tables with
  the anon key; Radar itself connects as the database owner and is unaffected.
- **Optional API auth**: with `RADAR_AUTH_REQUIRED=true`, every route except `/api/health` needs
  `Authorization: Bearer <Supabase access token>` whose email equals `RADAR_ALLOWED_EMAIL`.
  Tokens are verified with the project's JWKS (ES256 / RS256, from `SUPABASE_URL`) or, for
  legacy HS256 tokens, with `SUPABASE_JWT_SECRET`. The bundled web app has no login screen, so
  this is for a custom client or an API exposed beyond `localhost`.

The daily launchd job, `radar send` and the web app all follow `RADAR_DATABASE_URL`, so they use
the same database once it is set.
