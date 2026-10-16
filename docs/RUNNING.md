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

## Email alerts (IMAP)

Optional. Radar reads LinkedIn and JobTeaser alert emails from **one folder/label named `radar`**
in your mailbox, read-only (`EXAMINE`, `BODY.PEEK`: nothing is marked as read, moved, flagged or
deleted; the IMAP client refuses those commands). Only the From header of other messages in the
folder is looked at; their content is never downloaded, logged or sent to the LLM. Secrets live
in the macOS keychain (or, for an app password, optionally in `.env`), never in the repo.

| Mailbox | Auth (`RADAR_IMAP_AUTH=auto` picks it from the address) | Host (default from the address) |
|---|---|---|
| Outlook.com / Hotmail / Live / MSN (personal Microsoft account) | `oauth2`: your own Entra app + `radar imap login` | `outlook.office365.com` |
| Gmail | `password`: a Google app password | `imap.gmail.com` |

Why OAuth2 for Outlook: Microsoft turned off basic authentication (password / app password) for
Outlook.com on 16 September 2024; IMAP now only accepts OAuth2 (`AUTHENTICATE XOAUTH2`) with the
scope `https://outlook.office.com/IMAP.AccessAsUser.All` (+ `offline_access`, added by MSAL, for
the refresh token). Radar uses the device-code flow with a public client: no client secret, no
redirect server.

### Outlook.com (one-off, about 15 minutes)

1. **Get a Microsoft Entra tenant.** Apps can no longer be registered "outside a directory" with
   a bare personal account. Sign in to <https://portal.azure.com> with your Outlook address and
   create the free Azure account when asked (identity check with phone and card; app
   registrations are free). This creates a tenant called *Default Directory*.
2. **Register the app** at <https://entra.microsoft.com> (top-right settings icon: switch to
   *Default Directory* if needed) → **Entra ID → App registrations → New registration**:
   - Name: `Radar IMAP` (anything).
   - Supported account types: **Personal accounts only** (or *Any Entra ID Tenant + Personal
     Microsoft accounts*; either works with tenant `consumers`).
   - Redirect URI: leave empty (the device-code flow needs none).
   - **Register**, then copy the **Application (client) ID** from the Overview page.
3. **Allow the device-code flow**: in the app, **Authentication** → *Advanced settings* →
   **Allow public client flows: Yes** → **Save**. (Without it the login fails with
   `AADSTS7000218`.) Do not create a client secret or certificate.
4. **API permissions** (optional, documents what the app asks for) → *Add a permission* →
   **Microsoft Graph → Delegated permissions** → tick `IMAP.AccessAsUser.All` and
   `offline_access` → *Add permissions*. No admin consent is needed: you consent yourself at login.
5. **Enable IMAP in Outlook.com**: <https://outlook.live.com> → Settings → Mail →
   **Forwarding and IMAP** → *Let devices and apps use IMAP*: **On** → Save.
6. **Folder + rule**: create a folder named `radar` (Folders → *Create new folder*). Settings →
   Mail → **Rules** → *Add new rule*: condition **From** = `jobalerts-noreply@linkedin.com`,
   `jobs-noreply@linkedin.com`, `jobs-listings@linkedin.com` and the From address of a JobTeaser
   alert you received (open one and copy it; any `@jobteaser.com` / `@<x>.jobteaser.com` address
   is accepted); action **Move to → radar**; tick *Stop processing more rules*; Save, then
   *Run rule* once to file the existing alerts.
7. **`.env`** (the client id is not a secret):

   ```bash
   RADAR_IMAP_USER=<your Outlook address>
   RADAR_IMAP_OAUTH_CLIENT_ID=<Application (client) ID>
   ```

8. **Log in once**: `uv run radar imap login` prints a Microsoft URL
   (<https://microsoft.com/devicelogin>) and a code; open it, enter the code, sign in, accept the
   consent ("access your mailboxes via IMAP"; Radar only reads, enforced in code). The MSAL token
   cache (refresh token) goes to the macOS keychain, item `speculor-radar`.
9. **Check**: `uv run radar imap check` → `OK: ... N alert LinkedIn, M JobTeaser` (counts only).

Each run refreshes the access token silently. If that stops working (token revoked, password
changed, a long time without runs) the `email` source pauses with *token scaduto → Esegui
`radar imap login`*: run it and the source resumes.

### Gmail (5 minutes)

1. Turn on **2-Step Verification** for the Google account (required for app passwords).
2. <https://myaccount.google.com/apppasswords> → create one named `Radar` → copy the 16 characters.
   (IMAP is always on for personal Gmail accounts since January 2025.)
3. **Label + filter**: Gmail search box → *Show search options* → From:
   `jobalerts-noreply@linkedin.com OR jobs-noreply@linkedin.com OR jobs-listings@linkedin.com OR
   jobteaser.com` → *Create filter* → **Apply the label** `radar` (new label) → also tick *Also
   apply filter to matching conversations* → Create filter.
4. **`.env`**: `RADAR_IMAP_USER=<your Gmail address>`; then store the app password in the
   keychain with `uv run radar imap set-password` (or put it in `RADAR_IMAP_PASSWORD`).
5. **Check**: `uv run radar imap check`.

A rejected password pauses the source (*login IMAP rifiutato*); `radar imap set-password` with a
new app password resumes it.

### Commands and settings

- `radar imap status`: mode, host, folder and which secrets exist (never their values).
- `radar imap login` / `set-password` / `logout` (deletes the keychain items) / `check`.
- Optional settings: `RADAR_IMAP_AUTH` (`auto` | `password` | `oauth2`), `RADAR_IMAP_HOST`,
  `RADAR_IMAP_PORT` (993), `RADAR_IMAP_FOLDER` (`radar`), `RADAR_IMAP_SINCE_DAYS` (14),
  `RADAR_IMAP_OAUTH_TENANT` (`consumers`; use `organizations` or a tenant id for a work account).
- Turn the source off with `email: {enabled: false}` in `config/companies.yaml`.

### Real alert samples (for parser fixtures)

```bash
make email-samples        # latest 3 LinkedIn + 3 JobTeaser alerts, anonymized,
                          # into private/email_samples/ (gitignored; counts printed only)
# read every file yourself; fix or delete anything personal, then promote one at a time:
make promote-email-sample FILE=private/email_samples/real_linkedin_1.eml
```

`promote-email-sample` runs the personal-data checks (`scripts/check_private.py`, also on the
decoded body), requires the anonymized recipient, and copies the file to `tests/fixtures/email/`.
It never commits: look at `git diff` and commit yourself.

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
