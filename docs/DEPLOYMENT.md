# Deployment

Radar runs in three places, each with a clear boundary:

| Where | What runs | What never runs there |
|---|---|---|
| Your computer | web app + API (`make dev-real`), `radar send`, `make bootstrap-data` | - |
| GitHub Actions | CI; daily `radar run` (public sources + LLM) | `radar send`, linkedin-cli, LinkedIn cookies |
| Supabase + Vercel (planned) | Postgres DB, API, static web app | `radar send`, linkedin-cli, LinkedIn cookies |

## Local mode

```bash
make install
make dev          # demo: data/demo.db + fake LLM, no keys needed
make dev-real     # your data: data/radar.db, keys from .env
make run-pipeline # one pipeline run on data/radar.db
make send         # send approved LinkedIn drafts, one by one, with confirmation
```

Copy `.env.example` to `.env` and `config/*.example.yaml` to `config/*.yaml` (all gitignored).

## GitHub Actions

### CI (`.github/workflows/ci.yml`)

On every push to `main` and every pull request: backend (ruff, ruff format, mypy strict,
pytest with coverage summary), frontend (eslint, tsc, vitest, build) and a gitleaks scan.

### Daily run (`.github/workflows/daily.yml`)

Runs at 07:00 Europe/Amsterdam (two UTC crons, one gated by the current DST offset) and on
manual dispatch. Steps: check out `main`, check out the `data` branch into `.data-branch/`,
restore `radar.db`, `radar init-db`, `radar run`, then commit `radar.db` and
`reports/*.md` back to `data` as `github-actions[bot]`. The job log contains counts only.

Secrets and variables (Settings -> Secrets and variables -> Actions):

| Name | Kind | Required | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | secret | yes (with groq) | LLM scoring and drafts |
| `ANTHROPIC_API_KEY` | secret | no | only if a provider is `anthropic` |
| `RADAR_LLM_PROVIDER` | variable | no (default `groq`) | `groq`, `anthropic` or `fake` |
| `RADAR_DRAFT_PROVIDER` | variable | no | provider for packs/drafts if different |

The workflow needs `contents: write` (already declared) to push to the `data` branch. No
LinkedIn cookie, session or linkedin-cli is ever configured in Actions.

### The `data` branch

Personal data (the database, which also stores the gitignored config files in its `settings`
table, and the daily reports) lives on an **orphan branch named `data`**, never on `main`:

- `main`'s history stays free of personal data, so the repository can later be made public
  after simply **deleting the `data` branch** (no history rewriting needed);
- the daily job has durable storage without any external service;
- the branch is orphan (no shared history with `main`), so it can never be merged by accident.

Bootstrap it once from your computer:

```bash
make seed            # or otherwise create data/radar.db
make bootstrap-data  # radar config push + push data/radar.db to the `data` branch
```

`make bootstrap-data` works in a temporary git worktree and only pushes `refs/heads/data`.
To pull the latest cloud state locally:
`git fetch origin data && git show origin/data:radar.db > data/radar.db`.

While the repository is private, anyone with read access can read the `data` branch: keep it
private, and delete the branch (and any forks) before making it public.

## Supabase + Vercel (planned, next session)

Status: **planned**, not implemented yet. Steps:

1. **Create a Supabase project** (EU region). Copy the Postgres connection string (pooler,
   port 6543 for serverless) and the JWT secret.
2. **Set `RADAR_DATABASE_URL`** to the Postgres URL (locally in `.env`, in Vercel, and as an
   Actions secret replacing the `data` branch DB). Add a Postgres driver dependency, run
   `radar init-db`, migrate existing data from SQLite (`radar config push` for the config).
3. **Enable email auth with an allow-list**: Supabase Auth -> email (magic link) only, disable
   sign-ups, invite the single allowed user. Set `RADAR_AUTH_REQUIRED=true`,
   `RADAR_ALLOWED_EMAIL`, `SUPABASE_JWT_SECRET` on the API.
4. **Vercel project**: Python serverless functions for the FastAPI app (`api/` entry importing
   `radar.api.main:app`) + the static frontend build (`frontend/dist`) with `/api/*` rewrites.
5. **Environment variables** in Vercel: `RADAR_DATABASE_URL`, `RADAR_AUTH_REQUIRED`,
   `RADAR_ALLOWED_EMAIL`, `SUPABASE_JWT_SECRET`, `RADAR_CORS_ORIGINS`, LLM keys if the API
   generates drafts; frontend build vars `VITE_API_BASE`, `VITE_SUPABASE_URL`,
   `VITE_SUPABASE_ANON_KEY`.
6. Point the daily workflow at Supabase (`RADAR_DATABASE_URL` secret) and retire the `data`
   branch.

`radar send` keeps running only on the user's computer, pointed at the same database via
`RADAR_DATABASE_URL`.
