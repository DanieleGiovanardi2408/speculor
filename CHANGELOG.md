# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Daily pipeline: collection from Greenhouse, Lever, Ashby, SmartRecruiters and LinkedIn public
  guest search; normalization and de-duplication; closed-posting detection; daily diff via
  change events.
- Deterministic hard filters driven by `config/rules.yaml`.
- LLM scoring (Groq by default, pluggable providers, offline `fake` provider for demo/tests).
- Application packs (cover letter, form answers) and outreach drafts.
- FastAPI backend and React web app to review postings, packs and approve drafts.
- `radar send`: local-only, one-by-one sending of approved LinkedIn drafts through
  linkedin-cli, each after explicit confirmation; refuses to run in CI.
- Makefile, local demo mode without API keys, CI (backend, frontend, gitleaks), daily
  GitHub Actions run storing data on an orphan `data` branch.
- Pre-commit hooks: gitleaks, ruff, hygiene checks and a personal-data guard.
