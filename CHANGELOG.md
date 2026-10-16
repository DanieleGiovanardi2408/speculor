# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Daily pipeline: collection from Greenhouse, Lever, Ashby and Workday (public CXS endpoint, best
  effort, robots.txt checked); optional read-only IMAP email alerts (one Gmail label, LinkedIn and
  JobTeaser senders only); manual add by URL; normalization and de-duplication; closed-posting
  detection; daily diff via change events. SmartRecruiters and LinkedIn guest connectors exist
  but fetch nothing (their robots.txt disallows generic agents).
- Deterministic hard filters driven by `config/rules.yaml`, with the rule and triggering text
  stored for every hit and one-click restore with undo.
- Post-graduation track: graduate programmes and full-time roles starting after graduation are
  flagged `post_graduation` and shown in a separate *Dopo la laurea* view (API `track` param).
- LLM scoring (Groq by default, Anthropic as a one-line switch, offline `fake` provider for
  demo/tests); deterministic "Bassa" for off-target titles without an LLM call, target roles
  first, cache by content hash, resume after free-tier daily limits.
- Application packs (cover letter, form answers) and outreach drafts, with writing rules
  enforced in code.
- FastAPI backend and Italian React web app (*Oggi*, *Annunci*, *Pacchetto*, *Approvazioni*,
  *Candidature*, *Aziende*, *Impostazioni*, *Esecuzioni*), keyboard shortcuts, axe-clean at
  WCAG 2.1 AA.
- `radar send`: local-only, one-by-one sending of approved LinkedIn drafts through
  linkedin-cli, each after explicit confirmation; refuses to run in CI.
- Daily run on the user's Mac with launchd at 07:00 (`make schedule-install`,
  `schedule-status`, `schedule-run-now`, `schedule-remove`), one run at a time, counts-only log.
- Optional Postgres support: `radar db copy`, `radar db secure` (row level security), transaction
  pooler handling, optional Supabase JWT auth for the API.
- Makefile, local demo mode without API keys, CI (backend, frontend, gitleaks).
- Pre-commit hooks: gitleaks, ruff, hygiene checks and a personal-data guard.

- Contact search ("Cerca contatti", `radar contacts find --posting-id ID | --alta [--drafts]`,
  `POST /api/postings/{id}/contacts/find`, daily for up to 5 Alta postings with a pack): 2-3
  public profiles per posting (Luiss alumni, similar role, likely hiring manager) via Groq's
  built-in browser search. Contacts are kept only when grounded in the executed search results
  (URL checked, slug must match the name), stored as "da verificare" with the source query.
  Estimated cost per call in `llm_usage.est_cost_eur`; hard monthly cap
  (`RADAR_CONTACTS_MONTHLY_CAP_EUR`, default 5) and daily call cap. Never logs into LinkedIn,
  never runs linkedin-cli, never sends anything.

### Removed

- GitHub Actions daily cron and data branch (runs locally with launchd).
